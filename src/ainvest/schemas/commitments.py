"""Open-order commitment math shared by sizing and risk exposure.

Paper and risk both need the same fail-closed view of reserved buys and
effective quantities after working orders. Helpers here stay schema-layer
pure: no risk outcomes, no sizing reason codes.
"""

from __future__ import annotations

from decimal import Decimal

from ainvest.schemas.common import OrderSide, canonicalize_decimal, parse_decimal
from ainvest.schemas.portfolio import PortfolioSnapshot

ZERO = Decimal("0")


class OpenBuyLimitError(ValueError):
    """Raised when an open BUY cannot reserve capital without a usable limit."""

    def __init__(self, *, order_id: str, reason: str) -> None:
        self.order_id = order_id
        self.reason = reason
        super().__init__(f"{reason} (order_id={order_id})")


def open_order_side_quantities(
    portfolio: PortfolioSnapshot, instrument_id: str
) -> tuple[Decimal, Decimal]:
    """Return ``(open_buy_qty, open_sell_qty)`` for one instrument."""
    buy_qty = ZERO
    sell_qty = ZERO
    for order in portfolio.open_orders:
        if order.instrument.instrument_id != instrument_id:
            continue
        qty = parse_decimal(order.quantity)
        if order.side is OrderSide.BUY:
            buy_qty = canonicalize_decimal(buy_qty + qty)
        elif order.side is OrderSide.SELL:
            sell_qty = canonicalize_decimal(sell_qty + qty)
    return buy_qty, sell_qty


def open_buy_reserved_notional(portfolio: PortfolioSnapshot) -> Decimal:
    """Sum open BUY ``qty * limit_price``.

    Raises :class:`OpenBuyLimitError` when any open BUY lacks a positive limit.
    """
    reserved = ZERO
    for order in portfolio.open_orders:
        if order.side is not OrderSide.BUY:
            continue
        limit = _require_positive_buy_limit(order.order_id, order.limit_price)
        reserved = canonicalize_decimal(reserved + parse_decimal(order.quantity) * limit)
    return reserved


def baseline_after_open_orders(
    portfolio: PortfolioSnapshot,
) -> tuple[Decimal, dict[str, Decimal]]:
    """Cash and per-instrument qty after committing open orders.

    Open BUYs reserve cash at ``qty * limit_price`` and increase effective qty.
    Open SELLs reduce effective qty (cash is not credited until fill).

    Raises :class:`OpenBuyLimitError` when an open BUY lacks a positive limit.
    """
    cash = canonicalize_decimal(portfolio.cash)
    qty_by_id: dict[str, Decimal] = {
        position.instrument.instrument_id: canonicalize_decimal(position.quantity)
        for position in portfolio.positions
    }
    for order in portfolio.open_orders:
        instrument_id = order.instrument.instrument_id
        qty = canonicalize_decimal(order.quantity)
        if order.side is OrderSide.BUY:
            limit = _require_positive_buy_limit(order.order_id, order.limit_price)
            cash = canonicalize_decimal(cash - qty * limit)
            qty_by_id[instrument_id] = canonicalize_decimal(
                qty_by_id.get(instrument_id, ZERO) + qty
            )
        else:
            qty_by_id[instrument_id] = canonicalize_decimal(
                qty_by_id.get(instrument_id, ZERO) - qty
            )
    return cash, qty_by_id


def open_buy_extra_market_value(
    portfolio: PortfolioSnapshot,
    instrument_id: str,
    extra_qty: Decimal,
) -> Decimal:
    """Mark open BUY quantity beyond filled size at each order's limit price.

    ``extra_qty`` is ``effective_qty - filled_qty`` for ``instrument_id``.
    Caller must have already validated open BUY limits (e.g. via
    :func:`baseline_after_open_orders`).
    """
    remaining = canonicalize_decimal(extra_qty)
    if remaining <= ZERO:
        return ZERO
    market_value = ZERO
    for order in portfolio.open_orders:
        if order.side is not OrderSide.BUY:
            continue
        if order.instrument.instrument_id != instrument_id:
            continue
        # Limits already validated by baseline_after_open_orders / reserved notional.
        limit = _require_positive_buy_limit(order.order_id, order.limit_price)
        take = min(remaining, canonicalize_decimal(order.quantity))
        market_value = canonicalize_decimal(market_value + take * limit)
        remaining = canonicalize_decimal(remaining - take)
        if remaining <= ZERO:
            break
    return market_value


def _require_positive_buy_limit(order_id: str, limit_price: Decimal | None) -> Decimal:
    if limit_price is None:
        raise OpenBuyLimitError(
            order_id=order_id,
            reason="open BUY missing limit_price (cannot project commitments)",
        )
    try:
        limit = parse_decimal(limit_price)
    except ValueError as exc:
        raise OpenBuyLimitError(
            order_id=order_id,
            reason="open BUY limit_price is not a usable decimal",
        ) from exc
    if limit <= ZERO:
        raise OpenBuyLimitError(
            order_id=order_id,
            reason="open BUY limit_price must be positive",
        )
    return canonicalize_decimal(limit)


__all__ = [
    "OpenBuyLimitError",
    "baseline_after_open_orders",
    "open_buy_extra_market_value",
    "open_buy_reserved_notional",
    "open_order_side_quantities",
]
