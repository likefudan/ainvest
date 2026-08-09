"""Normalize the pinned Robinhood read projection into provider-independent models."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from ainvest.data.models import PriceLevel
from ainvest.execution.robinhood.read_client import GatewayReadResult
from ainvest.execution.robinhood.read_models import (
    OPEN_ORDER_STATES,
    UNAVAILABLE_UNTRUSTED_TEXT,
    AccountRead,
    AccountsRead,
    AccountTypeTradability,
    AccountTypeTradabilityRead,
    AllDayTradability,
    BrokerageTradingType,
    BuyingPowerRead,
    ClosedOrderRead,
    ClosedOrdersRead,
    EquityExecutionRead,
    EquityOrderState,
    EquityOrderType,
    EquityPositionRead,
    EquityQuoteRead,
    EquityTradabilityRead,
    FinancialMetric,
    FinancialPeriodRead,
    FinancialSeriesRead,
    FinancialsRead,
    FundamentalBounds,
    FundamentalRead,
    FundamentalsRead,
    HaltSession,
    HistoricalBarRead,
    HistoricalBounds,
    HistoricalInterval,
    HistoricalSeriesRead,
    HistoricalSession,
    HistoricalsRead,
    MarketHours,
    NamedUntrustedText,
    NormalizedUnit,
    OpenOrderRead,
    OpenOrdersRead,
    PartialInstrumentReference,
    PortfolioRead,
    PositionsRead,
    PriceBookEntryRead,
    PriceBookFailureRead,
    PriceBooksRead,
    QuoteIneligibility,
    QuotesRead,
    ReportingPeriod,
    RobinhoodAccountScope,
    RobinhoodReadEvidence,
    ShortSellingTradability,
    TimeInForce,
    TradabilitiesRead,
    TradabilityState,
    TwentyFourSevenTradability,
    UntrustedDisplayText,
)
from ainvest.schemas.common import (
    DECIMAL_STRING_PATTERN,
    OrderSide,
    Provenance,
    QualityFlag,
    Symbol,
    ensure_utc,
    parse_decimal,
)
from ainvest.schemas.market import FactValueKind, FundamentalFact, FundamentalSnapshot


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
    received_at: datetime | str,
) -> PortfolioRead:
    """Map unbound mixed-asset totals without asserting an account identity."""

    def build() -> PortfolioRead:
        data = _data(result, "get_portfolio")
        buying_power = _object(data["buying_power"])
        currency = _string(data, "currency")
        return PortfolioRead(
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
    received_at: datetime | str,
) -> PositionsRead:
    """Map unbound long positions; unsupported position types fail closed."""

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
    """Map display quotes; keep live use disabled until session proof exists."""

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

            # The provider says this is a regular-hours print, but it supplies
            # no exchange-calendar proof for the receipt time. P06-T1 part 1
            # therefore maps it for display while failing closed for live use.
            reasons: list[QuoteIneligibility] = [QuoteIneligibility.SESSION_UNVERIFIED]
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
            open_orders=tuple(open_orders),
            records_seen=len(rows),
            has_more=_has_more(data),
            evidence=_evidence(result, received_at),
        )

    return _boundary(build)


def map_equity_price_books(
    result: GatewayReadResult,
    *,
    received_at: datetime | str,
    expected_symbols: Sequence[Symbol],
) -> PriceBooksRead:
    """Map symbol-keyed books without claiming canonical instrument identity."""

    def build() -> PriceBooksRead:
        expected = _expected_symbols(expected_symbols)
        data = _data(result, "get_equity_price_book")
        omitted: list[str] = []
        books: list[PriceBookEntryRead] = []
        errors: list[PriceBookFailureRead] = []
        for raw in _nullable_sequence(data, "books"):
            row = _object(raw)
            symbol = _string(row, "symbol")
            books.append(
                PriceBookEntryRead(
                    instrument=PartialInstrumentReference(symbol=symbol),
                    updated_at=_required_time(row.get("updated_at")),
                    bids=_price_levels(row, "bids"),
                    asks=_price_levels(row, "asks"),
                )
            )
        raw_errors = _nullable_sequence(data, "errors") if "errors" in data else ()
        for index, raw in enumerate(raw_errors):
            row = _object(raw)
            errors.append(
                PriceBookFailureRead(
                    symbol=_string(row, "symbol"),
                    error=_untrusted(row.get("error"), f"errors[{index}].error", omitted),
                )
            )
        actual = tuple(book.instrument.symbol for book in books) + tuple(e.symbol for e in errors)
        _require_expected_symbol_partition(actual, expected)
        return PriceBooksRead(
            books=tuple(books),
            errors=tuple(errors),
            omitted_untrusted_fields=tuple(omitted),
            evidence=_evidence(result, received_at),
        )

    return _boundary(build)


def map_equity_tradability(
    result: GatewayReadResult,
    *,
    received_at: datetime | str,
    expected_symbols: Sequence[Symbol],
) -> TradabilitiesRead:
    """Map account-unbound tradability flags; session tags remain unverified."""

    def build() -> TradabilitiesRead:
        expected = _expected_symbols(expected_symbols)
        data = _data(result, "get_equity_tradability")
        omitted: list[str] = []
        rows: list[EquityTradabilityRead] = []
        for index, raw in enumerate(_nullable_sequence(data, "results")):
            row = _object(raw)
            rows.append(
                EquityTradabilityRead(
                    instrument=PartialInstrumentReference(symbol=_string(row, "symbol")),
                    name=_optional_untrusted(row.get("name"), f"results[{index}].name", omitted),
                    simple_name=_optional_untrusted(
                        row.get("simple_name"), f"results[{index}].simple_name", omitted
                    ),
                    state=_optional_enum(row.get("state"), TradabilityState),
                    country=_optional_string(row.get("country")),
                    tradeable=_strict_bool(row, "tradeable"),
                    fractional_tradability=_optional_enum(
                        row.get("fractional_tradability"), AccountTypeTradability
                    ),
                    extended_hours_fractional_tradability=_strict_bool(
                        row, "extended_hours_fractional_tradability"
                    ),
                    all_day_tradability=_optional_enum(
                        row.get("all_day_tradability"), AllDayTradability
                    ),
                    twenty_four_seven_tradability=_optional_enum(
                        row.get("twenty_four_seven_tradability"),
                        TwentyFourSevenTradability,
                    ),
                    short_selling_tradability=_optional_enum(
                        row.get("short_selling_tradability"),
                        ShortSellingTradability,
                    ),
                    internal_halt_reason=_optional_untrusted(
                        row.get("internal_halt_reason"),
                        f"results[{index}].internal_halt_reason",
                        omitted,
                    ),
                    internal_halt_details=_optional_untrusted(
                        row.get("internal_halt_details"),
                        f"results[{index}].internal_halt_details",
                        omitted,
                    ),
                    internal_halt_sessions=tuple(
                        HaltSession(_plain_string(value))
                        for value in _nullable_sequence(row, "internal_halt_sessions")
                    )
                    if "internal_halt_sessions" in row
                    else (),
                    internal_halt_start_time=_optional_time(row.get("internal_halt_start_time")),
                    internal_halt_end_time=_optional_time(row.get("internal_halt_end_time")),
                    account_type_tradabilities=tuple(
                        AccountTypeTradabilityRead(
                            account_type=_string(item, "account_type"),
                            tradability=AccountTypeTradability(
                                _string(item, "account_type_tradability")
                            ),
                        )
                        for item in map(
                            _object,
                            _nullable_sequence(row, "account_type_tradabilities")
                            if "account_type_tradabilities" in row
                            else (),
                        )
                    ),
                )
            )
        not_found = (
            tuple(_plain_string(value) for value in _nullable_sequence(data, "not_found"))
            if "not_found" in data
            else ()
        )
        actual = tuple(row.instrument.symbol for row in rows) + not_found
        _require_expected_symbol_partition(actual, expected)
        return TradabilitiesRead(
            tradabilities=tuple(rows),
            unavailable_symbols=not_found,
            omitted_untrusted_fields=tuple(omitted),
            evidence=_evidence(result, received_at),
        )

    return _boundary(build)


def map_equity_historicals(
    result: GatewayReadResult,
    *,
    received_at: datetime | str,
    expected_symbols: Sequence[Symbol],
    expected_interval: HistoricalInterval | None = None,
    expected_bounds: HistoricalBounds | None = None,
) -> HistoricalsRead:
    """Map historical series while retaining provider interval/session semantics."""

    def build() -> HistoricalsRead:
        expected = _expected_symbols(expected_symbols)
        data = _data(result, "get_equity_historicals")
        series: list[HistoricalSeriesRead] = []
        for raw in _nullable_sequence(data, "results"):
            row = _object(raw)
            interval = HistoricalInterval(_string(row, "interval"))
            bounds = HistoricalBounds(_string(row, "bounds"))
            if expected_interval is not None and interval is not expected_interval:
                raise RobinhoodMappingError(MappingErrorCode.INCONSISTENT_DATA)
            if expected_bounds is not None and bounds is not expected_bounds:
                raise RobinhoodMappingError(MappingErrorCode.INCONSISTENT_DATA)
            bars: list[HistoricalBarRead] = []
            for raw_bar in _nullable_sequence(row, "bars"):
                bar = _object(raw_bar)
                session_raw = bar.get("session")
                bars.append(
                    HistoricalBarRead(
                        begins_at=_required_time(bar.get("begins_at")),
                        open=_decimal(bar["open_price"]),
                        high=_decimal(bar["high_price"]),
                        low=_decimal(bar["low_price"]),
                        close=_decimal(bar["close_price"]),
                        volume=_nonnegative_integer(bar["volume"]),
                        session=None
                        if session_raw in (None, "")
                        else HistoricalSession(_plain_string(session_raw)),
                        interpolated=None
                        if "interpolated" not in bar
                        else _strict_bool(bar, "interpolated"),
                    )
                )
            series.append(
                HistoricalSeriesRead(
                    instrument=PartialInstrumentReference(symbol=_string(row, "symbol")),
                    interval=interval,
                    bounds=bounds,
                    bars=tuple(bars),
                )
            )
        not_found = (
            tuple(_plain_string(value) for value in _nullable_sequence(data, "not_found"))
            if "not_found" in data
            else ()
        )
        _require_expected_symbol_partition(
            tuple(item.instrument.symbol for item in series) + not_found, expected
        )
        return HistoricalsRead(
            series=tuple(series),
            unavailable_symbols=not_found,
            evidence=_evidence(result, received_at),
        )

    return _boundary(build)


_FUNDAMENTAL_DECIMAL_UNITS = {
    "open": NormalizedUnit.UNSPECIFIED,
    "high": NormalizedUnit.UNSPECIFIED,
    "low": NormalizedUnit.UNSPECIFIED,
    "volume": NormalizedUnit.SHARES,
    "overnight_volume": NormalizedUnit.SHARES,
    "average_volume_2_weeks": NormalizedUnit.SHARES,
    "average_volume": NormalizedUnit.SHARES,
    "average_volume_30_days": NormalizedUnit.SHARES,
    "high_52_weeks": NormalizedUnit.UNSPECIFIED,
    "low_52_weeks": NormalizedUnit.UNSPECIFIED,
    "float": NormalizedUnit.SHARES,
    "market_cap": NormalizedUnit.USD,
    "pb_ratio": NormalizedUnit.RATIO,
    "pe_ratio": NormalizedUnit.RATIO,
    "shares_outstanding": NormalizedUnit.SHARES,
    "dividend_yield": NormalizedUnit.PERCENT,
    "dividend_per_share": NormalizedUnit.UNSPECIFIED,
    "thirty_day_sec_yield": NormalizedUnit.PERCENT,
}
_FUNDAMENTAL_POSITIVE_FIELDS = {
    "open",
    "high",
    "low",
    "high_52_weeks",
    "low_52_weeks",
}
_FUNDAMENTAL_SIGNED_FIELDS = {"pb_ratio", "pe_ratio"}
_FUNDAMENTAL_DATE_FIELDS = (
    "high_52_weeks_date",
    "low_52_weeks_date",
    "payable_date",
    "ex_dividend_date",
    "record_date",
)
_FUNDAMENTAL_DISPLAY_FIELDS = (
    "distribution_frequency",
    "description",
    "ceo",
    "headquarters_city",
    "headquarters_state",
    "sector",
    "industry",
    "financial_status_description",
)


def map_equity_fundamentals(
    result: GatewayReadResult,
    *,
    received_at: datetime | str,
    expected_symbols: Sequence[Symbol],
) -> FundamentalsRead:
    """Map manifest-backed facts and isolate provider-authored display text."""

    def build() -> FundamentalsRead:
        expected = _expected_symbols(expected_symbols)
        data = _data(result, "get_equity_fundamentals")
        evidence = _evidence(result, received_at)
        omitted: list[str] = []
        items: list[FundamentalRead] = []
        for index, raw in enumerate(_nullable_sequence(data, "results")):
            row = _object(raw)
            symbol = _string(row, "symbol")
            facts: list[FundamentalFact] = []
            for key, unit in _FUNDAMENTAL_DECIMAL_UNITS.items():
                if row.get(key) is not None:
                    if key in _FUNDAMENTAL_POSITIVE_FIELDS:
                        value = _positive_decimal(row[key])
                    elif key in _FUNDAMENTAL_SIGNED_FIELDS:
                        value = _decimal(row[key])
                    else:
                        value = _nonnegative_decimal(row[key])
                    facts.append(
                        FundamentalFact(
                            key=key,
                            kind=FactValueKind.DECIMAL,
                            decimal_value=value,
                            unit=unit.value,
                        )
                    )
            for key in _FUNDAMENTAL_DATE_FIELDS:
                if row.get(key) is not None:
                    facts.append(
                        FundamentalFact(
                            key=key,
                            kind=FactValueKind.TEXT,
                            text_value=_required_date(row[key]).isoformat(),
                            unit="DATE",
                        )
                    )
            if row.get("financial_status_indicator") is not None:
                facts.append(
                    FundamentalFact(
                        key="financial_status_indicator",
                        kind=FactValueKind.TEXT,
                        text_value=_fundamental_status_code(row["financial_status_indicator"]),
                        unit="CODE",
                    )
                )
            integer_facts = (
                ("num_employees", NormalizedUnit.PEOPLE),
                ("year_founded", NormalizedUnit.YEAR),
            )
            for key, unit in integer_facts:
                if row.get(key) is not None:
                    facts.append(
                        FundamentalFact(
                            key=key,
                            kind=FactValueKind.DECIMAL,
                            decimal_value=_nonnegative_integer(row[key]),
                            unit=unit.value,
                        )
                    )
            display: list[NamedUntrustedText] = []
            for key in _FUNDAMENTAL_DISPLAY_FIELDS:
                # P06-T0 intentionally removes every mapping key literally
                # named `description`; represent that discard explicitly.
                if key == "description":
                    raw_text = None
                else:
                    raw_text = row[key]
                    if raw_text is None:
                        continue
                display.append(
                    NamedUntrustedText(
                        field=key,
                        text=_untrusted(raw_text, f"results[{index}].{key}", omitted),
                    )
                )
            snapshot = FundamentalSnapshot(
                symbol=symbol,
                as_of=evidence.provenance.received_at,
                facts=tuple(facts),
                provenance=Provenance(
                    source=evidence.provenance.source,
                    observed_at=evidence.provenance.observed_at,
                    received_at=evidence.provenance.received_at,
                    quality_flags=()
                    if facts
                    else (QualityFlag.MISSING_FIELDS, QualityFlag.PARTIAL),
                ),
            )
            items.append(
                FundamentalRead(
                    instrument=PartialInstrumentReference(symbol=symbol),
                    bounds=FundamentalBounds(_string(row, "bounds")),
                    market_date=None
                    if row.get("market_date") is None
                    else _required_date(row["market_date"]),
                    snapshot=snapshot,
                    non_comparable_fact_keys=tuple(
                        fact.key for fact in facts if fact.unit == NormalizedUnit.UNSPECIFIED
                    ),
                    display_text=tuple(display),
                )
            )
        not_found = (
            tuple(_plain_string(value) for value in _nullable_sequence(data, "not_found"))
            if "not_found" in data
            else ()
        )
        _require_expected_symbol_partition(
            tuple(item.instrument.symbol for item in items) + not_found, expected
        )
        return FundamentalsRead(
            fundamentals=tuple(items),
            unavailable_symbols=not_found,
            omitted_untrusted_fields=tuple(omitted),
            evidence=evidence,
        )

    return _boundary(build)


def map_financials(
    result: GatewayReadResult,
    *,
    received_at: datetime | str,
    expected_symbols: Sequence[Symbol],
    expected_period: ReportingPeriod | None = None,
) -> FinancialsRead:
    """Map positional financial results with explicit unspecified currency."""

    def build() -> FinancialsRead:
        expected = _expected_symbols(expected_symbols)
        data = _data(result, "get_financials")
        raw_results = _nullable_sequence(data, "results")
        if len(raw_results) != len(expected):
            raise RobinhoodMappingError(MappingErrorCode.INCONSISTENT_DATA)
        results: list[FinancialSeriesRead] = []
        unavailable: list[Symbol] = []
        for index, raw in enumerate(raw_results):
            expected_symbol = expected[index]
            if raw is None:
                unavailable.append(expected_symbol)
                continue
            row = _object(raw)
            if _string(row, "symbol") != expected_symbol:
                raise RobinhoodMappingError(MappingErrorCode.INCONSISTENT_DATA)
            period = ReportingPeriod(_string(row, "period"))
            if expected_period is not None and period is not expected_period:
                raise RobinhoodMappingError(MappingErrorCode.INCONSISTENT_DATA)
            periods: list[FinancialPeriodRead] = []
            for raw_period in _nullable_sequence(row, "financials"):
                item = _object(raw_period)
                metrics: list[FinancialMetric] = []
                for key in ("revenue", "gross_profit", "net_income", "net_margin"):
                    if item.get(key) is not None:
                        unit = (
                            NormalizedUnit.PERCENT
                            if key == "net_margin"
                            else NormalizedUnit.UNSPECIFIED
                        )
                        metrics.append(
                            FinancialMetric(
                                key=key,
                                value=_decimal(item[key]),
                                unit=unit,
                                comparable=unit is not NormalizedUnit.UNSPECIFIED,
                            )
                        )
                periods.append(
                    FinancialPeriodRead(
                        fiscal_year=_strict_integer(item["fiscal_year"]),
                        fiscal_quarter=None
                        if item.get("fiscal_quarter") is None
                        else _strict_integer(item["fiscal_quarter"]),
                        period_end_date=_required_date(item["period_end_date"]),
                        metrics=tuple(metrics),
                    )
                )
            results.append(
                FinancialSeriesRead(
                    instrument=PartialInstrumentReference(symbol=expected_symbol),
                    period=period,
                    financials=tuple(periods),
                )
            )
        return FinancialsRead(
            series=tuple(results),
            unavailable_symbols=tuple(unavailable),
            evidence=_evidence(result, received_at),
        )

    return _boundary(build)


def map_closed_equity_orders(
    result: GatewayReadResult,
    *,
    received_at: datetime | str,
    expected_symbol: Symbol | None = None,
    expected_order_id: str | None = None,
) -> ClosedOrdersRead:
    """Map closed external lifecycles without inventing ainvest command IDs."""

    def build() -> ClosedOrdersRead:
        data = _data(result, "get_equity_orders")
        rows = _nullable_sequence(data, "orders")
        omitted: list[str] = []
        closed: list[ClosedOrderRead] = []
        identity_pairs: list[tuple[str, str]] = []
        for index, raw in enumerate(rows):
            row = _object(raw)
            order_id = _string(row, "id")
            instrument_id = _string(row, "instrument_id")
            symbol = _string(row, "symbol")
            identity_pairs.append((instrument_id, symbol))
            if expected_symbol is not None and symbol != expected_symbol:
                raise RobinhoodMappingError(MappingErrorCode.INCONSISTENT_DATA)
            if expected_order_id is not None and order_id != expected_order_id:
                raise RobinhoodMappingError(MappingErrorCode.INCONSISTENT_DATA)
            state = EquityOrderState(_string(row, "state"))
            if state in OPEN_ORDER_STATES:
                continue
            dollar_raw = row.get("dollar_based_amount")
            dollar = None if dollar_raw is None else _object(dollar_raw)
            executions: list[EquityExecutionRead] = []
            for raw_execution in _nullable_sequence(row, "executions"):
                execution = _object(raw_execution)
                executions.append(
                    EquityExecutionRead(
                        execution_id=_string(execution, "id"),
                        price=_decimal(execution["price"]),
                        quantity=_decimal(execution["quantity"]),
                        timestamp=_required_time(execution.get("timestamp")),
                        fees=_decimal(execution["fees"]),
                    )
                )
            reject_reason = None
            if "reject_reason" in row:
                reject_reason = _untrusted(
                    row["reject_reason"], f"orders[{index}].reject_reason", omitted
                )
            closed.append(
                ClosedOrderRead(
                    order_id=order_id,
                    instrument=PartialInstrumentReference(
                        instrument_id=instrument_id, symbol=symbol
                    ),
                    **_common_order_fields(row, state=state, dollar=dollar),
                    executions=tuple(executions),
                    reject_reason=reject_reason,
                )
            )
        _require_consistent_identity_pairs(identity_pairs)
        return ClosedOrdersRead(
            closed_orders=tuple(closed),
            records_seen=len(rows),
            has_more=_has_more(data),
            omitted_untrusted_fields=tuple(omitted),
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
        **_common_order_fields(row, state=state, dollar=dollar),
    )


def _common_order_fields(
    row: Mapping[str, Any],
    *,
    state: EquityOrderState,
    dollar: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Parse fields shared by open and closed external order views once."""

    return {
        "state": state,
        "side": OrderSide(_string(row, "side").upper()),
        "order_type": _order_type(_string(row, "type"), _string(row, "trigger")),
        "quantity": _optional_decimal(row.get("quantity")),
        "filled_quantity": _decimal(row["cumulative_quantity"]),
        "dollar_amount": None if dollar is None else _decimal(dollar["amount"]),
        "dollar_currency": None if dollar is None else _string(dollar, "currency_code"),
        "limit_price": _optional_decimal(row.get("price")),
        "stop_price": _optional_decimal(row.get("stop_price")),
        "average_price": _optional_decimal(row.get("average_price")),
        "fees": _decimal(row["fees"]),
        "time_in_force": TimeInForce(_string(row, "time_in_force")),
        "market_hours": MarketHours(_string(row, "market_hours")),
        "placed_agent": _string(row, "placed_agent"),
        "created_at": _required_time(row.get("created_at")),
        "last_transaction_at": _optional_time(row.get("last_transaction_at")),
    }


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


def _expected_symbols(values: Sequence[Symbol]) -> tuple[Symbol, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not values:
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    normalized = tuple(_plain_string(value) for value in values)
    if len(normalized) != len(set(normalized)):
        raise RobinhoodMappingError(MappingErrorCode.INCONSISTENT_DATA)
    return normalized


def _require_expected_symbol_partition(actual: Sequence[str], expected: Sequence[str]) -> None:
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise RobinhoodMappingError(MappingErrorCode.INCONSISTENT_DATA)


def _require_consistent_identity_pairs(pairs: Sequence[tuple[str, str]]) -> None:
    by_id: dict[str, str] = {}
    by_symbol: dict[str, str] = {}
    for instrument_id, symbol in pairs:
        if by_id.setdefault(instrument_id, symbol) != symbol:
            raise RobinhoodMappingError(MappingErrorCode.INCONSISTENT_DATA)
        if by_symbol.setdefault(symbol, instrument_id) != instrument_id:
            raise RobinhoodMappingError(MappingErrorCode.INCONSISTENT_DATA)


def _price_levels(data: Mapping[str, Any], key: str) -> tuple[PriceLevel, ...]:
    return tuple(
        PriceLevel(
            price=_decimal(row["price"]),
            quantity=_positive_integer(row["quantity"]),
        )
        for row in map(_object, _nullable_sequence(data, key))
    )


def _string(data: Mapping[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value or value != value.strip():
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return value


def _plain_string(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return value


def _optional_string(value: object) -> str | None:
    return None if value in (None, "") else _plain_string(value)


def _optional_enum(value: object, enum_type: type[Any]) -> Any:
    return None if value in (None, "") else enum_type(_plain_string(value))


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


def _nonnegative_decimal(value: object) -> Decimal:
    parsed = _decimal(value)
    if parsed < 0:
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return parsed


def _positive_decimal(value: object) -> Decimal:
    parsed = _decimal(value)
    if parsed <= 0:
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return parsed


def _strict_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return value


def _nonnegative_integer(value: object) -> Decimal:
    parsed = _strict_integer(value)
    if parsed < 0:
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return Decimal(parsed)


def _positive_integer(value: object) -> Decimal:
    parsed = _strict_integer(value)
    if parsed <= 0:
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return Decimal(parsed)


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


def _required_date(value: object) -> date:
    text = _plain_string(value)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE) from None
    if parsed.isoformat() != text:
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return parsed


def _fundamental_status_code(value: object) -> str:
    text = _plain_string(value)
    if re.fullmatch(r"[A-Z0-9]{1,16}", text) is None:
        raise RobinhoodMappingError(MappingErrorCode.INVALID_VALUE)
    return text


def _untrusted(value: object, path: str, omitted: list[str]) -> UntrustedDisplayText:
    try:
        text = _plain_string(value)
        return UntrustedDisplayText(value=text)
    except (RobinhoodMappingError, ValidationError, ValueError):
        omitted.append(path)
        return UntrustedDisplayText(value=UNAVAILABLE_UNTRUSTED_TEXT)


def _optional_untrusted(
    value: object, path: str, omitted: list[str]
) -> UntrustedDisplayText | None:
    return None if value is None else _untrusted(value, path, omitted)


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
    "map_closed_equity_orders",
    "map_equity_fundamentals",
    "map_equity_historicals",
    "map_equity_positions",
    "map_equity_price_books",
    "map_equity_quotes",
    "map_equity_tradability",
    "map_financials",
    "map_open_equity_orders",
    "map_portfolio",
]
