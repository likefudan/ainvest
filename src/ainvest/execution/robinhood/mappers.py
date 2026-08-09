"""Normalize the first five pinned Robinhood read payloads (P06-T1, part 1)."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from ainvest.execution.robinhood.read_client import GatewayReadResult
from ainvest.execution.robinhood.read_models import (
    OPEN_ORDER_STATES,
    AccountRead,
    AccountsRead,
    BrokerageTradingType,
    BuyingPowerRead,
    EquityOrderState,
    EquityOrderType,
    EquityPositionRead,
    EquityQuoteRead,
    MarketHours,
    OpenOrderRead,
    OpenOrdersRead,
    PortfolioRead,
    PositionsRead,
    QuoteIneligibility,
    QuotesRead,
    RobinhoodAccountScope,
    RobinhoodReadEvidence,
    TimeInForce,
)
from ainvest.schemas.common import (
    DECIMAL_STRING_PATTERN,
    OrderSide,
    Provenance,
    Symbol,
    ensure_utc,
    parse_decimal,
)


class MappingErrorCode(StrEnum):
    WRONG_CAPABILITY = "wrong_capability"
    INVALID_SHAPE = "invalid_shape"
    INVALID_VALUE = "invalid_value"
    INCONSISTENT_DATA = "inconsistent_data"
    UNSUPPORTED_POSITION = "unsupported_position"


_SAFE_MESSAGES = {
    MappingErrorCode.WRONG_CAPABILITY: "read result capability does not match mapper",
    MappingErrorCode.INVALID_SHAPE: "read result has an invalid payload shape",
    MappingErrorCode.INVALID_VALUE: "read result contains an invalid value",
    MappingErrorCode.INCONSISTENT_DATA: "read result contains inconsistent data",
    MappingErrorCode.UNSUPPORTED_POSITION: "read result contains an unsupported position",
}


class RobinhoodMappingError(ValueError):
    """Sanitized mapper failure that never carries provider values or prose."""

    def __init__(self, code: MappingErrorCode) -> None:
        self.code = code
        super().__init__(_SAFE_MESSAGES[code])


def map_accounts(
    result: GatewayReadResult,
    *,
    received_at: datetime | str,
) -> AccountsRead:
    """Map ``get_accounts`` without retaining any account number."""

    def build() -> AccountsRead:
        data = _data(result, "get_accounts")
        rows = _nullable_sequence(data, "accounts")
        accounts: list[AccountRead] = []
        for raw in rows:
            row = _object(raw)
            agentic = _strict_bool(row, "agentic_allowed")
            state = _string(row, "state")
            deactivated = _strict_bool(row, "deactivated")
            permanently_deactivated = _strict_bool(row, "permanently_deactivated")
            scope = RobinhoodAccountScope.AGENTIC if agentic else RobinhoodAccountScope.UNAVAILABLE
            tradable = (
                scope is RobinhoodAccountScope.AGENTIC
                and state == "active"
                and not deactivated
                and not permanently_deactivated
            )
            accounts.append(
                AccountRead(
                    scope=scope,
                    trading_type=BrokerageTradingType(_string(row, "type")),
                    brokerage_account_type=_string(row, "brokerage_account_type"),
                    is_default=_strict_bool(row, "is_default"),
                    state=state,
                    deactivated=deactivated,
                    permanently_deactivated=permanently_deactivated,
                    unsettled_funds=_optional_decimal(row.get("unsettled_funds")),
                    tradable=tradable,
                )
            )
        return AccountsRead(accounts=tuple(accounts), evidence=_evidence(result, received_at))

    return _boundary(build)


def map_portfolio(
    result: GatewayReadResult,
    *,
    account_scope: RobinhoodAccountScope,
    received_at: datetime | str,
) -> PortfolioRead:
    """Map mixed-asset totals without coercing them to ``PortfolioSnapshot``."""

    def build() -> PortfolioRead:
        data = _data(result, "get_portfolio")
        buying_power = _object(data["buying_power"])
        currency = _string(data, "currency")
        return PortfolioRead(
            account_scope=account_scope,
            currency=currency,
            total_value=_decimal(data["total_value"]),
            cash=_decimal(data["cash"]),
            pending_deposits=_decimal(data["pending_deposits"]),
            equity_value=_decimal(data["equity_value"]),
            options_value=_decimal(data["options_value"]),
            futures_value=_decimal(data["futures_value"]),
            event_contracts_value=_decimal(data["event_contracts_value"]),
            crypto_value=_decimal(data["crypto_value"]),
            mutual_funds_value=_decimal(data["mutual_funds_value"]),
            fixed_income_value=_decimal(data["fixed_income_value"]),
            buying_power=BuyingPowerRead(
                amount=_decimal(buying_power["buying_power"]),
                unleveraged_amount=_decimal(buying_power["unleveraged_buying_power"]),
                intraday_amount=_optional_decimal(buying_power.get("intraday_buying_power")),
                off_intraday_amount=_optional_decimal(
                    buying_power.get("off_intraday_buying_power")
                ),
                currency=_string(buying_power, "display_currency"),
            ),
            evidence=_evidence(result, received_at),
        )

    return _boundary(build)


def map_equity_positions(
    result: GatewayReadResult,
    *,
    account_scope: RobinhoodAccountScope,
    received_at: datetime | str,
) -> PositionsRead:
    """Map long equity positions; short, boxed, empty, and unknown types fail."""

    def build() -> PositionsRead:
        data = _data(result, "get_equity_positions")
        positions: list[EquityPositionRead] = []
        for raw in _nullable_sequence(data, "positions"):
            row = _object(raw)
            position_type = _string(row, "type")
            if position_type != "long":
                raise RobinhoodMappingError(MappingErrorCode.UNSUPPORTED_POSITION)
            positions.append(
                EquityPositionRead(
                    symbol=_string(row, "symbol"),
                    quantity=_decimal(row["quantity"]),
                    intraday_quantity=_decimal(row["intraday_quantity"]),
                    average_buy_price=_optional_decimal(row.get("average_buy_price")),
                    shares_available_for_sells=_decimal(row["shares_available_for_sells"]),
                    shares_held_for_sells=_decimal(row["shares_held_for_sells"]),
                    shares_held_for_stock_grants=_decimal(row["shares_held_for_stock_grants"]),
                    shares_held_for_options_events=_decimal(row["shares_held_for_options_events"]),
                    shares_held_for_asset_transfer=_decimal(row["shares_held_for_asset_transfer"]),
                    shares_pending_from_options_events=_decimal(
                        row["shares_pending_from_options_events"]
                    ),
                )
            )
        return PositionsRead(
            account_scope=account_scope,
            positions=tuple(positions),
            has_more=_has_more(data),
            evidence=_evidence(result, received_at),
        )

    return _boundary(build)


def map_equity_quotes(
    result: GatewayReadResult,
    *,
    received_at: datetime | str,
    max_quote_age_seconds: int,
) -> QuotesRead:
    """Map regular-session quotes and fail closed via ``live_eligible``."""

    def build() -> QuotesRead:
        if isinstance(max_quote_age_seconds, bool) or not 1 <= max_quote_age_seconds <= 86_400:
            raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
        data = _data(result, "get_equity_quotes")
        received = ensure_utc(received_at)
        quotes: list[EquityQuoteRead] = []
        for raw in _nullable_sequence(data, "results"):
            item = _object(raw)
            quote = _object(item["quote"])
            symbol = _string(quote, "symbol")
            close = item.get("close")
            if close is not None and _string(_object(close), "symbol") != symbol:
                raise RobinhoodMappingError(MappingErrorCode.INCONSISTENT_DATA)

            bid = _zero_as_missing_price(quote.get("bid_price"))
            ask = _zero_as_missing_price(quote.get("ask_price"))
            bid_at = _optional_time(quote.get("venue_bid_time"))
            ask_at = _optional_time(quote.get("venue_ask_time"))
            last_at = _required_time(quote.get("venue_last_trade_time"))
            has_traded = _strict_bool(quote, "has_traded")
            listing_state = _string(quote, "state")

            reasons: list[QuoteIneligibility] = []
            if not has_traded:
                reasons.append(QuoteIneligibility.NO_TRADES)
            if listing_state != "active":
                reasons.append(QuoteIneligibility.INACTIVE_INSTRUMENT)
            if bid is None:
                reasons.append(QuoteIneligibility.MISSING_BID)
            if ask is None:
                reasons.append(QuoteIneligibility.MISSING_ASK)
            if bid_at is None:
                reasons.append(QuoteIneligibility.MISSING_BID_TIME)
            if ask_at is None:
                reasons.append(QuoteIneligibility.MISSING_ASK_TIME)
            if bid is not None and ask is not None and bid > ask:
                reasons.append(QuoteIneligibility.CROSSED_MARKET)

            times = [last_at, *(value for value in (bid_at, ask_at) if value is not None)]
            if any(value > received for value in times):
                reasons.append(QuoteIneligibility.FUTURE_TIMESTAMP)
            if any((received - value).total_seconds() > max_quote_age_seconds for value in times):
                reasons.append(QuoteIneligibility.STALE)

            quotes.append(
                EquityQuoteRead(
                    symbol=symbol,
                    last_price=_decimal(quote["last_trade_price"]),
                    last_at=last_at,
                    bid=bid,
                    bid_at=bid_at,
                    ask=ask,
                    ask_at=ask_at,
                    has_traded=has_traded,
                    listing_state=listing_state,
                    live_eligible=not reasons,
                    ineligibility=tuple(dict.fromkeys(reasons)),
                )
            )
        return QuotesRead(quotes=tuple(quotes), evidence=_evidence(result, received_at))

    return _boundary(build)


def map_open_equity_orders(
    result: GatewayReadResult,
    *,
    account_scope: RobinhoodAccountScope,
    received_at: datetime | str,
    expected_symbol: Symbol | None = None,
) -> OpenOrdersRead:
    """Map only the open subset of ``get_equity_orders``.

    Closed rows are counted but intentionally left to P06-T1 part 2, which
    owns richer order-history semantics.
    """

    def build() -> OpenOrdersRead:
        data = _data(result, "get_equity_orders")
        rows = _nullable_sequence(data, "orders")
        open_orders: list[OpenOrderRead] = []
        for raw in rows:
            row = _object(raw)
            state = EquityOrderState(_string(row, "state"))
            symbol = _string(row, "symbol")
            if expected_symbol is not None and symbol != expected_symbol:
                raise RobinhoodMappingError(MappingErrorCode.INCONSISTENT_DATA)
            if state not in OPEN_ORDER_STATES:
                continue
            open_orders.append(_open_order(row, state=state, symbol=symbol))
        return OpenOrdersRead(
            account_scope=account_scope,
            open_orders=tuple(open_orders),
            records_seen=len(rows),
            has_more=_has_more(data),
            evidence=_evidence(result, received_at),
        )

    return _boundary(build)


def _open_order(row: Mapping[str, Any], *, state: EquityOrderState, symbol: str) -> OpenOrderRead:
    dollar_raw = row.get("dollar_based_amount")
    dollar = None if dollar_raw is None else _object(dollar_raw)
    return OpenOrderRead(
        order_id=_string(row, "id"),
        instrument_id=_string(row, "instrument_id"),
        symbol=symbol,
        side=OrderSide(_string(row, "side").upper()),
        order_type=_order_type(_string(row, "type"), _string(row, "trigger")),
        state=state,
        quantity=_optional_decimal(row.get("quantity")),
        filled_quantity=_decimal(row["cumulative_quantity"]),
        dollar_amount=None if dollar is None else _decimal(dollar["amount"]),
        dollar_currency=None if dollar is None else _string(dollar, "currency_code"),
        limit_price=_optional_decimal(row.get("price")),
        stop_price=_optional_decimal(row.get("stop_price")),
        average_price=_optional_decimal(row.get("average_price")),
        fees=_decimal(row["fees"]),
        time_in_force=TimeInForce(_string(row, "time_in_force")),
        market_hours=MarketHours(_string(row, "market_hours")),
        created_at=_required_time(row.get("created_at")),
        last_transaction_at=_optional_time(row.get("last_transaction_at")),
    )


def _order_type(order_type: str, trigger: str) -> EquityOrderType:
    pair = (order_type, trigger)
    mapping = {
        ("market", "immediate"): EquityOrderType.MARKET,
        ("limit", "immediate"): EquityOrderType.LIMIT,
        ("market", "stop"): EquityOrderType.STOP_MARKET,
        ("limit", "stop"): EquityOrderType.STOP_LIMIT,
    }
    try:
        return mapping[pair]
    except KeyError:
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE) from None


def _boundary[T](build: Callable[[], T]) -> T:
    try:
        return build()
    except RobinhoodMappingError:
        raise
    except KeyError:
        raise RobinhoodMappingError(MappingErrorCode.INVALID_SHAPE) from None
    except (TypeError, ValueError, ValidationError):
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE) from None


def _data(result: GatewayReadResult, capability: str) -> Mapping[str, Any]:
    if result.capability != capability:
        raise RobinhoodMappingError(MappingErrorCode.WRONG_CAPABILITY)
    if set(result.payload) != {"data"}:
        raise RobinhoodMappingError(MappingErrorCode.INVALID_SHAPE)
    return _object(result.payload["data"])


def _evidence(result: GatewayReadResult, received_at: datetime | str) -> RobinhoodReadEvidence:
    return RobinhoodReadEvidence(
        provenance=Provenance(
            source="robinhood_mcp",
            observed_at=_required_time(result.observed_at),
            received_at=ensure_utc(received_at),
        ),
        manifest_digest=result.manifest_digest,
        schema_digest=result.schema_digest,
        result_digest=result.result_digest,
    )


def _object(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise RobinhoodMappingError(MappingErrorCode.INVALID_SHAPE)
    return value


def _nullable_sequence(data: Mapping[str, Any], key: str) -> Sequence[object]:
    value = data[key]
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise RobinhoodMappingError(MappingErrorCode.INVALID_SHAPE)
    return value


def _string(data: Mapping[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value or value != value.strip():
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return value


def _strict_bool(data: Mapping[str, Any], key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return value


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    if re.fullmatch(DECIMAL_STRING_PATTERN, value) is None:
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return parse_decimal(value)


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)


def _zero_as_missing_price(value: object) -> Decimal | None:
    parsed = _decimal(value)
    return None if parsed == 0 else parsed


def _required_time(value: object) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return ensure_utc(value)


def _optional_time(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    return _required_time(value)


def _has_more(data: Mapping[str, Any]) -> bool:
    value = data.get("next")
    if value is None or value == "":
        return False
    if not isinstance(value, str):
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return True


__all__ = [
    "MappingErrorCode",
    "RobinhoodMappingError",
    "map_accounts",
    "map_equity_positions",
    "map_equity_quotes",
    "map_open_equity_orders",
    "map_portfolio",
]
