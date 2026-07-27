"""Unit tests for exposure rules (P03-T9)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from risk_fixtures import (
    make_candidate,
    make_context,
    make_exposure_limits,
    make_instrument,
    make_quote,
    make_risk_config,
)

from ainvest.risk.models import (
    EvaluationPhase,
    ExposureInputs,
    ExposureLimits,
    RiskContext,
    SectorAssignment,
)
from ainvest.risk.rules.exposure import (
    DailyLossRule,
    DailyTurnoverRule,
    MaxOrderNotionalRule,
    MinCashReserveRule,
    SectorExposureRule,
    SymbolWeightRule,
)
from ainvest.schemas.examples import (
    candidate_order_example,
    portfolio_snapshot_example,
)
from ainvest.schemas.orders import CandidateOrder
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.risk import RiskOutcome


def _exposure(
    *,
    max_notional: str = "500",
    max_symbol: str = "0.50",
    max_sector: str = "0.80",
    max_turnover: str = "1000",
    min_cash: str = "0.10",
    max_loss: str = "100",
) -> ExposureLimits:
    return make_exposure_limits(
        max_notional=max_notional,
        max_symbol=max_symbol,
        max_sector=max_sector,
        max_turnover=max_turnover,
        min_cash=min_cash,
        max_loss=max_loss,
    )


def _ctx(
    *,
    exposure: ExposureLimits | None = None,
    candidate: CandidateOrder | None = None,
    portfolio: PortfolioSnapshot | None = None,
    inputs: ExposureInputs | None = None,
    include_portfolio: bool = True,
    include_inputs: bool = True,
) -> RiskContext:
    cand = candidate or make_candidate(
        quantity="2",
        limit_price="214.50",
        maximum_notional="429.00",
    )
    quote = make_quote(
        last_price="214.50",
        bid="214.40",
        ask="214.60",
    )
    port = portfolio
    if include_portfolio and port is None:
        port = PortfolioSnapshot.model_validate(portfolio_snapshot_example())
    exp_in = inputs
    if include_inputs and exp_in is None:
        exp_in = ExposureInputs(
            sectors=(SectorAssignment(instrument_id="rh_inst_aapl_xnas", sector="TECH"),),
            daily_turnover_to_date=Decimal("100"),
            daily_realized_pnl=Decimal("0"),
            daily_unrealized_pnl=Decimal("0"),
        )
    return make_context(
        risk_decision_id="risk_01HZYEXPOSURE0001",
        phase=EvaluationPhase.PROPOSAL,
        as_of=datetime(2026, 7, 23, 15, 0, tzinfo=UTC),
        candidate=cand,
        quote=quote,
        instrument=make_instrument(),
        config=make_risk_config(exposure=exposure or _exposure()),
        portfolio=port,
        short_term_volatility_bps=Decimal("10"),
        exposure_inputs=exp_in,
    )


@pytest.mark.unit
def test_max_notional_boundary() -> None:
    # notional = 2 * 214.50 = 429
    ok = _ctx(exposure=_exposure(max_notional="429"))
    assert MaxOrderNotionalRule().evaluate(ok).decision is RiskOutcome.APPROVED
    over = _ctx(exposure=_exposure(max_notional="428.99"))
    assert MaxOrderNotionalRule().evaluate(over).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_symbol_weight_and_missing_portfolio() -> None:
    assert (
        SymbolWeightRule().evaluate(_ctx(include_portfolio=False)).decision is RiskOutcome.REJECTED
    )
    # Portfolio example equity 5154.20 with 10 AAPL; buying 2 more at 214.50
    # projected symbol mv ~ 12*214.50 = 2574; equity rises by ~0 net of cash move
    ctx = _ctx(exposure=_exposure(max_symbol="0.01"))
    assert SymbolWeightRule().evaluate(ctx).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_sector_missing_and_over_limit() -> None:
    missing = _ctx(
        inputs=ExposureInputs(
            sectors=(),
            daily_turnover_to_date=Decimal("0"),
            daily_realized_pnl=Decimal("0"),
            daily_unrealized_pnl=Decimal("0"),
        )
    )
    assert SectorExposureRule().evaluate(missing).decision is RiskOutcome.REJECTED
    tight = _ctx(exposure=_exposure(max_sector="0.01"))
    assert SectorExposureRule().evaluate(tight).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_daily_turnover_boundary() -> None:
    # prior 100 + 429 = 529
    ok = _ctx(exposure=_exposure(max_turnover="529"))
    assert DailyTurnoverRule().evaluate(ok).decision is RiskOutcome.APPROVED
    over = _ctx(exposure=_exposure(max_turnover="528"))
    assert DailyTurnoverRule().evaluate(over).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_min_cash_reserve() -> None:
    # Force high reserve requirement so BUY fails.
    ctx = _ctx(exposure=_exposure(min_cash="0.95"))
    assert MinCashReserveRule().evaluate(ctx).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_daily_loss_incomplete_and_breach() -> None:
    incomplete = _ctx(
        inputs=ExposureInputs(
            sectors=(SectorAssignment(instrument_id="rh_inst_aapl_xnas", sector="TECH"),),
            daily_turnover_to_date=Decimal("0"),
            daily_realized_pnl=Decimal("-10"),
            daily_unrealized_pnl=None,
        )
    )
    assert DailyLossRule().evaluate(incomplete).decision is RiskOutcome.REJECTED

    loss = _ctx(
        exposure=_exposure(max_loss="50"),
        inputs=ExposureInputs(
            sectors=(SectorAssignment(instrument_id="rh_inst_aapl_xnas", sector="TECH"),),
            daily_turnover_to_date=Decimal("0"),
            daily_realized_pnl=Decimal("-40"),
            daily_unrealized_pnl=Decimal("-20"),
        ),
    )
    assert DailyLossRule().evaluate(loss).decision is RiskOutcome.REJECTED

    ok = _ctx(
        exposure=_exposure(max_loss="50"),
        inputs=ExposureInputs(
            sectors=(SectorAssignment(instrument_id="rh_inst_aapl_xnas", sector="TECH"),),
            daily_turnover_to_date=Decimal("0"),
            daily_realized_pnl=Decimal("-10"),
            daily_unrealized_pnl=Decimal("-20"),
        ),
    )
    assert DailyLossRule().evaluate(ok).decision is RiskOutcome.APPROVED


@pytest.mark.unit
def test_min_cash_rejects_when_open_buys_already_commit_cash() -> None:
    payload = portfolio_snapshot_example()
    payload["cash"] = "500.00"
    payload["buying_power"] = "100.00"
    payload["equity"] = "500.00"
    payload["positions"] = []
    payload["exposure"] = {
        "cash": "500.00",
        "equity": "500.00",
        "gross_market_value": "0",
        "net_market_value": "0",
        "largest_position_weight": "0",
        "position_count": 0,
    }
    payload["open_orders"] = [
        {
            "order_id": "ord_open_buy_msft",
            "instrument": {
                "instrument_id": "rh_inst_msft_xnas",
                "symbol": "MSFT",
                "exchange": "XNAS",
                "currency": "USD",
                "asset_type": "EQUITY",
                "identity_as_of": "2026-07-24T18:30:00Z",
            },
            "side": "BUY",
            "quantity": "2",
            "submitted_at": "2026-07-24T18:29:00Z",
            "limit_price": "200.00",
            "symbol": "MSFT",
        }
    ]
    # New AAPL buy notional 429; cash after open buy = 100; projected cash negative.
    cand = CandidateOrder.model_validate(
        {
            **candidate_order_example(),
            "account_scope": "paper",
            "quantity": "2",
            "limit_price": "214.50",
            "maximum_notional": "429.00",
        }
    )
    ctx = _ctx(
        exposure=_exposure(min_cash="0"),
        candidate=cand,
        portfolio=PortfolioSnapshot.model_validate(payload),
        include_inputs=True,
        inputs=ExposureInputs(
            sectors=(
                SectorAssignment(instrument_id="rh_inst_aapl_xnas", sector="TECH"),
                SectorAssignment(instrument_id="rh_inst_msft_xnas", sector="TECH"),
            ),
            daily_turnover_to_date=Decimal("0"),
            daily_realized_pnl=Decimal("0"),
            daily_unrealized_pnl=Decimal("0"),
        ),
    )
    result = MinCashReserveRule().evaluate(ctx)
    assert result.decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_symbol_weight_includes_open_buy_qty() -> None:
    payload = portfolio_snapshot_example()  # 10 AAPL, cash 3000, equity 5154.20
    payload["open_orders"] = [
        {
            "order_id": "ord_open_buy_aapl",
            "instrument": payload["positions"][0]["instrument"],
            "side": "BUY",
            "quantity": "20",
            "submitted_at": "2026-07-24T18:29:00Z",
            "limit_price": "214.50",
            "symbol": "AAPL",
        }
    ]
    cand = CandidateOrder.model_validate(
        {
            **candidate_order_example(),
            "account_scope": "paper",
            "quantity": "10",
            "limit_price": "214.50",
            "maximum_notional": "2145.00",
        }
    )
    ctx = _ctx(
        exposure=_exposure(max_symbol="0.60", max_notional="5000", min_cash="0"),
        candidate=cand,
        portfolio=PortfolioSnapshot.model_validate(payload),
    )
    # Effective AAPL qty 10+20+10=40 → weight well above 0.60.
    assert SymbolWeightRule().evaluate(ctx).decision is RiskOutcome.REJECTED
