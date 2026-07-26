"""Market, technical, fundamental, and event observation schemas (P02-T1)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    CurrencyCode,
    DomainModel,
    InstrumentIdentity,
    Money,
    NonNegativeDecimal,
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


class FundamentalSnapshot(DomainModel):
    """Standardized fundamental facts with evidence-grade provenance."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    symbol: Symbol
    as_of: UtcDateTime
    facts: dict[str, NonNegativeDecimal | str | bool] = Field(default_factory=dict)
    provenance: Provenance

    @model_validator(mode="after")
    def _reject_empty_facts_without_flag(self) -> FundamentalSnapshot:
        if not self.facts and not self.provenance.quality_flags:
            raise ValueError("empty fundamentals require an explicit quality flag")
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
    "FundamentalSnapshot",
    "MarketEvent",
    "MarketQuote",
    "OhlcvBar",
    "ResearchMarketSection",
    "ResearchPortfolioSection",
    "TechnicalIndicators",
]
