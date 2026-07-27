"""Shared portfolio / open-order builders for unit tests."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

from ainvest.schemas.examples import portfolio_snapshot_example
from ainvest.schemas.portfolio import PortfolioSnapshot

DEFAULT_AS_OF = "2026-07-24T18:30:00Z"
DEFAULT_SUBMITTED_AT = "2026-07-24T18:29:00Z"

AAPL_INSTRUMENT: dict[str, Any] = {
    "instrument_id": "rh_inst_aapl_xnas",
    "symbol": "AAPL",
    "exchange": "XNAS",
    "currency": "USD",
    "asset_type": "EQUITY",
    "identity_as_of": DEFAULT_AS_OF,
}

MSFT_INSTRUMENT: dict[str, Any] = {
    "instrument_id": "rh_inst_msft_xnas",
    "symbol": "MSFT",
    "exchange": "XNAS",
    "currency": "USD",
    "asset_type": "EQUITY",
    "identity_as_of": DEFAULT_AS_OF,
}

_KNOWN_INSTRUMENTS: dict[str, dict[str, Any]] = {
    "AAPL": AAPL_INSTRUMENT,
    "MSFT": MSFT_INSTRUMENT,
}


def _as_decimal_str(value: str | Decimal) -> str:
    return str(value) if isinstance(value, Decimal) else value


def make_instrument(
    *,
    symbol: str = "AAPL",
    instrument_id: str | None = None,
    exchange: str = "XNAS",
    currency: str = "USD",
    asset_type: str = "EQUITY",
    identity_as_of: str = DEFAULT_AS_OF,
    **overrides: Any,
) -> dict[str, Any]:
    """Build an instrument identity dict for portfolio / open-order payloads."""
    known = _KNOWN_INSTRUMENTS.get(symbol)
    payload = {
        "instrument_id": instrument_id
        or (known["instrument_id"] if known is not None else f"rh_inst_{symbol.lower()}_xnas"),
        "symbol": symbol,
        "exchange": exchange,
        "currency": currency,
        "asset_type": asset_type,
        "identity_as_of": identity_as_of,
    }
    payload.update(overrides)
    return payload


def make_cash_portfolio(
    *,
    cash: str | Decimal,
    buying_power: str | Decimal | None = None,
    account_scope: str = "paper",
    equity: str | Decimal | None = None,
    **overrides: Any,
) -> PortfolioSnapshot:
    """Cash-only portfolio snapshot (no positions) with synced exposure."""
    cash_s = _as_decimal_str(cash)
    buying_power_s = cash_s if buying_power is None else _as_decimal_str(buying_power)
    equity_s = cash_s if equity is None else _as_decimal_str(equity)
    payload: dict[str, Any] = {
        **portfolio_snapshot_example(),
        "account_scope": account_scope,
        "cash": cash_s,
        "buying_power": buying_power_s,
        "equity": equity_s,
        "positions": [],
        "open_orders": [],
        "exposure": {
            "cash": cash_s,
            "equity": equity_s,
            "gross_market_value": "0",
            "net_market_value": "0",
            "largest_position_weight": "0",
            "position_count": 0,
        },
    }
    payload.update(overrides)
    return PortfolioSnapshot.model_validate(payload)


def make_open_order(
    *,
    order_id: str,
    side: str,
    quantity: str | Decimal,
    limit_price: str | Decimal | None = None,
    instrument: dict[str, Any] | None = None,
    symbol: str | None = None,
    submitted_at: str = DEFAULT_SUBMITTED_AT,
    **overrides: Any,
) -> dict[str, Any]:
    """Open-order dict suitable for ``PortfolioSnapshot.open_orders`` payloads."""
    if instrument is None:
        resolved_symbol = symbol or "AAPL"
        instrument = make_instrument(symbol=resolved_symbol)
    else:
        instrument = dict(instrument)
        resolved_symbol = symbol or str(instrument["symbol"])
    order: dict[str, Any] = {
        "order_id": order_id,
        "instrument": instrument,
        "side": side,
        "quantity": _as_decimal_str(quantity),
        "submitted_at": submitted_at,
        "symbol": resolved_symbol,
    }
    if limit_price is not None:
        order["limit_price"] = _as_decimal_str(limit_price)
    order.update(overrides)
    return order


def with_open_orders(
    portfolio_or_payload: PortfolioSnapshot | dict[str, Any],
    *orders: dict[str, Any],
) -> PortfolioSnapshot:
    """Return a snapshot with ``orders`` appended to ``open_orders``."""
    if isinstance(portfolio_or_payload, PortfolioSnapshot):
        payload = portfolio_or_payload.model_dump(mode="python")
    else:
        payload = deepcopy(portfolio_or_payload)
    existing = list(payload.get("open_orders") or [])
    existing.extend(orders)
    payload["open_orders"] = existing
    return PortfolioSnapshot.model_validate(payload)


__all__ = [
    "AAPL_INSTRUMENT",
    "DEFAULT_AS_OF",
    "DEFAULT_SUBMITTED_AT",
    "MSFT_INSTRUMENT",
    "make_cash_portfolio",
    "make_instrument",
    "make_open_order",
    "with_open_orders",
]
