"""Pure fill-price and fee helpers for the paper broker."""

from __future__ import annotations

from decimal import Decimal

from ainvest.execution.paper.types import (
    BPS_DENOM,
    PaperCostModel,
    PaperMarketEvent,
    _money,
    _WorkingOrder,
)
from ainvest.schemas.common import OrderSide


def _adverse_fill_price(
    *,
    side: OrderSide,
    bid: Decimal,
    ask: Decimal,
    limit_price: Decimal,
    costs: PaperCostModel,
) -> Decimal:
    """Compute fill price from mid + explicit half-spread + slippage.

    BUY pays above mid; SELL receives below mid. The result is clipped to the
    limit so the fill never violates the limit order.
    """
    mid = _money((bid + ask) / Decimal("2"))
    adverse_bps = costs.half_spread_bps + costs.slippage_bps
    adverse_frac = adverse_bps / BPS_DENOM
    if side is OrderSide.BUY:
        raw = mid * (Decimal("1") + adverse_frac)
        # Never pay above the limit on a limit buy.
        return _money(min(raw, limit_price))
    raw = mid * (Decimal("1") - adverse_frac)
    # Never sell below the limit on a limit sell.
    return _money(max(raw, limit_price))


def _fee_for(*, notional: Decimal, costs: PaperCostModel) -> Decimal:
    return _money(notional * costs.fee_bps / BPS_DENOM)


def _is_marketable(working: _WorkingOrder, event: PaperMarketEvent) -> bool:
    limit = _money(working.proposal.limit_price)
    if working.proposal.side is OrderSide.BUY:
        return _money(event.ask) <= limit
    return _money(event.bid) >= limit
