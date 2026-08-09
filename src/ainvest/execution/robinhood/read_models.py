"""Provider-independent normalized Robinhood read models (P06-T1, part 1).

The pinned gateway payloads do not contain enough metadata to construct the
repository's canonical ``InstrumentIdentity`` or long-only
``PortfolioSnapshot``.  These deliberately narrower models retain only facts
the gateway actually returned and the digests needed to trace the accepted
result.  They are internal to the Robinhood integration boundary until P06-T2
adds a user-facing read service.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StringConstraints, model_validator

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
    CASH = "cash"
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
    def _at_most_one_default(self) -> Self:
        if sum(account.is_default for account in self.accounts) > 1:
            raise ValueError("at most one account may be default")
        return self


class BuyingPowerRead(DomainModel):
    amount: Money
    unleveraged_amount: PnL
    intraday_amount: Money | None = None
    off_intraday_amount: Money | None = None
    currency: CurrencyCode


class PortfolioRead(DomainModel):
    """Account totals without pretending they satisfy a stock-only equation."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    account_scope: RobinhoodAccountScope
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
    def _currencies_match(self) -> Self:
        if self.buying_power.currency != self.currency:
            raise ValueError("buying-power currency must match portfolio currency")
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
    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    account_scope: RobinhoodAccountScope
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


class OpenOrderRead(DomainModel):
    """Open external order; no invented ainvest proposal/hash identifiers."""

    order_id: OrderId
    instrument_id: InstrumentId
    symbol: Symbol
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
    created_at: UtcDateTime
    last_transaction_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def _coherent_open_order(self) -> Self:
        if self.state not in OPEN_ORDER_STATES:
            raise ValueError("open-order view may contain only open states")
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


class OpenOrdersRead(DomainModel):
    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    account_scope: RobinhoodAccountScope
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


__all__ = [
    "OPEN_ORDER_STATES",
    "AccountRead",
    "AccountsRead",
    "BrokerageTradingType",
    "BuyingPowerRead",
    "EquityOrderState",
    "EquityOrderType",
    "EquityPositionRead",
    "EquityQuoteRead",
    "MarketHours",
    "OpenOrderRead",
    "OpenOrdersRead",
    "PortfolioRead",
    "PositionsRead",
    "QuoteIneligibility",
    "QuoteSession",
    "QuotesRead",
    "RobinhoodAccountScope",
    "RobinhoodReadEvidence",
    "TimeInForce",
]
