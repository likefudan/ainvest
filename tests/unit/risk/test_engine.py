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
    AllowlistEntry,
    EligibilityLimits,
    EvaluationPhase,
    ExposureInputs,
    ExposureLimits,
    InstrumentMetadata,
    MarketQualityLimits,
    PhaseMarketQualityLimits,
    RiskContext,
    RiskRuleConfig,
    RuleResult,
    SectorAssignment,
)
from ainvest.risk.rules import DEFAULT_C4A_RULE_CODES
from ainvest.schemas.common import AssetType
from ainvest.schemas.examples import (
    candidate_order_example,
    market_quote_example,
    portfolio_snapshot_example,
)
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.orders import CandidateOrder
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.risk import RiskOutcome, RiskSeverity


def _phase_limits(
    *,
    age: int = 60,
    spread: str = "50",
    deviation: str = "100",
    vol: str = "500",
) -> PhaseMarketQualityLimits:
    return PhaseMarketQualityLimits(
        max_quote_age_seconds=age,
        max_spread_bps=Decimal(spread),
        max_limit_deviation_bps=Decimal(deviation),
        max_short_term_volatility_bps=Decimal(vol),
    )


def _config() -> RiskRuleConfig:
    return RiskRuleConfig(
        rule_set_version="c4a-1.0.0",
        eligibility=EligibilityLimits(
            allowlist=(
                AllowlistEntry(
                    instrument_id="rh_inst_aapl_xnas",
                    symbol="AAPL",
                    exchange="XNAS",
                    currency="USD",
                    asset_type=AssetType.EQUITY,
                ),
            )
        ),
        market_quality=MarketQualityLimits(
            proposal=_phase_limits(),
            pretrade=_phase_limits(age=30, spread="25", deviation="50", vol="300"),
            max_clock_skew_seconds=5,
        ),
        exposure=ExposureLimits(
            max_order_notional=Decimal("10000"),
            max_symbol_weight=Decimal("0.50"),
            max_sector_weight=Decimal("0.80"),
            max_daily_turnover=Decimal("50000"),
            min_cash_reserve_weight=Decimal("0.0"),
            max_daily_loss=Decimal("10000"),
        ),
    )


def _instrument(**overrides: object) -> InstrumentMetadata:
    payload = {
        "instrument_id": "rh_inst_aapl_xnas",
        "symbol": "AAPL",
        "exchange": "XNAS",
        "currency": "USD",
        "asset_type": "EQUITY",
        "tradable": True,
        "price_increment": "0.01",
        "quantity_increment": "1",
    }
    payload.update(overrides)
    return InstrumentMetadata.model_validate(payload)


def _candidate(**overrides: object) -> CandidateOrder:
    payload = candidate_order_example()
    payload["account_scope"] = "paper"
    payload.update(overrides)
    return CandidateOrder.model_validate(payload)


def _quote(**overrides: object) -> MarketQuote:
    payload = market_quote_example()
    payload.update(overrides)
    return MarketQuote.model_validate(payload)


def _context(
    *,
    phase: EvaluationPhase = EvaluationPhase.PROPOSAL,
    as_of: datetime | None = None,
    candidate: CandidateOrder | None = None,
    quote: MarketQuote | None = None,
    instrument: InstrumentMetadata | None = None,
    vol: str = "10",
) -> RiskContext:
    moment = as_of or datetime(2026, 7, 24, 18, 30, 0, tzinfo=UTC)
    return RiskContext(
        risk_decision_id="risk_01HZYC4ATEST0001",
        phase=phase,
        as_of=moment,
        candidate=candidate or _candidate(),
        quote=quote or _quote(),
        instrument=instrument or _instrument(),
        config=_config(),
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
    quote = _quote(
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
    out = evaluate_risk(ctx, calendar=cal, rule_codes=DEFAULT_C4A_RULE_CODES)
    assert out.decision.outcome is RiskOutcome.APPROVED
    assert out.input_digest.startswith("sha256:")
    assert out.config_digest.startswith("sha256:")
    assert out.decision.rule_set_version == "c4a-1.0.0"


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
