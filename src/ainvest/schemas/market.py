"""Market, technical, fundamental, and event observation schemas (P02-T1)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import StringConstraints, model_validator

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    CurrencyCode,
    DomainModel,
    InstrumentIdentity,
    Money,
    NonNegativeDecimal,
    PnL,
    PositiveDecimal,
    Price,
    Provenance,
    Quantity,
    SchemaVersion,
    Symbol,
    UtcDateTime,
)


class MarketQuote(DomainModel):
    """Equity/ETF quote observation with required provenance."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    instrument: InstrumentIdentity
    last_price: Price
    bid: Price | None = None
    ask: Price | None = None
    currency: CurrencyCode = "USD"
    provenance: Provenance

    @model_validator(mode="after")
    def _currency_matches_instrument(self) -> MarketQuote:
        if self.currency != self.instrument.currency:
            raise ValueError("quote currency must match instrument.currency")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("bid must be <= ask")
        return self


class OhlcvBar(DomainModel):
    """OHLCV bar with provenance."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    instrument: InstrumentIdentity
    interval: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]?[mhdw]$", max_length=8)]
    bar_start: UtcDateTime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: NonNegativeDecimal
    provenance: Provenance

    @model_validator(mode="after")
    def _ohlc_consistency(self) -> OhlcvBar:
        if self.high < self.low:
            raise ValueError("high must be >= low")
        if self.open < self.low or self.open > self.high:
            raise ValueError("open must be within [low, high]")
        if self.close < self.low or self.close > self.high:
            raise ValueError("close must be within [low, high]")
        if self.bar_start > self.provenance.observed_at:
            raise ValueError("bar_start must be <= provenance.observed_at")
        return self


class TechnicalIndicators(DomainModel):
    """Deterministic technical indicator snapshot."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    symbol: Symbol
    sma_20: PositiveDecimal | None = None
    sma_50: PositiveDecimal | None = None
    rsi_14: NonNegativeDecimal | None = None
    atr_14: NonNegativeDecimal | None = None
    provenance: Provenance

    @model_validator(mode="after")
    def _rsi_bounds(self) -> TechnicalIndicators:
        if self.rsi_14 is not None and self.rsi_14 > 100:
            raise ValueError("rsi_14 must be <= 100")
        return self


class FactValueKind(StrEnum):
    """Discriminated fundamental fact value kinds."""

    DECIMAL = "DECIMAL"
    TEXT = "TEXT"
    BOOLEAN = "BOOLEAN"


class FundamentalFact(DomainModel):
    """Immutable typed fundamental fact. Raw dicts are not allowed."""

    key: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$", max_length=64)]
    kind: FactValueKind
    decimal_value: PnL | None = None
    text_value: Annotated[str, StringConstraints(min_length=1, max_length=512)] | None = None
    boolean_value: bool | None = None
    unit: Annotated[str, StringConstraints(min_length=1, max_length=32)] | None = None

    @model_validator(mode="after")
    def _one_value_for_kind(self) -> Self:
        if self.kind is FactValueKind.DECIMAL:
            if (
                self.decimal_value is None
                or self.text_value is not None
                or self.boolean_value is not None
            ):
                raise ValueError("DECIMAL facts require decimal_value only")
        elif self.kind is FactValueKind.TEXT:
            if (
                self.text_value is None
                or self.decimal_value is not None
                or self.boolean_value is not None
            ):
                raise ValueError("TEXT facts require text_value only")
        elif self.kind is FactValueKind.BOOLEAN and (
            self.boolean_value is None
            or self.decimal_value is not None
            or self.text_value is not None
        ):
            raise ValueError("BOOLEAN facts require boolean_value only")
        return self


class FundamentalSnapshot(DomainModel):
    """Standardized fundamental facts with evidence-grade provenance."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    symbol: Symbol
    as_of: UtcDateTime
    facts: tuple[FundamentalFact, ...] = ()
    provenance: Provenance

    @model_validator(mode="after")
    def _reject_empty_facts_without_flag(self) -> FundamentalSnapshot:
        if not self.facts and not self.provenance.quality_flags:
            raise ValueError("empty fundamentals require an explicit quality flag")
        keys = [fact.key for fact in self.facts]
        if len(keys) != len(set(keys)):
            raise ValueError("fundamental fact keys must be unique")
        if self.as_of < self.provenance.observed_at:
            raise ValueError("fundamentals as_of must be >= provenance.observed_at")
        return self


class MarketEvent(DomainModel):
    """Company or market event discovery record."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    event_id: Annotated[str, StringConstraints(min_length=4, max_length=128)]
    symbol: Symbol | None = None
    event_type: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")]
    headline: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    occurred_at: UtcDateTime
    provenance: Provenance

    @model_validator(mode="after")
    def _event_time_order(self) -> MarketEvent:
        if self.occurred_at > self.provenance.received_at:
            raise ValueError("occurred_at must be <= provenance.received_at")
        return self


class ResearchMarketSection(DomainModel):
    """Compact market section embedded in ResearchPacket (design.md §6.1)."""

    last_price: Price
    bid: Price | None = None
    ask: Price | None = None
    currency: CurrencyCode = "USD"
    observed_at: UtcDateTime
    provenance: Provenance

    @model_validator(mode="after")
    def _align_observed_at(self) -> ResearchMarketSection:
        if self.observed_at != self.provenance.observed_at:
            raise ValueError("market.observed_at must equal provenance.observed_at")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("bid must be <= ask")
        return self


class ResearchPortfolioSection(DomainModel):
    """Portfolio context embedded in a research packet (full portfolio is P02-T2)."""

    quantity: Quantity
    market_value: Money
    portfolio_weight: NonNegativeDecimal
    buying_power: Money

    @model_validator(mode="after")
    def _weight_bounds(self) -> ResearchPortfolioSection:
        if self.portfolio_weight > 1:
            raise ValueError("portfolio_weight must be <= 1")
        return self


__all__ = [
    "FactValueKind",
    "FundamentalFact",
    "FundamentalSnapshot",
    "MarketEvent",
    "MarketQuote",
    "OhlcvBar",
    "ResearchMarketSection",
    "ResearchPortfolioSection",
    "TechnicalIndicators",
]
