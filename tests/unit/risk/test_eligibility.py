"""Unit tests for eligibility rules (P03-T10)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from ainvest.data.calendar_port import FakeMarketCalendar
from ainvest.risk.engine import evaluate_risk
from ainvest.risk.models import (
    AllowlistEntry,
    EligibilityLimits,
    EvaluationPhase,
    ExposureLimits,
    InstrumentMetadata,
    MarketQualityLimits,
    PhaseMarketQualityLimits,
    RiskContext,
    RiskRuleConfig,
)
from ainvest.risk.rules import DEFAULT_C4A_RULE_CODES
from ainvest.risk.rules.eligibility import (
    AllowlistRule,
    AssetClassRule,
    SessionRule,
    SideAndProductRule,
)
from ainvest.schemas.common import AssetType
from ainvest.schemas.examples import candidate_order_example, market_quote_example
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.orders import CandidateOrder
from ainvest.schemas.risk import RiskOutcome


def _mq() -> MarketQualityLimits:
    phase = PhaseMarketQualityLimits(
        max_quote_age_seconds=120,
        max_spread_bps=Decimal("100"),
        max_limit_deviation_bps=Decimal("500"),
        max_short_term_volatility_bps=Decimal("1000"),
    )
    return MarketQualityLimits(proposal=phase, pretrade=phase, max_clock_skew_seconds=30)


def _config() -> RiskRuleConfig:
    return RiskRuleConfig(
        rule_set_version="c4a-1.0.0",
        eligibility=EligibilityLimits(
            allowlist=(
                AllowlistEntry(
                    instrument_id="rh_inst_aapl_xnas",
                    symbol="AAPL",
                    exchange="XNAS",
                    asset_type=AssetType.EQUITY,
                ),
            )
        ),
        market_quality=_mq(),
        exposure=ExposureLimits(
            max_order_notional=Decimal("10000"),
            max_symbol_weight=Decimal("0.50"),
            max_sector_weight=Decimal("0.80"),
            max_daily_turnover=Decimal("50000"),
            min_cash_reserve_weight=Decimal("0.0"),
            max_daily_loss=Decimal("10000"),
        ),
    )


def _ctx(**updates: object) -> RiskContext:
    base = {
        "risk_decision_id": "risk_01HZYELIG0000001",
        "phase": EvaluationPhase.PROPOSAL,
        "as_of": datetime(2026, 7, 23, 15, 0, tzinfo=UTC),
        "candidate": CandidateOrder.model_validate(
            {**candidate_order_example(), "account_scope": "paper"}
        ),
        "quote": MarketQuote.model_validate(
            {
                **market_quote_example(),
                "provenance": {
                    "source": "test",
                    "observed_at": "2026-07-23T14:59:50Z",
                    "received_at": "2026-07-23T14:59:55Z",
                    "timezone": "UTC",
                    "is_delayed": False,
                    "quality_flags": [],
                },
            }
        ),
        "instrument": InstrumentMetadata.model_validate(
            {
                "instrument_id": "rh_inst_aapl_xnas",
                "symbol": "AAPL",
                "exchange": "XNAS",
                "currency": "USD",
                "asset_type": "EQUITY",
                "tradable": True,
                "price_increment": "0.01",
                "quantity_increment": "1",
            }
        ),
        "config": _config(),
        "short_term_volatility_bps": Decimal("10"),
    }
    base.update(updates)
    return RiskContext.model_validate(base)


@pytest.mark.unit
def test_reject_option_and_crypto_flags() -> None:
    ctx = _ctx(
        instrument=InstrumentMetadata.model_validate(
            {
                "instrument_id": "rh_inst_aapl_xnas",
                "symbol": "AAPL",
                "exchange": "XNAS",
                "currency": "USD",
                "asset_type": "EQUITY",
                "tradable": True,
                "price_increment": "0.01",
                "quantity_increment": "1",
                "is_option": True,
            }
        )
    )
    assert AssetClassRule().evaluate(ctx).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_reject_leveraged_margin_and_short_sell() -> None:
    for flag in ("is_leveraged_or_inverse", "allows_margin"):
        meta = {
            "instrument_id": "rh_inst_aapl_xnas",
            "symbol": "AAPL",
            "exchange": "XNAS",
            "currency": "USD",
            "asset_type": "ETF",
            "tradable": True,
            "price_increment": "0.01",
            "quantity_increment": "1",
            flag: True,
        }
        ctx = _ctx(instrument=InstrumentMetadata.model_validate(meta))
        assert SideAndProductRule().evaluate(ctx).decision is RiskOutcome.REJECTED

    # allows_short alone must not block BUY; oversell SELL is rejected.
    shortable = InstrumentMetadata.model_validate(
        {
            "instrument_id": "rh_inst_aapl_xnas",
            "symbol": "AAPL",
            "exchange": "XNAS",
            "currency": "USD",
            "asset_type": "EQUITY",
            "tradable": True,
            "price_increment": "0.01",
            "quantity_increment": "1",
            "allows_short": True,
        }
    )
    buy_ok = _ctx(instrument=shortable)
    assert SideAndProductRule().evaluate(buy_ok).decision is RiskOutcome.APPROVED
    sell = CandidateOrder.model_validate(
        {**candidate_order_example(), "account_scope": "paper", "side": "SELL"}
    )
    sell_ctx = _ctx(instrument=shortable, candidate=sell, portfolio=None)
    assert SideAndProductRule().evaluate(sell_ctx).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_sell_rejects_when_open_sells_consume_held() -> None:
    from ainvest.schemas.examples import portfolio_snapshot_example
    from ainvest.schemas.portfolio import PortfolioSnapshot

    payload = portfolio_snapshot_example()  # 10 AAPL
    payload["open_orders"] = [
        {
            "order_id": "ord_open_sell_aapl",
            "instrument": payload["positions"][0]["instrument"],
            "side": "SELL",
            "quantity": "8",
            "submitted_at": "2026-07-24T18:29:00Z",
            "limit_price": "214.50",
            "symbol": "AAPL",
        }
    ]
    portfolio = PortfolioSnapshot.model_validate(payload)
    sell = CandidateOrder.model_validate(
        {
            **candidate_order_example(),
            "account_scope": "paper",
            "side": "SELL",
            "quantity": "5",
            "maximum_notional": "1072.50",
        }
    )
    ctx = _ctx(candidate=sell, portfolio=portfolio)
    result = SideAndProductRule().evaluate(ctx)
    assert result.decision is RiskOutcome.REJECTED
    assert "sellable" in (result.evidence or "")


@pytest.mark.unit
def test_allowlist_miss_rejects() -> None:
    ctx = _ctx(
        candidate=CandidateOrder.model_validate(
            {
                **candidate_order_example(),
                "account_scope": "paper",
                "instrument_id": "rh_inst_other",
                "symbol": "MSFT",
            }
        )
    )
    assert AllowlistRule().evaluate(ctx).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_session_holiday_and_off_hours_reject() -> None:
    cal = FakeMarketCalendar(holidays=frozenset({date(2026, 7, 3)}))
    rule = SessionRule(cal)
    holiday_ctx = _ctx(as_of=datetime(2026, 7, 3, 15, 0, tzinfo=UTC))
    assert rule.evaluate(holiday_ctx).decision is RiskOutcome.REJECTED
    night = _ctx(as_of=datetime(2026, 7, 23, 2, 0, tzinfo=UTC))
    assert rule.evaluate(night).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_full_engine_rejects_unknown_exchange_session() -> None:
    cal = FakeMarketCalendar(supported_exchanges=frozenset({"XNYS"}))
    # Candidate exchange is XNAS → UNKNOWN → reject
    out = evaluate_risk(_ctx(), calendar=cal, rule_codes=DEFAULT_C4A_RULE_CODES)
    assert out.decision.outcome is RiskOutcome.REJECTED
    assert any(v.rule_code == "ELIGIBILITY_SESSION" for v in out.decision.violations)
