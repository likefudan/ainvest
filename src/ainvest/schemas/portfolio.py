"""Portfolio and account snapshot schemas (P02-T2).

These models are the only portfolio view a strategy may read. They are frozen
and Decimal/UTC-safe. Full order/risk objects remain in later P02 cards.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    CurrencyCode,
    DomainModel,
    InstrumentIdentity,
    Money,
    PnL,
    PositiveDecimal,
    Price,
    Provenance,
    Quantity,
    SchemaVersion,
    StableId,
    Symbol,
    UtcDateTime,
    Weight,
)


class AccountScope(StrEnum):
    """Broker account partition observed by strategies and sizing.

    ``paper`` is the safe default path. ``agentic`` matches the Robinhood
    account scope used by design.md OrderProposal examples.
    """

    PAPER = "paper"
    AGENTIC = "agentic"


class OpenOrderSide(StrEnum):
    """Sides allowed on open-order snapshots (long-only first release)."""

    BUY = "BUY"
    SELL = "SELL"


class PositionSnapshot(DomainModel):
    """Single long position within a portfolio snapshot."""

    instrument: InstrumentIdentity
    quantity: Quantity
    market_value: Money
    portfolio_weight: Weight
    average_cost: Price | None = None
    unrealized_pnl: PnL | None = None
    currency: CurrencyCode = "USD"

    @model_validator(mode="after")
    def _currency_matches_instrument(self) -> PositionSnapshot:
        if self.currency != self.instrument.currency:
            raise ValueError("position currency must match instrument.currency")
        return self


class OpenOrderSnapshot(DomainModel):
    """Outstanding broker order visible to strategies and pre-trade checks."""

    order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    instrument: InstrumentIdentity
    side: OpenOrderSide
    quantity: PositiveDecimal
    submitted_at: UtcDateTime
    limit_price: Price | None = None
    symbol: Symbol | None = None

    @model_validator(mode="after")
    def _symbol_matches_instrument(self) -> OpenOrderSnapshot:
        if self.symbol is not None and self.symbol != self.instrument.symbol:
            raise ValueError("open order symbol must match instrument.symbol")
        return self


class ExposureSnapshot(DomainModel):
    """Aggregate exposure derived from cash and positions."""

    cash: Money
    equity: Money
    gross_market_value: Money
    net_market_value: Money
    largest_position_weight: Weight
    position_count: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def _long_only_net_bounds(self) -> ExposureSnapshot:
        if self.net_market_value > self.gross_market_value:
            raise ValueError("net_market_value must be <= gross_market_value")
        return self


class PortfolioSnapshot(DomainModel):
    """Immutable account snapshot consumed by StrategyContext and sizing."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    snapshot_id: StableId
    account_scope: AccountScope
    as_of: UtcDateTime
    currency: CurrencyCode = "USD"
    cash: Money
    buying_power: Money
    equity: Money
    positions: tuple[PositionSnapshot, ...] = ()
    open_orders: tuple[OpenOrderSnapshot, ...] = ()
    exposure: ExposureSnapshot
    provenance: Provenance

    @model_validator(mode="after")
    def _consistency(self) -> PortfolioSnapshot:
        if self.exposure.cash != self.cash:
            raise ValueError("exposure.cash must match portfolio cash")
        if self.exposure.equity != self.equity:
            raise ValueError("exposure.equity must match portfolio equity")
        if self.exposure.position_count != len(self.positions):
            raise ValueError("exposure.position_count must match positions length")
        if self.buying_power > self.equity and self.account_scope is AccountScope.PAPER:
            # Paper remains cash-like: buying power cannot exceed equity.
            raise ValueError("paper buying_power must be <= equity")

        instrument_ids = [position.instrument.instrument_id for position in self.positions]
        if len(instrument_ids) != len(set(instrument_ids)):
            raise ValueError("position instrument_id values must be unique")

        for position in self.positions:
            if position.currency != self.currency:
                raise ValueError("position currency must match portfolio currency")
            if position.instrument.currency != self.currency:
                raise ValueError("instrument currency must match portfolio currency")

        for order in self.open_orders:
            if order.instrument.currency != self.currency:
                raise ValueError("open order currency must match portfolio currency")
            if order.submitted_at > self.as_of:
                raise ValueError("open order submitted_at must be <= portfolio as_of")

        if self.provenance.observed_at > self.as_of:
            raise ValueError("portfolio provenance.observed_at must be <= as_of")
        if self.provenance.received_at > self.as_of:
            raise ValueError("portfolio provenance.received_at must be <= as_of")
        return self


__all__ = [
    "AccountScope",
    "ExposureSnapshot",
    "OpenOrderSide",
    "OpenOrderSnapshot",
    "PortfolioSnapshot",
    "PositionSnapshot",
]
