"""Unit tests for the append-only portfolio ledger (P03-T15)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ainvest.portfolio.ledger import (
    LedgerApplyStatus,
    LedgerError,
    PortfolioLedger,
)
from ainvest.schemas.broker import BrokerFill
from ainvest.schemas.common import AssetType, InstrumentIdentity, OrderSide
from ainvest.schemas.portfolio import AccountScope

AS_OF = datetime(2026, 7, 27, 15, 0, 0, tzinfo=UTC)


def _instrument(instrument_id: str = "rh_inst_aapl") -> InstrumentIdentity:
    return InstrumentIdentity(
        instrument_id=instrument_id,
        symbol="AAPL",
        exchange="XNAS",
        currency="USD",
        asset_type=AssetType.EQUITY,
        identity_as_of=AS_OF,
    )


def _fill(
    *,
    fill_id: str,
    quantity: str,
    price: str,
    broker_order_id: str = "paper_client_1",
    filled_at: datetime | None = None,
) -> BrokerFill:
    return BrokerFill(
        fill_id=fill_id,
        broker_order_id=broker_order_id,
        quantity=Decimal(quantity),
        price=Decimal(price),
        filled_at=filled_at or AS_OF,
    )


@pytest.mark.unit
def test_opening_cash_and_conservation() -> None:
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("10000"),
        as_of=AS_OF,
    )
    report = ledger.assert_conservation()
    assert report.holds
    assert report.cash == Decimal("10000")
    assert report.realized_pnl == Decimal("0")


@pytest.mark.unit
def test_buy_and_sell_update_cash_positions_and_realized_pnl() -> None:
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("10000"),
        as_of=AS_OF,
    )
    instrument = _instrument()
    buy = ledger.apply_fill(
        _fill(fill_id="fill_buy_1", quantity="10", price="100"),
        side=OrderSide.BUY,
        instrument=instrument,
        fee=Decimal("1"),
    )
    assert buy.status is LedgerApplyStatus.APPLIED
    assert ledger.cash == Decimal("8999")  # 10000 - 1000 - 1
    assert ledger.position_quantity(instrument.instrument_id) == Decimal("10")

    sell = ledger.apply_fill(
        _fill(
            fill_id="fill_sell_1",
            quantity="4",
            price="110",
            filled_at=AS_OF + timedelta(minutes=1),
        ),
        side=OrderSide.SELL,
        instrument=instrument,
        fee=Decimal("1"),
    )
    assert sell.status is LedgerApplyStatus.APPLIED
    # proceeds = 440 - 1 = 439; cash = 8999 + 439 = 9438
    assert ledger.cash == Decimal("9438")
    assert ledger.position_quantity(instrument.instrument_id) == Decimal("6")
    # realized = (110 - 100) * 4 - 1 = 39
    assert ledger.realized_pnl == Decimal("39")
    ledger.assert_conservation()


@pytest.mark.unit
def test_duplicate_fill_is_idempotent() -> None:
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("5000"),
        as_of=AS_OF,
    )
    instrument = _instrument()
    fill = _fill(fill_id="fill_dup_1", quantity="2", price="50")
    first = ledger.apply_fill(fill, side=OrderSide.BUY, instrument=instrument, fee="0")
    cash_after = ledger.cash
    second = ledger.apply_fill(fill, side=OrderSide.BUY, instrument=instrument, fee="0")
    assert first.status is LedgerApplyStatus.APPLIED
    assert second.status is LedgerApplyStatus.DUPLICATE
    assert ledger.cash == cash_after
    assert ledger.position_quantity(instrument.instrument_id) == Decimal("2")
    ledger.assert_conservation()


@pytest.mark.unit
def test_out_of_order_and_late_fills_sorted_ingest() -> None:
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("5000"),
        as_of=AS_OF,
    )
    instrument = _instrument()
    early = _fill(
        fill_id="fill_late_a",
        quantity="1",
        price="100",
        filled_at=AS_OF + timedelta(seconds=10),
    )
    later = _fill(
        fill_id="fill_late_b",
        quantity="1",
        price="101",
        filled_at=AS_OF + timedelta(seconds=5),
    )
    # Intentionally pass late-before-early; helper sorts by filled_at.
    results = ledger.apply_fills_sorted(
        [
            (early, OrderSide.BUY, instrument, Decimal("0")),
            (later, OrderSide.BUY, instrument, Decimal("0")),
        ]
    )
    assert [item.status for item in results] == [
        LedgerApplyStatus.APPLIED,
        LedgerApplyStatus.APPLIED,
    ]
    # Average cost should reflect chronological application: first 101, then 100.
    _identity, qty, avg = ledger.positions()[instrument.instrument_id]
    assert qty == Decimal("2")
    assert avg == Decimal("100.5")
    ledger.assert_conservation()


@pytest.mark.unit
def test_insufficient_cash_rejects_without_mutation() -> None:
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("50"),
        as_of=AS_OF,
    )
    result = ledger.apply_fill(
        _fill(fill_id="fill_overdraft", quantity="1", price="100"),
        side=OrderSide.BUY,
        instrument=_instrument(),
    )
    assert result.status is LedgerApplyStatus.REJECTED
    assert result.reason_code == "INSUFFICIENT_CASH"
    assert ledger.cash == Decimal("50")
    assert ledger.positions() == {}


@pytest.mark.unit
def test_snapshot_includes_unrealized_pnl() -> None:
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("1000"),
        as_of=AS_OF,
    )
    instrument = _instrument()
    ledger.apply_fill(
        _fill(fill_id="fill_snap_1", quantity="2", price="100"),
        side=OrderSide.BUY,
        instrument=instrument,
    )
    snap = ledger.build_snapshot(
        snapshot_id="snap_01HZYLEDGER000001",
        marks={instrument.instrument_id: Decimal("110")},
    )
    assert snap.cash == Decimal("800")
    assert len(snap.positions) == 1
    assert snap.positions[0].unrealized_pnl == Decimal("20")
    assert snap.equity == Decimal("1020")
    assert snap.exposure.gross_market_value == Decimal("220")


@pytest.mark.unit
def test_negative_opening_cash_fails_closed() -> None:
    with pytest.raises(LedgerError):
        PortfolioLedger(
            account_scope=AccountScope.PAPER,
            currency="USD",
            opening_cash=Decimal("-1"),
            as_of=AS_OF,
        )
