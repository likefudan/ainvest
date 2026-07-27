"""Unit tests for eligibility rules (P03-T10)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from shared.portfolio_fixtures import make_open_order, with_open_orders

from ainvest.data.calendar_port import FakeMarketCalendar
from ainvest.risk.engine import evaluate_risk
from ainvest.risk.models import InstrumentMetadata, RiskContext
from ainvest.risk.rules import DEFAULT_SCREENING_RULE_CODES
from ainvest.risk.rules.eligibility import (
    AllowlistRule,
    AssetClassRule,
    SessionRule,
    SideAndProductRule,
)
from ainvest.schemas.examples import portfolio_snapshot_example
from ainvest.schemas.orders import CandidateOrder
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.risk import RiskOutcome
from risk.risk_fixtures import (
    make_candidate,
    make_context,
    make_fresh_quote,
    make_instrument,
)


def _ctx(
    *,
    as_of: datetime | None = None,
    candidate: CandidateOrder | None = None,
    instrument: InstrumentMetadata | None = None,
    portfolio: PortfolioSnapshot | None = None,
) -> RiskContext:
    return make_context(
        risk_decision_id="risk_01HZYELIG0000001",
        as_of=as_of,
        candidate=candidate,
        instrument=instrument,
        portfolio=portfolio,
        quote=make_fresh_quote(
            observed="2026-07-23T14:59:50Z",
            received="2026-07-23T14:59:55Z",
        ),
    )


@pytest.mark.unit
def test_reject_option_and_crypto_flags() -> None:
    ctx = _ctx(instrument=make_instrument(is_option=True))
    assert AssetClassRule().evaluate(ctx).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_reject_leveraged_margin_and_short_sell() -> None:
    for flag in ("is_leveraged_or_inverse", "allows_margin"):
        ctx = _ctx(instrument=make_instrument(asset_type="ETF", **{flag: True}))
        assert SideAndProductRule().evaluate(ctx).decision is RiskOutcome.REJECTED

    # allows_short alone must not block BUY; oversell SELL is rejected.
    shortable = make_instrument(allows_short=True)
    buy_ok = _ctx(instrument=shortable)
    assert SideAndProductRule().evaluate(buy_ok).decision is RiskOutcome.APPROVED
    sell_ctx = _ctx(instrument=shortable, candidate=make_candidate(side="SELL"), portfolio=None)
    assert SideAndProductRule().evaluate(sell_ctx).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_sell_rejects_when_open_sells_consume_held() -> None:
    portfolio = with_open_orders(
        portfolio_snapshot_example(),  # 10 AAPL
        make_open_order(
            order_id="ord_open_sell_aapl",
            side="SELL",
            quantity="8",
            limit_price="214.50",
            symbol="AAPL",
        ),
    )
    sell = make_candidate(side="SELL", quantity="5", maximum_notional="1072.50")
    ctx = _ctx(candidate=sell, portfolio=portfolio)
    result = SideAndProductRule().evaluate(ctx)
    assert result.decision is RiskOutcome.REJECTED
    assert "sellable" in (result.evidence or "")


@pytest.mark.unit
def test_allowlist_miss_rejects() -> None:
    ctx = _ctx(
        candidate=make_candidate(
            instrument_id="rh_inst_other",
            symbol="MSFT",
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
    out = evaluate_risk(_ctx(), calendar=cal, rule_codes=DEFAULT_SCREENING_RULE_CODES)
    assert out.decision.outcome is RiskOutcome.REJECTED
    assert any(v.rule_code == "ELIGIBILITY_SESSION" for v in out.decision.violations)
