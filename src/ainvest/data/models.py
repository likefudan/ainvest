"""Provider-independent request, response, and observation models (P04-T0).

Provider adapters normalize their outputs into these models before returning
control to research, strategy, or risk code.  No provider SDK type may cross
this boundary.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Self, cast

from pydantic import Field, StrictBool, StringConstraints, model_validator

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    DomainModel,
    InstrumentIdentity,
    MachineCode,
    PositiveDecimal,
    Price,
    Provenance,
    QualityFlag,
    SchemaVersion,
    StableId,
    Symbol,
    UtcDateTime,
)
from ainvest.schemas.market import (
    FundamentalSnapshot,
    MarketEvent,
    MarketQuote,
    OhlcvBar,
)

InstrumentId = Annotated[str, StringConstraints(min_length=3, max_length=128)]
PageCursor = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class PriceAdjustment(StrEnum):
    """Price adjustment requested for historical bars."""

    RAW = "RAW"
    SPLIT = "SPLIT"
    SPLIT_AND_DIVIDEND = "SPLIT_AND_DIVIDEND"


class DataRequest(DomainModel):
    """Common bounded request settings for every data-provider call."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    timeout_seconds: Annotated[int, Field(ge=1, le=120)] = 30


class PaginatedDataRequest(DataRequest):
    """Common opaque-cursor pagination settings."""

    cursor: PageCursor | None = None
    page_size: Annotated[int, Field(ge=1, le=500)] = 100


class QuoteRequest(DataRequest):
    """Request current quote observations by canonical instrument ID."""

    instrument_ids: Annotated[tuple[InstrumentId, ...], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def _unique_instruments(self) -> Self:
        if len(self.instrument_ids) != len(set(self.instrument_ids)):
            raise ValueError("instrument_ids must be unique")
        return self


class PriceBookRequest(DataRequest):
    """Request current order-book depth by canonical instrument ID."""

    instrument_ids: Annotated[tuple[InstrumentId, ...], Field(min_length=1, max_length=50)]
    depth: Annotated[int, Field(ge=1, le=50)] = 10

    @model_validator(mode="after")
    def _unique_instruments(self) -> Self:
        if len(self.instrument_ids) != len(set(self.instrument_ids)):
            raise ValueError("instrument_ids must be unique")
        return self


class OhlcvRequest(PaginatedDataRequest):
    """Request historical bars with an explicit adjustment convention."""

    instrument_id: InstrumentId
    interval: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]?[mhdw]$", max_length=8)]
    start_at: UtcDateTime
    end_at: UtcDateTime
    adjustment: PriceAdjustment = PriceAdjustment.RAW

    @model_validator(mode="after")
    def _valid_window(self) -> Self:
        if self.start_at >= self.end_at:
            raise ValueError("start_at must be before end_at")
        return self


class FundamentalRequest(PaginatedDataRequest):
    """Request standardized fundamental snapshots at a fixed knowledge cutoff."""

    symbols: Annotated[tuple[Symbol, ...], Field(min_length=1, max_length=100)]
    as_of: UtcDateTime

    @model_validator(mode="after")
    def _unique_symbols(self) -> Self:
        if len(self.symbols) != len(set(self.symbols)):
            raise ValueError("symbols must be unique")
        return self


class NewsEventRequest(PaginatedDataRequest):
    """Request company, industry, or macro events over a closed-open window."""

    start_at: UtcDateTime
    end_at: UtcDateTime
    symbols: Annotated[tuple[Symbol, ...], Field(max_length=100)] = ()
    event_types: Annotated[tuple[MachineCode, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def _valid_filters(self) -> Self:
        if self.start_at >= self.end_at:
            raise ValueError("start_at must be before end_at")
        if len(self.symbols) != len(set(self.symbols)):
            raise ValueError("symbols must be unique")
        if len(self.event_types) != len(set(self.event_types)):
            raise ValueError("event_types must be unique")
        return self


class InstrumentMetadataRequest(PaginatedDataRequest):
    """Resolve metadata by canonical IDs, symbols, or both."""

    instrument_ids: Annotated[tuple[InstrumentId, ...], Field(max_length=100)] = ()
    symbols: Annotated[tuple[Symbol, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def _valid_identifiers(self) -> Self:
        if not self.instrument_ids and not self.symbols:
            raise ValueError("at least one instrument_id or symbol is required")
        if len(self.instrument_ids) != len(set(self.instrument_ids)):
            raise ValueError("instrument_ids must be unique")
        if len(self.symbols) != len(set(self.symbols)):
            raise ValueError("symbols must be unique")
        return self


class PriceLevel(DomainModel):
    """One normalized order-book level."""

    price: Price
    quantity: PositiveDecimal


class PriceBook(DomainModel):
    """Normalized Level 2 price book with evidence-grade provenance."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    instrument: InstrumentIdentity
    bids: tuple[PriceLevel, ...]
    asks: tuple[PriceLevel, ...]
    provenance: Provenance

    @model_validator(mode="after")
    def _book_is_consistent(self) -> Self:
        bid_prices = [level.price for level in self.bids]
        ask_prices = [level.price for level in self.asks]
        if bid_prices != sorted(bid_prices, reverse=True):
            raise ValueError("bids must be ordered from highest to lowest price")
        if ask_prices != sorted(ask_prices):
            raise ValueError("asks must be ordered from lowest to highest price")
        if len(bid_prices) != len(set(bid_prices)):
            raise ValueError("bid prices must be unique")
        if len(ask_prices) != len(set(ask_prices)):
            raise ValueError("ask prices must be unique")
        if bid_prices and ask_prices and bid_prices[0] > ask_prices[0]:
            raise ValueError("best bid must be <= best ask")
        if (
            not self.bids
            and not self.asks
            and QualityFlag.MISSING_FIELDS not in self.provenance.quality_flags
            and QualityFlag.PARTIAL not in self.provenance.quality_flags
        ):
            raise ValueError("an empty price book requires MISSING_FIELDS or PARTIAL")
        return self


class InstrumentMetadataObservation(DomainModel):
    """Broker-tradability metadata plus provider provenance."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    instrument: InstrumentIdentity
    tradable: StrictBool
    price_increment: PositiveDecimal
    quantity_increment: PositiveDecimal
    is_leveraged_or_inverse: StrictBool = False
    allows_short: StrictBool = False
    allows_margin: StrictBool = False
    provenance: Provenance


class ObservationBatch[ObservationT](DomainModel):
    """Non-paginated observations returned by one provider call.

    The envelope retains provenance for empty responses and proves that a
    provider did not silently mix sources or freshness metadata.
    """

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    items: tuple[ObservationT, ...]
    provenance: Provenance

    @model_validator(mode="after")
    def _single_source_and_time(self) -> Self:
        _validate_envelope_provenance(self.items, self.provenance)
        return self


class ObservationPage[ObservationT](ObservationBatch[ObservationT]):
    """One deterministic page; cursors are opaque to callers."""

    next_cursor: PageCursor | None = None


class OhlcvPage(ObservationPage[OhlcvBar]):
    """Historical-bar page retaining its adjustment and series identity."""

    instrument_id: InstrumentId
    interval: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]?[mhdw]$", max_length=8)]
    adjustment: PriceAdjustment

    @model_validator(mode="after")
    def _series_is_consistent(self) -> Self:
        if any(item.instrument.instrument_id != self.instrument_id for item in self.items):
            raise ValueError("all bars must match instrument_id")
        if any(item.interval != self.interval for item in self.items):
            raise ValueError("all bars must match interval")
        return self


class FakeDataset(DomainModel):
    """Immutable normalized observations consumed by deterministic fakes."""

    dataset_id: StableId
    provenance: Provenance
    quotes: tuple[MarketQuote, ...] = ()
    price_books: tuple[PriceBook, ...] = ()
    ohlcv: tuple[OhlcvBar, ...] = ()
    fundamentals: tuple[FundamentalSnapshot, ...] = ()
    news_events: tuple[MarketEvent, ...] = ()
    instrument_metadata: tuple[InstrumentMetadataObservation, ...] = ()


def _validate_envelope_provenance(items: tuple[Any, ...], envelope: Provenance) -> None:
    raw_provenance = [getattr(item, "provenance", None) for item in items]
    if any(not isinstance(provenance, Provenance) for provenance in raw_provenance):
        raise ValueError("every observation must include provenance")
    item_provenance = cast(list[Provenance], raw_provenance)
    if any(provenance.source != envelope.source for provenance in item_provenance):
        raise ValueError("one provider response must not silently mix sources")
    if any(provenance.timezone != envelope.timezone for provenance in item_provenance):
        raise ValueError("one provider response must not silently mix source timezones")
    if any(provenance.received_at > envelope.received_at for provenance in item_provenance):
        raise ValueError("envelope received_at must not precede an item")
    if any(provenance.observed_at > envelope.observed_at for provenance in item_provenance):
        raise ValueError("envelope observed_at must not precede an item")
    if any(provenance.is_delayed for provenance in item_provenance) and not envelope.is_delayed:
        raise ValueError("a delayed item requires a delayed response envelope")
    item_flags = {flag for provenance in item_provenance for flag in provenance.quality_flags}
    if not item_flags.issubset(set(envelope.quality_flags)):
        raise ValueError("response quality_flags must include every item quality flag")


__all__ = [
    "DataRequest",
    "FakeDataset",
    "FundamentalRequest",
    "InstrumentId",
    "InstrumentMetadataObservation",
    "InstrumentMetadataRequest",
    "NewsEventRequest",
    "ObservationBatch",
    "ObservationPage",
    "OhlcvPage",
    "OhlcvRequest",
    "PageCursor",
    "PaginatedDataRequest",
    "PriceAdjustment",
    "PriceBook",
    "PriceBookRequest",
    "PriceLevel",
    "QuoteRequest",
]
