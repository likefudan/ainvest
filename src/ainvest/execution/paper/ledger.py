"""Cash and position-book mutation helpers for the paper broker."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from ainvest.execution.broker import BrokerRejectedError
from ainvest.execution.paper.types import (
    ZERO,
    PaperRejectReason,
    _money,
    _PositionBook,
    _WorkingOrder,
)
from ainvest.schemas.broker import BrokerOrderStatus
from ainvest.schemas.common import InstrumentIdentity, OrderSide, canonicalize_decimal


def _open_working_orders(orders: Mapping[str, _WorkingOrder]) -> tuple[_WorkingOrder, ...]:
    return tuple(
        wo
        for wo in orders.values()
        if wo.broker_order.status
        in {BrokerOrderStatus.ACCEPTED, BrokerOrderStatus.PARTIALLY_FILLED}
        and wo.remaining > ZERO
    )


def _available_cash(cash: Decimal, orders: Mapping[str, _WorkingOrder]) -> Decimal:
    reserved = sum((wo.reserved_cash for wo in _open_working_orders(orders)), ZERO)
    return canonicalize_decimal(cash - reserved)


def _position_qty(positions: Mapping[str, _PositionBook], instrument_id: str) -> Decimal:
    book = positions.get(instrument_id)
    return book.quantity if book is not None else ZERO


def _reserved_sell_qty(orders: Mapping[str, _WorkingOrder], instrument_id: str) -> Decimal:
    total = ZERO
    for wo in orders.values():
        if (
            wo.proposal.instrument_id == instrument_id
            and wo.proposal.side is OrderSide.SELL
            and wo.broker_order.status
            in {BrokerOrderStatus.ACCEPTED, BrokerOrderStatus.PARTIALLY_FILLED}
        ):
            total += wo.reserved_qty
    return canonicalize_decimal(total)


def _release_reserves(working: _WorkingOrder) -> None:
    # Reserves are virtual; releasing is clearing working-order fields.
    # Cash itself is only mutated on fills (BUY debit / SELL credit).
    del working  # reserves cleared by caller assignment


def _credit_position(
    positions: dict[str, _PositionBook],
    instrument: InstrumentIdentity,
    *,
    quantity: Decimal,
    price: Decimal,
) -> None:
    book = positions.get(instrument.instrument_id)
    if book is None:
        positions[instrument.instrument_id] = _PositionBook(
            instrument=instrument,
            quantity=quantity,
            average_cost=price,
        )
        return
    new_qty = canonicalize_decimal(book.quantity + quantity)
    if new_qty == ZERO:
        positions.pop(instrument.instrument_id, None)
        return
    new_cost = _money((book.average_cost * book.quantity + price * quantity) / new_qty)
    book.quantity = new_qty
    book.average_cost = new_cost
    book.instrument = instrument


def _debit_position(
    positions: dict[str, _PositionBook],
    instrument_id: str,
    *,
    quantity: Decimal,
) -> None:
    book = positions[instrument_id]
    new_qty = canonicalize_decimal(book.quantity - quantity)
    if new_qty < ZERO:
        raise BrokerRejectedError(
            "paper position went negative",
            reason_code=PaperRejectReason.INSUFFICIENT_POSITION.value,
        )
    if new_qty == ZERO:
        positions.pop(instrument_id, None)
    else:
        book.quantity = new_qty
