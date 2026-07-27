"""Unit tests for open-order commitment helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest
from portfolio_fixtures import (
    make_cash_portfolio,
    make_open_order,
    with_open_orders,
)

from ainvest.schemas.commitments import (
    OpenBuyLimitError,
    baseline_after_open_orders,
    open_buy_reserved_notional,
    open_order_side_quantities,
)
from ainvest.schemas.examples import portfolio_snapshot_example


@pytest.mark.unit
def test_open_order_side_quantities_sums_per_instrument() -> None:
    portfolio = with_open_orders(
        portfolio_snapshot_example(),  # 10 AAPL filled
        make_open_order(
            order_id="ord_buy_aapl",
            side="BUY",
            quantity="3",
            limit_price="214.50",
            symbol="AAPL",
        ),
        make_open_order(
            order_id="ord_sell_aapl",
            side="SELL",
            quantity="2",
            limit_price="214.50",
            symbol="AAPL",
        ),
        make_open_order(
            order_id="ord_buy_msft",
            side="BUY",
            quantity="5",
            limit_price="200.00",
            symbol="MSFT",
        ),
    )
    buy_qty, sell_qty = open_order_side_quantities(portfolio, "rh_inst_aapl_xnas")
    assert buy_qty == Decimal("3")
    assert sell_qty == Decimal("2")
    msft_buy, msft_sell = open_order_side_quantities(portfolio, "rh_inst_msft_xnas")
    assert msft_buy == Decimal("5")
    assert msft_sell == Decimal("0")


@pytest.mark.unit
def test_open_buy_reserved_notional_and_missing_limit() -> None:
    portfolio = with_open_orders(
        make_cash_portfolio(cash="5000.00"),
        make_open_order(
            order_id="ord_buy_msft",
            side="BUY",
            quantity="2",
            limit_price="200.00",
            symbol="MSFT",
        ),
        make_open_order(
            order_id="ord_sell_aapl",
            side="SELL",
            quantity="1",
            limit_price="214.50",
            symbol="AAPL",
        ),
    )
    assert open_buy_reserved_notional(portfolio) == Decimal("400.00")

    missing_limit = with_open_orders(
        make_cash_portfolio(cash="5000.00"),
        make_open_order(
            order_id="ord_buy_no_limit",
            side="BUY",
            quantity="1",
            symbol="MSFT",
        ),
    )
    with pytest.raises(OpenBuyLimitError, match="missing limit_price") as exc_info:
        open_buy_reserved_notional(missing_limit)
    assert exc_info.value.order_id == "ord_buy_no_limit"


@pytest.mark.unit
def test_baseline_after_open_orders_adjusts_cash_and_qty() -> None:
    portfolio = with_open_orders(
        portfolio_snapshot_example(),  # cash 3000, 10 AAPL
        make_open_order(
            order_id="ord_buy_aapl",
            side="BUY",
            quantity="2",
            limit_price="100.00",
            symbol="AAPL",
        ),
        make_open_order(
            order_id="ord_sell_aapl",
            side="SELL",
            quantity="3",
            limit_price="214.50",
            symbol="AAPL",
        ),
        make_open_order(
            order_id="ord_buy_msft",
            side="BUY",
            quantity="1",
            limit_price="50.00",
            symbol="MSFT",
        ),
    )
    cash, qty_by_id = baseline_after_open_orders(portfolio)
    assert cash == Decimal("2750.00")  # 3000 - 2*100 - 1*50; sells do not credit cash
    assert qty_by_id["rh_inst_aapl_xnas"] == Decimal("9")  # 10 + 2 - 3
    assert qty_by_id["rh_inst_msft_xnas"] == Decimal("1")


@pytest.mark.unit
def test_open_buy_extra_market_value_marks_unfilled_buys() -> None:
    from ainvest.schemas.commitments import open_buy_extra_market_value

    portfolio = with_open_orders(
        portfolio_snapshot_example(),  # 10 AAPL filled
        make_open_order(
            order_id="ord_buy_aapl_a",
            side="BUY",
            quantity="3",
            limit_price="214.50",
            symbol="AAPL",
        ),
        make_open_order(
            order_id="ord_buy_aapl_b",
            side="BUY",
            quantity="2",
            limit_price="200.00",
            symbol="AAPL",
        ),
    )
    # Price first 4 of the 5 open-buy shares in FIFO order order.
    assert open_buy_extra_market_value(portfolio, "rh_inst_aapl_xnas", Decimal("4")) == Decimal(
        "843.50"
    )  # 3*214.50 + 1*200
    assert open_buy_extra_market_value(portfolio, "rh_inst_aapl_xnas", Decimal("0")) == Decimal("0")
