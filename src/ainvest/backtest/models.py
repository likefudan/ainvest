"""Bounded historical contracts with explicit bar closure and knowledge clocks."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, StringConstraints, model_validator

from ainvest.data.indicators import Digest, IndicatorRun
from ainvest.data.models import PriceAdjustment
from ainvest.portfolio.sizer import SizingConfig, SizingResult
from ainvest.risk.engine import RiskEngineOutput
from ainvest.risk.models import (
    ExposureInputs,
    InstrumentMetadata,
    KillSwitchSnapshot,
    RecentOrderSubmission,
    RiskRuleConfig,
)
from ainvest.schemas.common import (
    DomainModel,
    InstrumentIdentity,
    MachineCode,
    NonNegativeDecimal,
    StableId,
    UtcDateTime,
)
from ainvest.schemas.market import MarketQuote, OhlcvBar
from ainvest.schemas.portfolio import AccountScope, PortfolioSnapshot
from ainvest.schemas.strategy import StrategyContext, StrategyState
from ainvest.strategies.definitions import StrategyResult
from ainvest.strategies.worker.digests import digest_json
from ainvest.strategies.worker.protocol import WorkerLimits

Version = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
Timeframe = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]?[mhdw]$")]


class ClosedHistoricalBar(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    bar: OhlcvBar
    closed_at: UtcDateTime

    @model_validator(mode="after")
    def _closed_capture(self) -> Self:
        if (
            self.closed_at <= self.bar.bar_start
            or self.bar.provenance.observed_at < self.closed_at
            or self.bar.provenance.received_at < self.closed_at
        ):
            raise ValueError("bar must be captured after its explicit closure")
        return self


class ReplayMarketData(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    instrument: InstrumentIdentity
    timeframe: Timeframe
    adjustment: Literal[PriceAdjustment.RAW] = PriceAdjustment.RAW
    bars: Annotated[tuple[ClosedHistoricalBar, ...], Field(min_length=1, max_length=500)]
    quotes: Annotated[tuple[MarketQuote, ...], Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def _series(self) -> Self:
        starts = tuple(item.bar.bar_start for item in self.bars)
        if tuple(sorted(set(starts))) != starts:
            raise ValueError("historical bars must be unique and ordered")
        if any(
            item.bar.instrument != self.instrument or item.bar.interval != self.timeframe
            for item in self.bars
        ):
            raise ValueError("historical series identity/interval differs")
        if len({item.bar.provenance.source for item in self.bars}) != 1:
            raise ValueError("historical bars mix providers")
        if any(item.instrument != self.instrument for item in self.quotes):
            raise ValueError("historical quote identity differs")
        clocks = tuple(
            (item.provenance.observed_at, item.provenance.received_at) for item in self.quotes
        )
        if tuple(sorted(set(clocks))) != clocks:
            raise ValueError("quotes must have unique ordered knowledge clocks")
        return self


class ReplayPoint(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    as_of: UtcDateTime
    available_at: UtcDateTime
    expected_starts: Annotated[tuple[UtcDateTime, ...], Field(min_length=1, max_length=500)]
    portfolio: PortfolioSnapshot
    instrument_metadata: InstrumentMetadata
    exposure_inputs: ExposureInputs
    short_term_volatility_bps: NonNegativeDecimal
    kill_switch: KillSwitchSnapshot
    recent_submissions: Annotated[tuple[RecentOrderSubmission, ...], Field(max_length=128)]

    @model_validator(mode="after")
    def _point(self) -> Self:
        if self.available_at > self.as_of or self.portfolio.as_of > self.as_of:
            raise ValueError("historical portfolio/policy is not available at cutoff")
        if self.portfolio.account_scope != AccountScope.PAPER:
            raise ValueError("historical replay accepts Paper snapshots only")
        if (
            self.portfolio.provenance.observed_at > self.as_of
            or self.portfolio.provenance.received_at > self.as_of
            or any(item.submitted_at > self.as_of for item in self.recent_submissions)
            or any(item.submitted_at > self.as_of for item in self.portfolio.open_orders)
            or (
                self.kill_switch.updated_at is not None and self.kill_switch.updated_at > self.as_of
            )
        ):
            raise ValueError("future historical risk/portfolio inputs")
        if (
            tuple(sorted(set(self.expected_starts))) != self.expected_starts
            or self.expected_starts[-1] > self.as_of
        ):
            raise ValueError("expected historical grid must be ordered and not future")
        if any(
            position.instrument.identity_as_of > self.as_of for position in self.portfolio.positions
        ):
            raise ValueError("future portfolio position identity")
        if any(
            order.instrument.identity_as_of > self.as_of for order in self.portfolio.open_orders
        ):
            raise ValueError("future portfolio order identity")
        return self


class ReplayConfig(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: StableId
    code_version: Version
    config_version: Version
    params: Annotated[dict[str, JsonValue], Field(max_length=32)]
    initial_state: StrategyState | None
    sizing: SizingConfig
    risk: RiskRuleConfig
    worker_limits: WorkerLimits
    max_age_seconds: Annotated[int, Field(strict=True, ge=0, le=86_400)]
    duration_seconds: Annotated[int, Field(strict=True, ge=1, le=120)]

    @model_validator(mode="after")
    def _isolation(self) -> Self:
        limits = self.worker_limits
        if (
            not limits.block_network
            or not limits.read_only_workdir
            or limits.cpu_seconds is None
            or limits.memory_limit_bytes is None
            or limits.wall_timeout_seconds > 10
            or limits.memory_limit_bytes > 512 * 1024 * 1024
        ):
            raise ValueError("replay requires bounded isolated strategy workers")
        return self


class ReplayStep(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    context: StrategyContext
    visible_input_digest: Digest
    context_digest: Digest
    worker_input_digest: Digest
    parameter_digest: Digest
    calculation: IndicatorRun
    strategy_result: StrategyResult
    sizing: SizingResult | None
    risk: RiskEngineOutput | None

    @model_validator(mode="after")
    def _binding(self) -> Self:
        if (
            self.context_digest != digest_json(self.context.model_dump(mode="json"))
            or self.worker_input_digest != self.context_digest
        ):
            raise ValueError("replay context digest binding differs")
        if self.sizing is not None and self.sizing.as_of != self.context.as_of:
            raise ValueError("sizing clock differs from replay context")
        if self.calculation.technical != self.context.research.technical:
            raise ValueError("retained indicators differ from replay context")
        candidate = self.sizing.candidate if self.sizing is not None else None
        if (candidate is None) != (self.risk is None):
            raise ValueError("sized candidate requires its retained risk result")
        if (
            self.risk is not None
            and candidate is not None
            and (
                self.risk.decision.candidate_id != candidate.candidate_id
                or self.risk.decision.decided_at != self.context.as_of
            )
        ):
            raise ValueError("risk result differs from replay candidate/clock")
        return self


class ReplayResult(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    runner_version: Literal["decision-replay-v1"] = "decision-replay-v1"
    run_id: StableId
    status: Literal["complete", "error"]
    error_code: MachineCode | None
    code_version: Version
    config_version: Version
    strategy_name: str
    strategy_version: str
    plugin_version: str
    source_commit: str
    dataset_digest: Digest
    schedule_digest: Digest
    config_digest: Digest
    calendar_digest: Digest
    steps: Annotated[tuple[ReplayStep, ...], Field(max_length=64)]
    semantic_digest: Digest
    execution_enabled: Literal[False] = False
    performance_report_available: Literal[False] = False

    @model_validator(mode="after")
    def _status(self) -> Self:
        if (self.status == "error") != (self.error_code is not None):
            raise ValueError("replay status/error code differs")
        if self.semantic_digest != digest_json(
            [step.model_dump(mode="json") for step in self.steps]
        ):
            raise ValueError("replay semantic digest differs")
        return self
