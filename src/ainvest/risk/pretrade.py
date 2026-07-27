"""Pre-trade risk re-evaluation before broker submission (P03-T12).

Always re-fetches market/account state and runs a fresh rule evaluation with a
new ``risk_decision_id``. A prior proposal-time APPROVED decision is never
copied into the pre-trade outcome.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Protocol

from pydantic import StringConstraints, model_validator

from ainvest.data.calendar_port import MarketCalendar
from ainvest.risk.engine import RiskEngineOutput, evaluate_risk
from ainvest.risk.kill_switch import KillSwitch
from ainvest.risk.models import (
    EvaluationPhase,
    ExposureInputs,
    InstrumentMetadata,
    KillSwitchSnapshot,
    RecentOrderSubmission,
    RiskContext,
    RiskRuleConfig,
)
from ainvest.risk.rules import PRETRADE_RULE_CODES
from ainvest.schemas.common import (
    DomainModel,
    NonNegativeDecimal,
    StableId,
    UtcDateTime,
    ensure_utc,
)
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.orders import CandidateOrder, OrderHashDigest
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.risk import RiskDecision, RiskOutcome

ClientOrderId = Annotated[str, StringConstraints(min_length=3, max_length=128)]


class PretradeMarketData(Protocol):
    """Fresh read port for pre-trade inputs (injected; no execution imports)."""

    def fetch_quote(self, instrument_id: str, *, as_of: datetime) -> MarketQuote:
        """Return a fresh quote for ``instrument_id`` at ``as_of``."""
        ...

    def fetch_portfolio(self, *, as_of: datetime) -> PortfolioSnapshot:
        """Return account, positions, and open orders at ``as_of``."""
        ...


class PretradeRequest(DomainModel):
    """Inputs for one pre-trade evaluation.

    ``risk_decision_id`` must be a new id. ``prior_proposal_decision_id`` is
    retained for audit correlation only and must never equal ``risk_decision_id``.

    ``recent_submissions`` must be an explicit load: ``None`` means history is
    unavailable (duplicate rules hard-reject). An empty tuple means the window
    was loaded and contained no prior submissions.
    """

    risk_decision_id: StableId
    as_of: UtcDateTime
    candidate: CandidateOrder
    instrument: InstrumentMetadata
    config: RiskRuleConfig
    client_order_id: ClientOrderId
    proposal_order_hash: OrderHashDigest
    prior_proposal_decision_id: StableId | None = None
    # None = history unavailable (fail closed). () = explicitly loaded empty window.
    recent_submissions: tuple[RecentOrderSubmission, ...] | None = None
    exposure_inputs: ExposureInputs | None = None
    short_term_volatility_bps: NonNegativeDecimal | None = None

    @model_validator(mode="after")
    def _distinct_decision_id(self) -> PretradeRequest:
        if (
            self.prior_proposal_decision_id is not None
            and self.prior_proposal_decision_id == self.risk_decision_id
        ):
            raise ValueError("pre-trade risk_decision_id must not reuse the proposal decision id")
        return self


def _reject_stale_snapshot(
    *,
    label: str,
    snapshot_as_of: datetime,
    evaluation_as_of: datetime,
    max_skew_seconds: int,
) -> None:
    """Fail closed when a re-fetched snapshot is in the future or too old."""
    if snapshot_as_of > evaluation_as_of:
        raise ValueError(f"{label} as_of must be <= evaluation as_of")
    age = evaluation_as_of - snapshot_as_of
    if age > timedelta(seconds=max_skew_seconds):
        raise ValueError(
            f"{label} snapshot is stale relative to evaluation as_of "
            f"(age_seconds={int(age.total_seconds())}; "
            f"max_skew_seconds={max_skew_seconds})"
        )


def evaluate_pretrade(
    request: PretradeRequest,
    *,
    market_data: PretradeMarketData,
    kill_switch: KillSwitch | KillSwitchSnapshot,
    calendar: MarketCalendar,
    prior_decision: RiskDecision | None = None,
) -> RiskEngineOutput:
    """Re-fetch state and run the full pre-trade rule set.

    ``prior_decision`` is accepted only for correlation/assertions. Its outcome
    is never returned. A prior APPROVED result cannot authorize execution.
    """
    clock = ensure_utc(request.as_of)
    if prior_decision is not None:
        if prior_decision.risk_decision_id == request.risk_decision_id:
            raise ValueError("pre-trade must use a distinct risk_decision_id")
        # Explicitly discard prior outcome — never reuse APPROVED.
        del prior_decision

    quote = market_data.fetch_quote(request.candidate.instrument_id, as_of=clock)
    portfolio = market_data.fetch_portfolio(as_of=clock)
    skew = request.config.market_quality.max_clock_skew_seconds
    _reject_stale_snapshot(
        label="quote",
        snapshot_as_of=quote.provenance.received_at,
        evaluation_as_of=clock,
        max_skew_seconds=skew,
    )
    _reject_stale_snapshot(
        label="portfolio",
        snapshot_as_of=portfolio.as_of,
        evaluation_as_of=clock,
        max_skew_seconds=skew,
    )

    if isinstance(kill_switch, KillSwitch):
        snapshot = kill_switch.snapshot()
        if snapshot.is_active:
            kill_switch.record_blocked_submission(
                reason=snapshot.reason or "kill switch active at pre-trade",
                as_of=clock,
            )
    else:
        snapshot = kill_switch

    context = RiskContext(
        risk_decision_id=request.risk_decision_id,
        phase=EvaluationPhase.PRETRADE,
        as_of=clock,
        candidate=request.candidate,
        quote=quote,
        instrument=request.instrument,
        config=request.config,
        portfolio=portfolio,
        short_term_volatility_bps=request.short_term_volatility_bps,
        exposure_inputs=request.exposure_inputs,
        kill_switch=snapshot,
        recent_submissions=request.recent_submissions,
        client_order_id=request.client_order_id,
        proposal_order_hash=request.proposal_order_hash,
    )
    output = evaluate_risk(context, calendar=calendar, rule_codes=PRETRADE_RULE_CODES)
    if output.decision.risk_decision_id != request.risk_decision_id:
        raise RuntimeError("pre-trade decision id mismatch")
    if (
        request.prior_proposal_decision_id is not None
        and output.decision.risk_decision_id == request.prior_proposal_decision_id
    ):
        raise RuntimeError("pre-trade reused proposal decision id")
    if output.decision.outcome is RiskOutcome.APPROVED and snapshot.is_active:
        raise RuntimeError("active kill switch cannot produce APPROVED")
    return output


__all__ = [
    "ClientOrderId",
    "PretradeMarketData",
    "PretradeRequest",
    "evaluate_pretrade",
]
