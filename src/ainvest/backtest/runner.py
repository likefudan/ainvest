"""Point-in-time decisions via existing isolated strategy/sizing/risk libraries."""

import time
from decimal import ROUND_HALF_EVEN, localcontext
from importlib.metadata import version
from threading import Lock

from ainvest.backtest.models import (
    ReplayConfig,
    ReplayMarketData,
    ReplayPoint,
    ReplayResult,
    ReplayStep,
)
from ainvest.data.calendar import ExchangeCalendar
from ainvest.data.calendar_port import FakeMarketCalendar, MarketCalendar
from ainvest.data.indicators import IndicatorRun, compute_indicators, digest_bytes
from ainvest.data.models import OhlcvPage
from ainvest.data.ports import DataIncompleteError
from ainvest.data.quality import BarExpectation
from ainvest.portfolio.sizer import size_position
from ainvest.risk.engine import evaluate_risk
from ainvest.risk.models import EvaluationPhase, RiskContext
from ainvest.risk.rules import PRETRADE_RULE_CODES
from ainvest.schemas.common import Provenance
from ainvest.schemas.market import MarketQuote, ResearchMarketSection
from ainvest.schemas.research import EvidenceCitation, EvidenceKind, ResearchPacket
from ainvest.schemas.strategy import StrategyContext, StrategyState, parse_trade_signal_for_context
from ainvest.strategies.definitions import StrategyDefinition, StrategyResult
from ainvest.strategies.worker.codes import WorkerStatus
from ainvest.strategies.worker.digests import digest_json
from ainvest.strategies.worker.runner import evaluate_in_worker, strategy_ref_from_definition

_REPLAY_LOCK = Lock()


def _calendar_digest(calendar: MarketCalendar) -> str:
    # Restrict this offline adapter to the two existing local calendar readers;
    # arbitrary caller callbacks are not an unrecorded provider/network channel.
    if type(calendar) is FakeMarketCalendar:
        return digest_json(
            {
                "kind": "fake-calendar-v1",
                "holidays": sorted(day.isoformat() for day in calendar.holidays),
                "early_close_dates": sorted(day.isoformat() for day in calendar.early_close_dates),
                "regular_open": calendar.regular_open.isoformat(),
                "regular_close": calendar.regular_close.isoformat(),
                "early_close_time": calendar.early_close_time.isoformat(),
                "supported_exchanges": sorted(calendar.supported_exchanges),
            }
        )
    if type(calendar) is ExchangeCalendar:
        return digest_json(
            {
                "kind": "exchange-calendar-v1",
                "valid_from": calendar.valid_from.isoformat(),
                "valid_through": calendar.valid_through.isoformat(),
                "library_version": version("pandas_market_calendars"),
            }
        )
    raise ReplayError("REPLAY_CALENDAR_UNSUPPORTED")


class ReplayError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _visible_context(
    data: ReplayMarketData,
    point: ReplayPoint,
    config: ReplayConfig,
    state: StrategyState | None,
) -> tuple[StrategyContext, MarketQuote, str, IndicatorRun]:
    cutoff = point.as_of
    if data.instrument.identity_as_of > cutoff:
        raise ReplayError("REPLAY_FUTURE_IDENTITY")
    if (
        point.portfolio.provenance.quality_flags
        or point.portfolio.provenance.is_delayed
        or (cutoff - point.portfolio.provenance.observed_at).total_seconds()
        > config.max_age_seconds
    ):
        raise ReplayError("REPLAY_PORTFOLIO_QUALITY")
    eligible = tuple(
        quote
        for quote in data.quotes
        if quote.provenance.observed_at <= cutoff and quote.provenance.received_at <= cutoff
    )
    if not eligible:
        raise ReplayError("REPLAY_QUOTE_UNAVAILABLE")
    quote = max(
        eligible, key=lambda value: (value.provenance.observed_at, value.provenance.received_at)
    )
    if (
        quote.provenance.quality_flags
        or quote.provenance.is_delayed
        or quote.bid is None
        or quote.ask is None
        or (cutoff - quote.provenance.observed_at).total_seconds() > config.max_age_seconds
    ):
        raise ReplayError("REPLAY_QUOTE_QUALITY")
    bars = tuple(
        item.bar
        for item in data.bars
        if item.closed_at <= cutoff
        and item.bar.provenance.observed_at <= cutoff
        and item.bar.provenance.received_at <= cutoff
    )
    if not bars:
        raise ReplayError("REPLAY_BARS_UNAVAILABLE")
    # This envelope describes only the retained normalized prefix; it does not
    # claim to be an original provider wire capture or replace source timestamps.
    flags = {flag for bar in bars for flag in bar.provenance.quality_flags}
    provenance = Provenance(
        source=bars[0].provenance.source,
        observed_at=max(bar.provenance.observed_at for bar in bars),
        received_at=max(bar.provenance.received_at for bar in bars),
        is_delayed=any(bar.provenance.is_delayed for bar in bars),
        quality_flags=tuple(sorted(flags)),
    )
    page = OhlcvPage(
        instrument_id=data.instrument.instrument_id,
        interval=data.timeframe,
        adjustment=data.adjustment,
        items=bars,
        provenance=provenance,
    )
    expected = BarExpectation(
        instrument=data.instrument,
        provider=provenance.source,
        timeframe=data.timeframe,
        adjustment=data.adjustment,
        as_of=cutoff,
        max_age_seconds=config.max_age_seconds,
        expected_starts=point.expected_starts,
    )
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = 28
            decimal_context.rounding = ROUND_HALF_EVEN
            calculation = compute_indicators(page, expected)
    except DataIncompleteError:
        raise ReplayError("REPLAY_BAR_QUALITY") from None
    if calculation.technical.provenance.quality_flags:
        raise ReplayError("REPLAY_BAR_QUALITY")
    visible_digest = digest_json(
        {
            "quote": quote.model_dump(mode="json"),
            "bars": page.model_dump(mode="json"),
            "expected": expected.model_dump(mode="json"),
        }
    )
    research_id = "replay_res_" + visible_digest.split(":")[1]
    packet = ResearchPacket(
        research_id=research_id,
        symbol=data.instrument.symbol,
        instrument=data.instrument,
        as_of=cutoff,
        market=ResearchMarketSection(
            last_price=quote.last_price,
            bid=quote.bid,
            ask=quote.ask,
            currency=quote.currency,
            observed_at=quote.provenance.observed_at,
            provenance=quote.provenance,
        ),
        technical=calculation.technical,
        evidence=(
            EvidenceCitation(
                evidence_id="replay_quote_"
                + digest_bytes(quote.model_dump_json().encode()).split(":")[1],
                kind=EvidenceKind.QUOTE,
                summary="Historical normalized quote",
                provenance=quote.provenance,
                locator="quote:replay/" + visible_digest.split(":")[1],
            ),
            EvidenceCitation(
                evidence_id="replay_ta_" + calculation.input_digest.split(":")[1],
                kind=EvidenceKind.TECHNICAL,
                summary="Point-in-time deterministic indicators",
                provenance=calculation.technical.provenance,
                locator="technical:replay/" + calculation.input_digest.split(":")[1],
            ),
        ),
    )
    context = StrategyContext(
        as_of=cutoff, research=packet, portfolio=point.portfolio, strategy_state=state
    )
    return context, quote, visible_digest, calculation


def _run_replay(
    data: ReplayMarketData,
    points: tuple[ReplayPoint, ...],
    config: ReplayConfig,
    *,
    definition: StrategyDefinition,
    calendar: MarketCalendar,
) -> ReplayResult:
    """Bounded dry decision trace, not simulated fills/returns or order approval.

    Portfolio snapshots/policy availability are trusted historical inputs.
    No Strategy receives the raw dataset, future schedule, callable or broker.
    """
    try:
        if not 1 <= len(points) <= 64:
            raise ValueError("schedule size")
        if (
            len(data.model_dump_json().encode()) > 2_097_152
            or len(config.model_dump_json().encode()) > 65_536
        ):
            raise ValueError("input size")
        if sum(len(point.model_dump_json().encode()) for point in points) > 2_097_152:
            raise ValueError("schedule size")
        data = ReplayMarketData.model_validate_json(data.model_dump_json())
        config = ReplayConfig.model_validate_json(config.model_dump_json())
        points = tuple(ReplayPoint.model_validate_json(point.model_dump_json()) for point in points)
        clocks = tuple(point.as_of for point in points)
        if tuple(sorted(set(clocks))) != clocks:
            raise ValueError("cutoffs must be unique and ordered")
        definition.metadata.validate()
        calendar_digest = _calendar_digest(calendar)
        definition.validate_params(config.params)
        parameter_digest = digest_json(
            strategy_ref_from_definition(definition, config.params).params
        )
        if config.initial_state is not None and (
            config.initial_state.strategy != definition.name
            or config.initial_state.strategy_version != definition.version
            or config.initial_state.updated_at > points[0].as_of
        ):
            raise ValueError("initial strategy state differs or is future")
    except Exception:
        raise ReplayError("REPLAY_INPUT_INVALID") from None
    steps: list[ReplayStep] = []
    retained_bytes = 0
    state = config.initial_state
    deadline = time.monotonic() + config.duration_seconds
    error = None
    for index, point in enumerate(points):
        try:
            if time.monotonic() >= deadline:
                raise ReplayError("REPLAY_TIMEOUT")
            context, quote, visible_digest, calculation = _visible_context(
                data, point, config, state
            )
            worker = evaluate_in_worker(
                definition,
                params=config.params,
                context=context,
                limits=config.worker_limits.model_copy(
                    update={
                        "wall_timeout_seconds": min(
                            config.worker_limits.wall_timeout_seconds,
                            max(0.001, deadline - time.monotonic()),
                        ),
                    }
                ),
                run_id="replay_worker_"
                + digest_bytes((config.run_id + str(index)).encode()).split(":")[1],
            )
            if time.monotonic() >= deadline:
                raise ReplayError("REPLAY_TIMEOUT")
            if worker.status != WorkerStatus.SUCCESS or worker.result is None:
                raise ReplayError("REPLAY_WORKER_FAILED")
            if (
                worker.input_digest != digest_json(context.model_dump(mode="json"))
                or worker.parameter_digest != parameter_digest
                or worker.strategy_name != definition.name
                or worker.strategy_version != definition.version
                or worker.plugin_id != definition.metadata.plugin_id
                or worker.plugin_version != definition.metadata.plugin_version
                or worker.source_commit != definition.metadata.source_commit
            ):
                raise ReplayError("REPLAY_WORKER_BINDING")
            result = StrategyResult.model_validate_json(worker.result.model_dump_json())
            if len(result.signals) > 1:
                raise ReplayError("REPLAY_MULTIPLE_SIGNALS")
            if result.next_state is not None and (
                result.next_state.updated_at > point.as_of
                or result.next_state.strategy != definition.name
                or result.next_state.strategy_version != definition.version
            ):
                raise ReplayError("REPLAY_STATE_INVALID")
            sizing = None
            risk = None
            if result.signals:
                signal = parse_trade_signal_for_context(
                    result.signals[0].model_dump(mode="json"), context
                )
                if (
                    signal.strategy != definition.name
                    or signal.strategy_version != definition.version
                ):
                    raise ReplayError("REPLAY_SIGNAL_INVALID")
                seed = digest_json(
                    {
                        "run_id": config.run_id,
                        "context": context.model_dump(mode="json"),
                        "config": config.model_dump(mode="json"),
                        "parameters": worker.parameter_digest,
                    }
                ).split(":")[1]
                with localcontext() as decimal_context:
                    decimal_context.prec = 28
                    decimal_context.rounding = ROUND_HALF_EVEN
                    sizing = size_position(
                        signal=signal,
                        quote=quote,
                        portfolio=point.portfolio,
                        config=config.sizing,
                        as_of=point.as_of,
                        candidate_id="cand_replay_" + seed,
                    )
                    if sizing.candidate is not None:
                        risk = evaluate_risk(
                            RiskContext(
                                risk_decision_id="risk_replay_" + seed,
                                phase=EvaluationPhase.PROPOSAL,
                                as_of=point.as_of,
                                candidate=sizing.candidate,
                                quote=quote,
                                instrument=point.instrument_metadata,
                                config=config.risk,
                                portfolio=point.portfolio,
                                short_term_volatility_bps=point.short_term_volatility_bps,
                                exposure_inputs=point.exposure_inputs,
                                kill_switch=point.kill_switch,
                                recent_submissions=point.recent_submissions,
                                client_order_id="replay_" + seed,
                            ),
                            calendar=calendar,
                            rule_codes=PRETRADE_RULE_CODES,
                        )
            step = ReplayStep(
                context=context,
                visible_input_digest=visible_digest,
                context_digest=digest_json(context.model_dump(mode="json")),
                worker_input_digest=worker.input_digest,
                parameter_digest=worker.parameter_digest,
                calculation=calculation,
                strategy_result=result,
                sizing=sizing,
                risk=risk,
            )
            if worker.input_digest != step.context_digest:
                raise ReplayError("REPLAY_WORKER_BINDING")
            step_bytes = len(step.model_dump_json().encode())
            if time.monotonic() >= deadline:
                raise ReplayError("REPLAY_TIMEOUT")
            if step_bytes > 262_144 or retained_bytes + step_bytes > 2_097_152:
                raise ReplayError("REPLAY_RESULT_LIMIT")
            steps.append(step)
            retained_bytes += step_bytes
            state = result.next_state if result.next_state is not None else state
        except ReplayError as exc:
            error = exc.code
            break
        except Exception:
            error = "REPLAY_STEP_INVALID"
            break
    semantic = digest_json([step.model_dump(mode="json") for step in steps])
    return ReplayResult(
        run_id=config.run_id,
        status="error" if error else "complete",
        error_code=error,
        code_version=config.code_version,
        config_version=config.config_version,
        strategy_name=definition.name,
        strategy_version=definition.version,
        plugin_version=definition.metadata.plugin_version,
        source_commit=definition.metadata.source_commit,
        dataset_digest=digest_json(data.model_dump(mode="json")),
        schedule_digest=digest_json([point.model_dump(mode="json") for point in points]),
        config_digest=digest_json(config.model_dump(mode="json")),
        calendar_digest=calendar_digest,
        steps=tuple(steps),
        semantic_digest=semantic,
    )


def run_replay(
    data: ReplayMarketData,
    points: tuple[ReplayPoint, ...],
    config: ReplayConfig,
    *,
    definition: StrategyDefinition,
    calendar: MarketCalendar,
) -> ReplayResult:
    """Run one bounded dry replay at a time; reject concurrent runs without queueing."""
    if not _REPLAY_LOCK.acquire(blocking=False):
        raise ReplayError("REPLAY_RUN_BUSY")
    try:
        return _run_replay(data, points, config, definition=definition, calendar=calendar)
    finally:
        _REPLAY_LOCK.release()
