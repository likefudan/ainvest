"""Provider-independent normalized Robinhood read models (P06-T1).

The pinned gateway payloads do not contain enough metadata to construct the
repository's canonical ``InstrumentIdentity`` or long-only
``PortfolioSnapshot``.  These deliberately narrower models retain only facts
the gateway actually returned and the digests needed to trace the accepted
result.  They are internal to the Robinhood integration boundary until P06-T2
adds a user-facing read service.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StringConstraints, field_validator, model_validator

from ainvest.data.models import PriceLevel
from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    CurrencyCode,
    DomainModel,
    Money,
    NonNegativeDecimal,
    OrderSide,
    PnL,
    PositiveDecimal,
    Price,
    Provenance,
    Quantity,
    SchemaVersion,
    Symbol,
    UtcDateTime,
)
from ainvest.schemas.market import FundamentalSnapshot

Digest = Annotated[
    str,
    StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$", min_length=71, max_length=71),
]
MachineToken = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$", min_length=1, max_length=64),
]
OrderId = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9_-]{3,128}$", min_length=3, max_length=128),
]
InstrumentId = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9_-]{3,128}$", min_length=3, max_length=128),
]
FieldPath = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]*(?:\[[0-9]+\]|\.[a-z][a-z0-9_]*)*$",
        min_length=1,
        max_length=160,
    ),
]

UNAVAILABLE_UNTRUSTED_TEXT = "[unavailable: untrusted text omitted]"


class UntrustedDisplayText(DomainModel):
    """Bounded provider text for escaped display only; never prompts or logs."""

    value: Annotated[str, StringConstraints(min_length=1, max_length=512)]

    @field_validator("value")
    @classmethod
    def _reject_controls(cls, value: str) -> str:
        if any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in value):
            raise ValueError("untrusted display text cannot contain control characters")
        return value


class NamedUntrustedText(DomainModel):
    field: MachineToken
    text: UntrustedDisplayText


class PartialInstrumentReference(DomainModel):
    """Only provider facts; deliberately not a canonical InstrumentIdentity."""

    symbol: Symbol
    instrument_id: InstrumentId | None = None
    identity_verified: Literal[False] = False


class NormalizedUnit(StrEnum):
    SHARES = "SHARES"
    USD = "USD"
    RATIO = "RATIO"
    PERCENT = "PERCENT"
    PEOPLE = "PEOPLE"
    YEAR = "YEAR"
    UNSPECIFIED = "UNSPECIFIED"


class AccountBinding(StrEnum):
    UNVERIFIED = "unverified"


class SessionEvidence(StrEnum):
    UNVERIFIED = "unverified"


class RobinhoodReadEvidence(DomainModel):
    """Source and immutable gateway digests attached to one normalized read."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    provenance: Provenance
    manifest_digest: Digest
    schema_digest: Digest
    result_digest: Digest


class RobinhoodAccountScope(StrEnum):
    """Whether the account is accessible to this agent.

    ``unavailable`` intentionally combines all non-agentic states.  The raw
    account number is not a normalized identifier and never crosses this
    boundary.
    """

    AGENTIC = "agentic"
    UNAVAILABLE = "unavailable"


class BrokerageTradingType(StrEnum):
    """Provider trading-permissions label, not leverage or tradability proof."""

    CASH = "cash"
    LIMITED_MARGIN = "limited_margin"
    MARGIN = "margin"


class AccountRead(DomainModel):
    """A non-identifying account eligibility summary."""

    scope: RobinhoodAccountScope
    trading_type: BrokerageTradingType
    brokerage_account_type: MachineToken
    is_default: StrictBool
    state: MachineToken
    deactivated: StrictBool
    permanently_deactivated: StrictBool
    unsettled_funds: Money | None = None
    tradable: StrictBool

    @model_validator(mode="after")
    def _tradability_is_derived(self) -> Self:
        expected = (
            self.scope is RobinhoodAccountScope.AGENTIC
            and self.state == "active"
            and not self.deactivated
            and not self.permanently_deactivated
        )
        if self.tradable is not expected:
            raise ValueError("tradable must match account eligibility and state")
        return self


class AccountsRead(DomainModel):
    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    accounts: tuple[AccountRead, ...]
    evidence: RobinhoodReadEvidence

    @model_validator(mode="after")
    def _selection_is_unambiguous(self) -> Self:
        if sum(account.is_default for account in self.accounts) > 1:
            raise ValueError("at most one account may be default")
        if sum(account.tradable for account in self.accounts) > 1:
            raise ValueError("multiple eligible agentic accounts are ambiguous")
        return self


class BuyingPowerRead(DomainModel):
    amount: Money
    unleveraged_amount: PnL
    intraday_amount: Money | None = None
    off_intraday_amount: Money | None = None
    currency: CurrencyCode


class PortfolioRead(DomainModel):
    """Unbound account totals without a caller-asserted account identity."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    currency: CurrencyCode
    total_value: Money
    cash: Money
    pending_deposits: Money
    equity_value: Money
    options_value: Money
    futures_value: Money
    event_contracts_value: Money
    crypto_value: Money
    mutual_funds_value: Money
    fixed_income_value: Money
    buying_power: BuyingPowerRead
    evidence: RobinhoodReadEvidence

    @model_validator(mode="after")
    def _totals_are_coherent(self) -> Self:
        if self.buying_power.currency != self.currency:
            raise ValueError("buying-power currency must match portfolio currency")
        component_total = sum(
            (
                self.cash,
                self.equity_value,
                self.options_value,
                self.futures_value,
                self.event_contracts_value,
                self.crypto_value,
                self.mutual_funds_value,
                self.fixed_income_value,
            )
        )
        if self.total_value != component_total:
            raise ValueError("portfolio total must equal cash plus asset-class totals")
        return self


class EquityPositionRead(DomainModel):
    symbol: Symbol
    quantity: PositiveDecimal
    intraday_quantity: PnL
    average_buy_price: Price | None = None
    shares_available_for_sells: Quantity
    shares_held_for_sells: Quantity
    shares_held_for_stock_grants: Quantity
    shares_held_for_options_events: Quantity
    shares_held_for_asset_transfer: Quantity
    shares_pending_from_options_events: Quantity

    @model_validator(mode="after")
    def _holdings_are_bounded(self) -> Self:
        if self.shares_available_for_sells > self.quantity:
            raise ValueError("sellable shares cannot exceed position quantity")
        return self


class PositionsRead(DomainModel):
    """Unbound positions; account identity is not present in this provider payload."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    positions: tuple[EquityPositionRead, ...]
    has_more: StrictBool = False
    evidence: RobinhoodReadEvidence

    @model_validator(mode="after")
    def _symbols_are_unique(self) -> Self:
        symbols = [position.symbol for position in self.positions]
        if len(symbols) != len(set(symbols)):
            raise ValueError("position symbols must be unique")
        return self


class QuoteSession(StrEnum):
    REGULAR = "regular"


class QuoteIneligibility(StrEnum):
    SESSION_UNVERIFIED = "session_unverified"
    NO_TRADES = "no_trades"
    INACTIVE_INSTRUMENT = "inactive_instrument"
    MISSING_BID = "missing_bid"
    MISSING_ASK = "missing_ask"
    MISSING_BID_TIME = "missing_bid_time"
    MISSING_ASK_TIME = "missing_ask_time"
    CROSSED_MARKET = "crossed_market"
    STALE = "stale"
    FUTURE_TIMESTAMP = "future_timestamp"


class EquityQuoteRead(DomainModel):
    """Regular-session quote view with explicit live-use eligibility."""

    symbol: Symbol
    session: Literal[QuoteSession.REGULAR] = QuoteSession.REGULAR
    last_price: Price
    last_at: UtcDateTime
    bid: Price | None = None
    bid_at: UtcDateTime | None = None
    ask: Price | None = None
    ask_at: UtcDateTime | None = None
    has_traded: StrictBool
    listing_state: MachineToken
    live_eligible: StrictBool
    ineligibility: tuple[QuoteIneligibility, ...] = ()

    @model_validator(mode="after")
    def _eligibility_is_coherent(self) -> Self:
        if self.live_eligible == bool(self.ineligibility):
            raise ValueError("live eligibility must agree with ineligibility reasons")
        if (
            self.bid is not None
            and self.ask is not None
            and self.bid > self.ask
            and QuoteIneligibility.CROSSED_MARKET not in self.ineligibility
        ):
            raise ValueError("crossed quotes must be marked ineligible")
        return self


class QuotesRead(DomainModel):
    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    quotes: tuple[EquityQuoteRead, ...]
    evidence: RobinhoodReadEvidence

    @model_validator(mode="after")
    def _symbols_are_unique(self) -> Self:
        symbols = [quote.symbol for quote in self.quotes]
        if len(symbols) != len(set(symbols)):
            raise ValueError("quote symbols must be unique")
        return self


class EquityOrderState(StrEnum):
    NEW = "new"
    QUEUED = "queued"
    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"
    VOIDED = "voided"
    PENDING_CANCELLED = "pending_cancelled"
    PARTIALLY_FILLED_REST_CANCELLED = "partially_filled_rest_cancelled"
    LOCATING = "locating"
    LOCATE_FAILED = "locate_failed"


OPEN_ORDER_STATES = frozenset(
    {
        EquityOrderState.NEW,
        EquityOrderState.QUEUED,
        EquityOrderState.CONFIRMED,
        EquityOrderState.UNCONFIRMED,
        EquityOrderState.PARTIALLY_FILLED,
        EquityOrderState.PENDING_CANCELLED,
        EquityOrderState.LOCATING,
    }
)


class EquityOrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"


class MarketHours(StrEnum):
    REGULAR = "regular_hours"
    EXTENDED = "extended_hours"
    ALL_DAY = "all_day_hours"


class TimeInForce(StrEnum):
    DAY = "gfd"
    GOOD_TIL_CANCELLED = "gtc"


class _ExternalOrderFieldsRead(DomainModel):
    """Lifecycle fields shared by open and closed external order views."""

    side: OrderSide
    order_type: EquityOrderType
    state: EquityOrderState
    quantity: PositiveDecimal | None = None
    filled_quantity: NonNegativeDecimal
    dollar_amount: Money | None = None
    dollar_currency: CurrencyCode | None = None
    limit_price: Price | None = None
    stop_price: Price | None = None
    average_price: Price | None = None
    fees: Money
    time_in_force: TimeInForce
    market_hours: MarketHours
    placed_agent: MachineToken
    created_at: UtcDateTime
    last_transaction_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def _common_order_fields_are_coherent(self) -> Self:
        if self.quantity is None and self.dollar_amount is None:
            raise ValueError("order needs either share quantity or dollar amount")
        if (self.dollar_amount is None) != (self.dollar_currency is None):
            raise ValueError("dollar amount and currency must appear together")
        if self.quantity is not None and self.filled_quantity > self.quantity:
            raise ValueError("filled quantity cannot exceed requested quantity")
        if self.last_transaction_at is not None and self.last_transaction_at < self.created_at:
            raise ValueError("last transaction cannot precede order creation")
        if (
            self.order_type in {EquityOrderType.LIMIT, EquityOrderType.STOP_LIMIT}
            and self.limit_price is None
        ):
            raise ValueError("limit orders require a limit price")
        if (
            self.order_type in {EquityOrderType.STOP_MARKET, EquityOrderType.STOP_LIMIT}
            and self.stop_price is None
        ):
            raise ValueError("stop orders require a stop price")
        return self


class OpenOrderRead(_ExternalOrderFieldsRead):
    """Open external order; no invented ainvest proposal/hash identifiers."""

    order_id: OrderId
    instrument_id: InstrumentId
    symbol: Symbol

    @model_validator(mode="after")
    def _state_is_open(self) -> Self:
        if self.state not in OPEN_ORDER_STATES:
            raise ValueError("open-order view may contain only open states")
        return self


class OpenOrdersRead(DomainModel):
    """Unbound orders; account identity is not present in this provider payload."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    open_orders: tuple[OpenOrderRead, ...]
    records_seen: Annotated[int, Field(ge=0)]
    has_more: StrictBool = False
    evidence: RobinhoodReadEvidence

    @model_validator(mode="after")
    def _order_ids_are_unique(self) -> Self:
        order_ids = [order.order_id for order in self.open_orders]
        if len(order_ids) != len(set(order_ids)):
            raise ValueError("open order IDs must be unique")
        if len(self.open_orders) > self.records_seen:
            raise ValueError("open orders cannot exceed records seen")
        return self


class PriceBookEntryRead(DomainModel):
    instrument: PartialInstrumentReference
    updated_at: UtcDateTime
    bids: tuple[PriceLevel, ...]
    asks: tuple[PriceLevel, ...]

    @model_validator(mode="after")
    def _book_is_ordered(self) -> Self:
        bid_prices = [level.price for level in self.bids]
        ask_prices = [level.price for level in self.asks]
        if bid_prices != sorted(bid_prices, reverse=True):
            raise ValueError("bids must be ordered highest to lowest")
        if ask_prices != sorted(ask_prices):
            raise ValueError("asks must be ordered lowest to highest")
        if len(bid_prices) != len(set(bid_prices)) or len(ask_prices) != len(set(ask_prices)):
            raise ValueError("price-book levels must have unique prices per side")
        if bid_prices and ask_prices and bid_prices[0] > ask_prices[0]:
            raise ValueError("price book cannot be crossed")
        return self


class PriceBookFailureRead(DomainModel):
    symbol: Symbol
    error: UntrustedDisplayText


class PriceBooksRead(DomainModel):
    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    books: tuple[PriceBookEntryRead, ...]
    errors: tuple[PriceBookFailureRead, ...]
    omitted_untrusted_fields: tuple[FieldPath, ...] = ()
    evidence: RobinhoodReadEvidence

    @model_validator(mode="after")
    def _symbols_are_partitioned(self) -> Self:
        symbols = [book.instrument.symbol for book in self.books] + [e.symbol for e in self.errors]
        if len(symbols) != len(set(symbols)):
            raise ValueError("each price-book symbol must occur exactly once")
        if any(book.updated_at > self.evidence.provenance.observed_at for book in self.books):
            raise ValueError("book update cannot follow gateway observation")
        return self


class TradabilityState(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class AccountTypeTradability(StrEnum):
    TRADABLE = "tradable"
    UNTRADABLE = "untradable"
    POSITION_CLOSING_ONLY = "position_closing_only"


class HaltSession(StrEnum):
    REGULAR = "regular_hours"
    EXTENDED = "extended_hours"
    ALL_DAY = "all_day_hours"


class AllDayTradability(StrEnum):
    TRADABLE = "all_day_tradability_tradable"
    UNTRADABLE = "all_day_tradability_untradable"


class TwentyFourSevenTradability(StrEnum):
    TRADABLE = "twenty_four_seven_tradability_tradable"
    UNTRADABLE = "twenty_four_seven_tradability_untradable"


class ShortSellingTradability(StrEnum):
    TRADABLE = "short_selling_tradability_tradable"
    UNTRADABLE = "short_selling_tradability_untradable"


class AccountTypeTradabilityRead(DomainModel):
    account_type: MachineToken
    tradability: AccountTypeTradability


class EquityTradabilityRead(DomainModel):
    instrument: PartialInstrumentReference
    name: UntrustedDisplayText | None = None
    simple_name: UntrustedDisplayText | None = None
    state: TradabilityState | None = None
    country: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")] | None = None
    tradeable: StrictBool
    fractional_tradability: AccountTypeTradability | None = None
    extended_hours_fractional_tradability: StrictBool
    all_day_tradability: AllDayTradability | None = None
    twenty_four_seven_tradability: TwentyFourSevenTradability | None = None
    short_selling_tradability: ShortSellingTradability | None = None
    internal_halt_reason: UntrustedDisplayText | None = None
    internal_halt_details: UntrustedDisplayText | None = None
    internal_halt_sessions: tuple[HaltSession, ...] = ()
    internal_halt_start_time: UtcDateTime | None = None
    internal_halt_end_time: UtcDateTime | None = None
    account_type_tradabilities: tuple[AccountTypeTradabilityRead, ...] = ()

    @model_validator(mode="after")
    def _halt_and_account_rows_are_coherent(self) -> Self:
        if (self.internal_halt_start_time is None) != (self.internal_halt_end_time is None):
            raise ValueError("halt start and end must appear together")
        if (
            self.internal_halt_start_time is not None
            and self.internal_halt_end_time is not None
            and self.internal_halt_start_time > self.internal_halt_end_time
        ):
            raise ValueError("halt start cannot follow halt end")
        account_types = [item.account_type for item in self.account_type_tradabilities]
        if len(account_types) != len(set(account_types)):
            raise ValueError("account-type tradability rows must be unique")
        if len(self.internal_halt_sessions) != len(set(self.internal_halt_sessions)):
            raise ValueError("halt sessions must be unique")
        return self


class TradabilitiesRead(DomainModel):
    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    tradabilities: tuple[EquityTradabilityRead, ...]
    unavailable_symbols: tuple[Symbol, ...] = ()
    account_binding: Literal[AccountBinding.UNVERIFIED] = AccountBinding.UNVERIFIED
    session_evidence: Literal[SessionEvidence.UNVERIFIED] = SessionEvidence.UNVERIFIED
    omitted_untrusted_fields: tuple[FieldPath, ...] = ()
    evidence: RobinhoodReadEvidence


class HistoricalInterval(StrEnum):
    SECOND_15 = "15second"
    SECOND_30 = "30second"
    MINUTE = "minute"
    MINUTE_5 = "5minute"
    MINUTE_10 = "10minute"
    MINUTE_30 = "30minute"
    HOUR = "hour"
    HOUR_4 = "4hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    MONTH_3 = "3month"
    MONTH_6 = "6month"
    YEAR = "year"
    YEAR_5 = "5year"
    YEAR_10 = "10year"
    YEAR_20 = "20year"
    YEAR_50 = "50year"


class HistoricalBounds(StrEnum):
    REGULAR = "regular"
    EXTENDED = "extended"
    TRADING = "trading"
    TWENTY_FOUR_FIVE = "24_5"
    TWENTY_FOUR_SEVEN = "24_7"
    HYPER_TRADING = "hyper_trading"


class HistoricalSession(StrEnum):
    REGULAR = "reg"
    PRE = "pre"
    POST = "post"


class HistoricalBarRead(DomainModel):
    begins_at: UtcDateTime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: NonNegativeDecimal
    session: HistoricalSession | None = None
    interpolated: StrictBool | None = None

    @model_validator(mode="after")
    def _ohlc_is_coherent(self) -> Self:
        if self.high < self.low or not self.low <= self.open <= self.high:
            raise ValueError("bar open/high/low values are inconsistent")
        if not self.low <= self.close <= self.high:
            raise ValueError("bar close must be within low and high")
        if self.interpolated is True and self.volume != 0:
            raise ValueError("interpolated bars must have zero volume")
        return self


class HistoricalSeriesRead(DomainModel):
    instrument: PartialInstrumentReference
    interval: HistoricalInterval
    bounds: HistoricalBounds
    bars: tuple[HistoricalBarRead, ...]

    @model_validator(mode="after")
    def _bars_are_strictly_ordered(self) -> Self:
        starts = [bar.begins_at for bar in self.bars]
        if starts != sorted(starts) or len(starts) != len(set(starts)):
            raise ValueError("historical bars must be unique and ascending")
        return self


class HistoricalsRead(DomainModel):
    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    series: tuple[HistoricalSeriesRead, ...]
    unavailable_symbols: tuple[Symbol, ...] = ()
    session_evidence: Literal[SessionEvidence.UNVERIFIED] = SessionEvidence.UNVERIFIED
    evidence: RobinhoodReadEvidence

    @model_validator(mode="after")
    def _bars_do_not_postdate_observation(self) -> Self:
        if any(
            bar.begins_at > self.evidence.provenance.observed_at
            for series in self.series
            for bar in series.bars
        ):
            raise ValueError("historical bar cannot follow gateway observation")
        return self


class FundamentalBounds(StrEnum):
    REGULAR = "regular"
    TRADING = "trading"
    EXTENDED = "extended"
    TWENTY_FOUR_FIVE = "24_5"


class FundamentalRead(DomainModel):
    instrument: PartialInstrumentReference
    bounds: FundamentalBounds
    market_date: date | None = None
    snapshot: FundamentalSnapshot
    non_comparable_fact_keys: tuple[MachineToken, ...] = ()
    display_text: tuple[NamedUntrustedText, ...] = ()

    @model_validator(mode="after")
    def _symbols_match(self) -> Self:
        if self.instrument.symbol != self.snapshot.symbol:
            raise ValueError("fundamental symbols must match")
        if (
            self.market_date is not None
            and self.market_date > self.snapshot.provenance.observed_at.date()
        ):
            raise ValueError("fundamental market date cannot follow observation")
        fields = [item.field for item in self.display_text]
        if len(fields) != len(set(fields)):
            raise ValueError("fundamental display fields must be unique")
        expected_non_comparable = {
            fact.key for fact in self.snapshot.facts if fact.unit == NormalizedUnit.UNSPECIFIED
        }
        if set(self.non_comparable_fact_keys) != expected_non_comparable:
            raise ValueError("unspecified fundamental facts must be explicitly non-comparable")
        if len(self.non_comparable_fact_keys) != len(set(self.non_comparable_fact_keys)):
            raise ValueError("non-comparable fundamental fact keys must be unique")
        return self


class FundamentalsRead(DomainModel):
    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    fundamentals: tuple[FundamentalRead, ...]
    unavailable_symbols: tuple[Symbol, ...] = ()
    omitted_untrusted_fields: tuple[FieldPath, ...] = ()
    evidence: RobinhoodReadEvidence


class ReportingPeriod(StrEnum):
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class FinancialMetric(DomainModel):
    key: Literal["revenue", "gross_profit", "net_income", "net_margin"]
    value: PnL
    unit: NormalizedUnit
    comparable: StrictBool

    @model_validator(mode="after")
    def _unit_policy_is_exact(self) -> Self:
        expected = (
            NormalizedUnit.PERCENT if self.key == "net_margin" else NormalizedUnit.UNSPECIFIED
        )
        comparable = expected is not NormalizedUnit.UNSPECIFIED
        if self.unit is not expected or self.comparable is not comparable:
            raise ValueError("financial metric unit policy is inconsistent")
        return self


class FinancialPeriodRead(DomainModel):
    fiscal_year: Annotated[int, Field(ge=1900, le=2200)]
    fiscal_quarter: Annotated[int, Field(ge=1, le=4)] | None = None
    period_end_date: date
    metrics: tuple[FinancialMetric, ...]


class FinancialSeriesRead(DomainModel):
    instrument: PartialInstrumentReference
    period: ReportingPeriod
    financials: tuple[FinancialPeriodRead, ...]

    @model_validator(mode="after")
    def _periods_are_coherent(self) -> Self:
        dates = [item.period_end_date for item in self.financials]
        if dates != sorted(dates, reverse=True) or len(dates) != len(set(dates)):
            raise ValueError("financial periods must be unique and most-recent first")
        identities = [(item.fiscal_year, item.fiscal_quarter) for item in self.financials]
        if len(identities) != len(set(identities)):
            raise ValueError("financial period identities must be unique")
        for item in self.financials:
            if (self.period is ReportingPeriod.QUARTERLY) != (item.fiscal_quarter is not None):
                raise ValueError("fiscal quarter must agree with reporting period")
            if abs(item.period_end_date.year - item.fiscal_year) > 1:
                raise ValueError("fiscal year is inconsistent with period end date")
        return self


class FinancialsRead(DomainModel):
    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    series: tuple[FinancialSeriesRead, ...]
    unavailable_symbols: tuple[Symbol, ...] = ()
    evidence: RobinhoodReadEvidence

    @model_validator(mode="after")
    def _periods_do_not_postdate_observation(self) -> Self:
        if any(
            period.period_end_date > self.evidence.provenance.observed_at.date()
            for series in self.series
            for period in series.financials
        ):
            raise ValueError("financial period cannot follow gateway observation")
        return self


CLOSED_ORDER_STATES = frozenset(EquityOrderState) - OPEN_ORDER_STATES


class EquityExecutionRead(DomainModel):
    execution_id: OrderId
    price: Price
    quantity: PositiveDecimal
    timestamp: UtcDateTime
    fees: Money


class ClosedOrderRead(_ExternalOrderFieldsRead):
    order_id: OrderId
    instrument: PartialInstrumentReference
    executions: tuple[EquityExecutionRead, ...] = ()
    reject_reason: UntrustedDisplayText | None = None

    @model_validator(mode="after")
    def _closed_lifecycle_is_coherent(self) -> Self:
        if self.state not in CLOSED_ORDER_STATES:
            raise ValueError("closed-order view may contain only closed states")
        if any(execution.timestamp < self.created_at for execution in self.executions):
            raise ValueError("execution cannot precede order creation")
        if self.last_transaction_at is not None and any(
            execution.timestamp > self.last_transaction_at for execution in self.executions
        ):
            raise ValueError("execution cannot follow the last transaction")
        if sum((item.quantity for item in self.executions), start=0) != self.filled_quantity:
            raise ValueError("execution quantity must equal cumulative quantity")
        if (
            self.state is EquityOrderState.FILLED
            and self.quantity is not None
            and self.filled_quantity != self.quantity
        ):
            raise ValueError("filled share order must fill its requested quantity")
        ids = [item.execution_id for item in self.executions]
        if len(ids) != len(set(ids)):
            raise ValueError("execution IDs must be unique within an order")
        return self


class ClosedOrdersRead(DomainModel):
    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    closed_orders: tuple[ClosedOrderRead, ...]
    records_seen: Annotated[int, Field(ge=0)]
    has_more: StrictBool = False
    account_binding: Literal[AccountBinding.UNVERIFIED] = AccountBinding.UNVERIFIED
    omitted_untrusted_fields: tuple[FieldPath, ...] = ()
    evidence: RobinhoodReadEvidence

    @model_validator(mode="after")
    def _orders_and_identity_are_unambiguous(self) -> Self:
        ids = [order.order_id for order in self.closed_orders]
        execution_ids = [e.execution_id for order in self.closed_orders for e in order.executions]
        if len(ids) != len(set(ids)) or len(execution_ids) != len(set(execution_ids)):
            raise ValueError("order and execution IDs must be unique")
        by_instrument: dict[str, Symbol] = {}
        by_symbol: dict[Symbol, str] = {}
        for order in self.closed_orders:
            instrument_id = order.instrument.instrument_id
            assert instrument_id is not None
            symbol = order.instrument.symbol
            if by_instrument.setdefault(instrument_id, symbol) != symbol:
                raise ValueError("instrument ID maps to inconsistent symbols")
            if by_symbol.setdefault(symbol, instrument_id) != instrument_id:
                raise ValueError("symbol maps to inconsistent instrument IDs")
        return self


__all__ = [
    "CLOSED_ORDER_STATES",
    "OPEN_ORDER_STATES",
    "UNAVAILABLE_UNTRUSTED_TEXT",
    "AccountBinding",
    "AccountRead",
    "AccountTypeTradability",
    "AccountTypeTradabilityRead",
    "AccountsRead",
    "AllDayTradability",
    "BrokerageTradingType",
    "BuyingPowerRead",
    "ClosedOrderRead",
    "ClosedOrdersRead",
    "EquityExecutionRead",
    "EquityOrderState",
    "EquityOrderType",
    "EquityPositionRead",
    "EquityQuoteRead",
    "EquityTradabilityRead",
    "FinancialMetric",
    "FinancialPeriodRead",
    "FinancialSeriesRead",
    "FinancialsRead",
    "FundamentalBounds",
    "FundamentalRead",
    "FundamentalsRead",
    "HaltSession",
    "HistoricalBarRead",
    "HistoricalBounds",
    "HistoricalInterval",
    "HistoricalSeriesRead",
    "HistoricalSession",
    "HistoricalsRead",
    "MarketHours",
    "NamedUntrustedText",
    "NormalizedUnit",
    "OpenOrderRead",
    "OpenOrdersRead",
    "PartialInstrumentReference",
    "PortfolioRead",
    "PositionsRead",
    "PriceBookEntryRead",
    "PriceBookFailureRead",
    "PriceBooksRead",
    "QuoteIneligibility",
    "QuoteSession",
    "QuotesRead",
    "ReportingPeriod",
    "RobinhoodAccountScope",
    "RobinhoodReadEvidence",
    "SessionEvidence",
    "ShortSellingTradability",
    "TimeInForce",
    "TradabilitiesRead",
    "TradabilityState",
    "TwentyFourSevenTradability",
    "UntrustedDisplayText",
]
