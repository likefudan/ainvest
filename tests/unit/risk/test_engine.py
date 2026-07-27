"""Unit tests for the risk engine aggregator (P03-T8)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ainvest.data.calendar_port import FakeMarketCalendar
from ainvest.risk.engine import (
    aggregate_rule_results,
    compute_input_digest,
    evaluate_risk,
    evaluate_rules,
)
from ainvest.risk.models import (
    EvaluationPhase,
    ExposureInputs,
    InstrumentMetadata,
    RiskContext,
    RuleResult,
    SectorAssignment,
)
from ainvest.risk.rules import DEFAULT_SCREENING_RULE_CODES
from ainvest.schemas.examples import portfolio_snapshot_example
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.orders import CandidateOrder
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.risk import RiskOutcome, RiskSeverity
from risk.risk_fixtures import (
    make_candidate,
    make_context,
    make_instrument,
    make_market_quality,
    make_phase_limits,
    make_quote,
    make_risk_config,
)


def _context(
    *,
    phase: EvaluationPhase = EvaluationPhase.PROPOSAL,
    as_of: datetime | None = None,
    candidate: CandidateOrder | None = None,
    quote: MarketQuote | None = None,
    instrument: InstrumentMetadata | None = None,
    vol: str = "10",
) -> RiskContext:
    return make_context(
        risk_decision_id="risk_01HZYC4ATEST0001",
        phase=phase,
        as_of=as_of or datetime(2026, 7, 24, 18, 30, 0, tzinfo=UTC),
        candidate=candidate or make_candidate(),
        quote=quote or make_quote(),
        instrument=instrument or make_instrument(),
        config=make_risk_config(
            market_quality=make_market_quality(
                proposal=make_phase_limits(age=60, spread="50", deviation="100", vol="500"),
                pretrade=make_phase_limits(age=30, spread="25", deviation="50", vol="300"),
                max_clock_skew_seconds=5,
            )
        ),
        short_term_volatility_bps=Decimal(vol),
    )


@pytest.mark.unit
def test_aggregate_is_order_independent() -> None:
    hard = RuleResult(
        rule_code="A_HARD",
        severity=RiskSeverity.HARD,
        decision=RiskOutcome.REJECTED,
        reason="hard",
    )
    review = RuleResult(
        rule_code="B_REVIEW",
        severity=RiskSeverity.REVIEW,
        decision=RiskOutcome.NEEDS_REVIEW,
        reason="review",
    )
    ok = RuleResult(
        rule_code="C_OK",
        severity=RiskSeverity.INFO,
        decision=RiskOutcome.APPROVED,
        reason="ok",
    )
    o1, v1 = aggregate_rule_results([ok, review, hard])
    o2, v2 = aggregate_rule_results([hard, ok, review])
    assert o1 is RiskOutcome.REJECTED and o2 is RiskOutcome.REJECTED
    assert {v.rule_code for v in v1} == {v.rule_code for v in v2}


@pytest.mark.unit
def test_rule_exception_fails_closed() -> None:
    class Boom:
        code = "BOOM"

        def evaluate(self, context: RiskContext) -> RuleResult:
            del context
            raise RuntimeError("boom")

    out = evaluate_rules(_context(), [Boom()])
    assert out.decision.outcome is RiskOutcome.REJECTED
    assert out.decision.reason_code == "RULE_EXCEPTION"


@pytest.mark.unit
def test_unknown_rule_fails_closed() -> None:
    cal = FakeMarketCalendar()
    # Mid-session Thursday 2026-07-23 15:00 UTC = 11:00 ET
    ctx = _context(as_of=datetime(2026, 7, 23, 15, 0, tzinfo=UTC))
    out = evaluate_risk(ctx, calendar=cal, rule_codes=("NOT_A_REAL_RULE",))
    assert out.decision.outcome is RiskOutcome.REJECTED
    assert out.decision.reason_code == "UNKNOWN_RULE"


@pytest.mark.unit
def test_happy_path_approved_with_digests() -> None:
    cal = FakeMarketCalendar()
    ctx = _context(as_of=datetime(2026, 7, 23, 15, 0, tzinfo=UTC))
    # Align quote observed_at with as_of window
    quote = make_quote(
        provenance={
            "source": "test.quotes",
            "observed_at": "2026-07-23T14:59:50Z",
            "received_at": "2026-07-23T14:59:55Z",
            "timezone": "UTC",
            "is_delayed": False,
            "quality_flags": [],
        },
        last_price="214.50",
        bid="214.48",
        ask="214.52",
    )
    ctx = ctx.model_copy(update={"quote": quote})
    out = evaluate_risk(ctx, calendar=cal, rule_codes=DEFAULT_SCREENING_RULE_CODES)
    assert out.decision.outcome is RiskOutcome.APPROVED
    assert out.input_digest.startswith("sha256:")
    assert out.config_digest.startswith("sha256:")
    assert out.decision.rule_set_version == "risk-rules-1.0.0"


@pytest.mark.unit
def test_input_digest_includes_portfolio_and_exposure_inputs() -> None:
    base = _context(as_of=datetime(2026, 7, 23, 15, 0, tzinfo=UTC))
    portfolio = PortfolioSnapshot.model_validate(portfolio_snapshot_example())
    exposure = ExposureInputs(
        sectors=(SectorAssignment(instrument_id="rh_inst_aapl_xnas", sector="TECH"),),
        daily_turnover_to_date=Decimal("100"),
        daily_realized_pnl=Decimal("0"),
        daily_unrealized_pnl=Decimal("0"),
    )
    with_port = base.model_copy(update={"portfolio": portfolio, "exposure_inputs": exposure})
    digest_a = compute_input_digest(with_port)

    mutated = portfolio.model_dump(mode="python")
    mutated["open_orders"] = [
        {
            "order_id": "ord_digest_open_sell",
            "instrument": mutated["positions"][0]["instrument"],
            "side": "SELL",
            "quantity": "1",
            "submitted_at": "2026-07-24T18:29:00Z",
            "limit_price": "214.50",
            "symbol": "AAPL",
        }
    ]
    other_port = PortfolioSnapshot.model_validate(mutated)
    digest_b = compute_input_digest(with_port.model_copy(update={"portfolio": other_port}))
    assert digest_a != digest_b

    other_exposure = exposure.model_copy(update={"daily_turnover_to_date": Decimal("200")})
    digest_c = compute_input_digest(
        with_port.model_copy(update={"exposure_inputs": other_exposure})
    )
    assert digest_a != digest_c

    # Order of identical positions/open_orders must not change the digest.
    reordered = portfolio.model_dump(mode="python")
    reordered["positions"] = list(reversed(reordered["positions"]))
    reordered["open_orders"] = [
        {
            "order_id": "ord_b",
            "instrument": reordered["positions"][0]["instrument"],
            "side": "SELL",
            "quantity": "1",
            "submitted_at": "2026-07-24T18:29:00Z",
            "limit_price": "214.50",
            "symbol": "AAPL",
        },
        {
            "order_id": "ord_a",
            "instrument": reordered["positions"][0]["instrument"],
            "side": "SELL",
            "quantity": "1",
            "submitted_at": "2026-07-24T18:29:00Z",
            "limit_price": "214.50",
            "symbol": "AAPL",
        },
    ]
    digest_d = compute_input_digest(
        with_port.model_copy(update={"portfolio": PortfolioSnapshot.model_validate(reordered)})
    )
    reordered["open_orders"] = list(reversed(reordered["open_orders"]))
    digest_e = compute_input_digest(
        with_port.model_copy(update={"portfolio": PortfolioSnapshot.model_validate(reordered)})
    )
    assert digest_d == digest_e

    swapped_sectors = ExposureInputs(
        sectors=(
            SectorAssignment(instrument_id="rh_inst_msft_xnas", sector="TECH"),
            SectorAssignment(instrument_id="rh_inst_aapl_xnas", sector="TECH"),
        ),
        daily_turnover_to_date=Decimal("100"),
        daily_realized_pnl=Decimal("0"),
        daily_unrealized_pnl=Decimal("0"),
    )
    ordered_sectors = ExposureInputs(
        sectors=(
            SectorAssignment(instrument_id="rh_inst_aapl_xnas", sector="TECH"),
            SectorAssignment(instrument_id="rh_inst_msft_xnas", sector="TECH"),
        ),
        daily_turnover_to_date=Decimal("100"),
        daily_realized_pnl=Decimal("0"),
        daily_unrealized_pnl=Decimal("0"),
    )
    assert compute_input_digest(
        with_port.model_copy(update={"exposure_inputs": swapped_sectors})
    ) == compute_input_digest(with_port.model_copy(update={"exposure_inputs": ordered_sectors}))
