"""Provider-independent request, response, and observation models (P04-T0).

Provider adapters normalize their outputs into these models before returning
control to research, strategy, or risk code.  No provider SDK type may cross
this boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from enum import StrEnum
from ipaddress import ip_address
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    AfterValidator,
    AnyUrl,
    Field,
    StrictBool,
    StringConstraints,
    UrlConstraints,
    model_validator,
)

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    CurrencyCode,
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
    FactValueKind,
    FundamentalSnapshot,
    MarketEvent,
    MarketQuote,
    OhlcvBar,
)
from ainvest.schemas.research import EvidenceCitation, EvidenceKind

InstrumentId = Annotated[str, StringConstraints(min_length=3, max_length=128)]
PageCursor = Annotated[str, StringConstraints(min_length=1, max_length=512)]
AccessionNumber = Annotated[
    str,
    StringConstraints(pattern=r"^\d{10}-\d{2}-\d{6}$", min_length=20, max_length=20),
]
SecFormType = Annotated[
    str,
    StringConstraints(
        pattern=r"^[A-Z0-9]+(?:[ -][A-Z0-9]+)*(?:/A)?$",
        min_length=1,
        max_length=24,
    ),
]


def _validate_external_https_url(value: AnyUrl) -> AnyUrl:
    """Apply the data-boundary URL policy after Pydantic parses the URL."""
    if value.username is not None or value.password is not None:
        raise ValueError("external HTTPS URLs must not contain credentials")
    if value.fragment is not None:
        raise ValueError("external HTTPS URLs must not contain fragments")

    host = value.host
    if host is None:
        raise ValueError("external HTTPS URLs require a host")
    candidate = host.removeprefix("[").removesuffix("]")
    try:
        ip_address(candidate)
    except ValueError:
        is_ip_address = False
    else:
        is_ip_address = True
    if not is_ip_address:
        if len(host) > 253 or host.endswith("."):
            raise ValueError("external HTTPS URL host is malformed")
        labels = host.split(".")
        if any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not label.isascii()
            or not label.replace("-", "").isalnum()
            for label in labels
        ):
            raise ValueError("external HTTPS URL host is malformed")
    return value


ExternalHttpsUrl = Annotated[
    AnyUrl,
    UrlConstraints(allowed_schemes=["https"], host_required=True, max_length=2048),
    AfterValidator(_validate_external_https_url),
]


class PriceAdjustment(StrEnum):
    """Price adjustment requested for historical bars."""

    RAW = "RAW"
    SPLIT = "SPLIT"
    SPLIT_AND_DIVIDEND = "SPLIT_AND_DIVIDEND"


class TimeCertainty(StrEnum):
    """Whether an earnings/event time is authoritative or estimated."""

    CONFIRMED = "CONFIRMED"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class CorporateActionType(StrEnum):
    """Provider-independent corporate-action categories."""

    SPLIT = "SPLIT"
    DIVIDEND = "DIVIDEND"


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


class CorporateActionRequest(PaginatedDataRequest):
    """Request actions effective in a closed-open date window."""

    instrument_ids: Annotated[tuple[InstrumentId, ...], Field(min_length=1, max_length=100)]
    effective_from: date
    effective_to: date

    @model_validator(mode="after")
    def _valid_filters(self) -> Self:
        if self.effective_from >= self.effective_to:
            raise ValueError("effective_from must be before effective_to")
        if len(self.instrument_ids) != len(set(self.instrument_ids)):
            raise ValueError("instrument_ids must be unique")
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
            (not self.bids or not self.asks)
            and QualityFlag.MISSING_FIELDS not in self.provenance.quality_flags
            and QualityFlag.PARTIAL not in self.provenance.quality_flags
        ):
            raise ValueError("a one-sided or empty price book requires MISSING_FIELDS or PARTIAL")
        return self


class ReportingPeriod(DomainModel):
    """Provider-independent fiscal period attached to normalized fundamentals."""

    start_date: date
    end_date: date
    fiscal_year: Annotated[int, Field(ge=1900, le=9999)]
    fiscal_period: Annotated[
        str,
        StringConstraints(pattern=r"^(FY|Q[1-4]|H[12]|TTM)$", min_length=2, max_length=3),
    ]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.start_date > self.end_date:
            raise ValueError("reporting period start_date must be <= end_date")
        return self


class FilingReference(DomainModel):
    """Canonical SEC/company filing reference with evidence provenance."""

    accession_number: AccessionNumber
    form_type: SecFormType
    filed_at: UtcDateTime
    report_period_end: date
    primary_document_url: ExternalHttpsUrl
    provenance: Provenance

    @model_validator(mode="after")
    def _period_precedes_filing(self) -> Self:
        if self.report_period_end > self.filed_at.date():
            raise ValueError("report_period_end must be <= filed_at date")
        return self


class FundamentalObservation(DomainModel):
    """Provider-independent point-in-time normalized fundamentals."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    instrument: InstrumentIdentity
    snapshot: FundamentalSnapshot
    period: ReportingPeriod
    reporting_context: MachineCode = "CONSOLIDATED"
    currency: CurrencyCode
    earnings_at: UtcDateTime | None
    earnings_time_certainty: TimeCertainty
    citations: Annotated[tuple[EvidenceCitation, ...], Field(max_length=100)] = ()

    @property
    def provenance(self) -> Provenance:
        """Observation provenance reused from the normalized snapshot."""
        return self.snapshot.provenance

    @model_validator(mode="after")
    def _bindings_are_consistent(self) -> Self:
        if self.instrument.symbol != self.snapshot.symbol:
            raise ValueError("instrument.symbol must match snapshot.symbol")
        if self.instrument.currency != self.currency:
            raise ValueError("currency must match instrument.currency")
        unitless_decimal_keys = tuple(
            fact.key
            for fact in self.snapshot.facts
            if fact.kind is FactValueKind.DECIMAL and fact.unit is None
        )
        if unitless_decimal_keys:
            raise ValueError("decimal fundamental facts require an explicit unit")
        if self.earnings_time_certainty is TimeCertainty.UNKNOWN:
            if self.earnings_at is not None:
                raise ValueError("UNKNOWN earnings certainty requires earnings_at=None")
        elif self.earnings_at is None:
            raise ValueError("confirmed/estimated earnings certainty requires earnings_at")
        citation_ids = [citation.evidence_id for citation in self.citations]
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("fundamental citation evidence_ids must be unique")
        if not hasattr(self, "filing") and any(
            citation.kind is EvidenceKind.FILING for citation in self.citations
        ):
            raise ValueError("filing citations require SecFundamentalObservation")
        return self


class SecFundamentalObservation(FundamentalObservation):
    """Fundamentals whose SEC evidence is bound to one filing accession."""

    filing: FilingReference
    citations: Annotated[tuple[EvidenceCitation, ...], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def _sec_evidence_is_consistent(self) -> Self:
        if self.period.end_date > self.filing.report_period_end:
            raise ValueError("fact period.end_date must be <= filing.report_period_end")
        if self.filing.filed_at > self.snapshot.as_of:
            raise ValueError("filing.filed_at must be <= snapshot.as_of")
        if not any(
            citation.kind is EvidenceKind.FILING
            and self.filing.accession_number in citation.locator
            for citation in self.citations
        ):
            raise ValueError("SEC fundamentals require an accession-bound filing citation")
        return self


class CorporateActionObservation(DomainModel):
    """Common immutable identity, timing, and provenance for one action."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    action_id: StableId
    instrument: InstrumentIdentity
    effective_date: date
    declared_date: date | None = None
    provenance: Provenance

    @model_validator(mode="after")
    def _declared_before_effective(self) -> Self:
        if self.declared_date is not None and self.declared_date > self.effective_date:
            raise ValueError("declared_date must be <= effective_date")
        if (
            self.declared_date is None
            and QualityFlag.MISSING_FIELDS not in self.provenance.quality_flags
        ):
            raise ValueError("missing declared_date requires MISSING_FIELDS")
        return self


class SplitObservation(CorporateActionObservation):
    """Stock split represented as new shares received per old share."""

    action_type: Literal[CorporateActionType.SPLIT] = CorporateActionType.SPLIT
    split_ratio: PositiveDecimal


class DividendObservation(CorporateActionObservation):
    """Cash dividend with currency and an optional quality-qualified pay date."""

    action_type: Literal[CorporateActionType.DIVIDEND] = CorporateActionType.DIVIDEND
    cash_amount: PositiveDecimal
    currency: CurrencyCode
    pay_date: date | None = None

    @model_validator(mode="after")
    def _dividend_is_consistent(self) -> Self:
        if self.currency != self.instrument.currency:
            raise ValueError("dividend currency must match instrument.currency")
        if self.pay_date is not None and self.pay_date < self.effective_date:
            raise ValueError("pay_date must be >= effective_date")
        if (
            self.pay_date is None
            and QualityFlag.MISSING_FIELDS not in self.provenance.quality_flags
        ):
            raise ValueError("missing pay_date requires MISSING_FIELDS")
        return self


CorporateAction = Annotated[
    SplitObservation | DividendObservation,
    Field(discriminator="action_type"),
]


class NewsEventObservation(DomainModel):
    """News/event record with publisher, license, symbols, and citations."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    event: MarketEvent
    symbols: Annotated[tuple[Symbol, ...], Field(max_length=100)] = ()
    url: ExternalHttpsUrl
    publisher: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    published_at: UtcDateTime
    license_name: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    event_time_certainty: TimeCertainty
    citations: Annotated[tuple[EvidenceCitation, ...], Field(min_length=1, max_length=100)]
    related_filings: Annotated[tuple[FilingReference, ...], Field(max_length=20)] = ()

    @property
    def provenance(self) -> Provenance:
        """Observation provenance reused from the normalized event."""
        return self.event.provenance

    @model_validator(mode="after")
    def _bindings_are_consistent(self) -> Self:
        if len(self.symbols) != len(set(self.symbols)):
            raise ValueError("news/event symbols must be unique")
        if self.event.symbol is not None and self.event.symbol not in self.symbols:
            raise ValueError("event.symbol must be included in symbols")
        if self.published_at > self.provenance.observed_at:
            raise ValueError("published_at must be <= provenance.observed_at")
        citation_ids = [citation.evidence_id for citation in self.citations]
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("news/event citation evidence_ids must be unique")
        for filing in self.related_filings:
            if not any(
                citation.kind is EvidenceKind.FILING and filing.accession_number in citation.locator
                for citation in self.citations
            ):
                raise ValueError("related filings require accession-bound filing citations")
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
    fundamentals: tuple[SecFundamentalObservation | FundamentalObservation, ...] = ()
    corporate_actions: tuple[CorporateAction, ...] = ()
    news_events: tuple[NewsEventObservation, ...] = ()
    instrument_metadata: tuple[InstrumentMetadataObservation, ...] = ()

    @model_validator(mode="after")
    def _all_observations_match_dataset_envelope(self) -> Self:
        for items in (
            self.quotes,
            self.price_books,
            self.ohlcv,
            self.fundamentals,
            self.corporate_actions,
            self.news_events,
            self.instrument_metadata,
        ):
            _validate_envelope_provenance(
                items,
                self.provenance,
                require_quality_flags=False,
            )
        _require_unique_keys(
            self.quotes,
            lambda item: item.instrument.instrument_id,
            "quote instrument_id",
        )
        _require_unique_keys(
            self.price_books,
            lambda item: item.instrument.instrument_id,
            "price-book instrument_id",
        )
        _require_unique_keys(
            self.ohlcv,
            lambda item: (item.instrument.instrument_id, item.interval, item.bar_start),
            "OHLCV instrument/interval/bar_start",
        )
        _require_unique_keys(
            self.fundamentals,
            _fundamental_identity,
            "fundamental instrument/source/period/context",
        )
        _require_unique_keys(
            self.corporate_actions,
            lambda item: item.action_id,
            "corporate-action action_id",
        )
        _require_unique_keys(
            self.news_events,
            lambda item: item.event.event_id,
            "news/event event_id",
        )
        _require_unique_keys(
            self.instrument_metadata,
            lambda item: item.instrument.instrument_id,
            "metadata instrument_id",
        )
        _require_unique_keys(
            self.instrument_metadata,
            lambda item: item.instrument.symbol,
            "metadata symbol",
        )
        return self


def _fundamental_identity(
    item: SecFundamentalObservation | FundamentalObservation,
) -> tuple[object, ...]:
    source_identity: object
    if isinstance(item, SecFundamentalObservation):
        source_identity = item.filing.accession_number
    else:
        source_identity = item.snapshot.as_of
    return (
        item.instrument.instrument_id,
        source_identity,
        item.period.start_date,
        item.period.end_date,
        item.reporting_context,
    )


def _require_unique_keys(
    items: tuple[Any, ...],
    key: Callable[[Any], object],
    label: str,
) -> None:
    keys = [key(item) for item in items]
    if len(keys) != len(set(keys)):
        raise ValueError(f"fake dataset contains duplicate {label}")


def _validate_envelope_provenance(
    items: tuple[Any, ...],
    envelope: Provenance,
    *,
    require_quality_flags: bool = True,
) -> None:
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
    if require_quality_flags:
        item_flags = {flag for provenance in item_provenance for flag in provenance.quality_flags}
        if not item_flags.issubset(set(envelope.quality_flags)):
            raise ValueError("response quality_flags must include every item quality flag")


__all__ = [
    "CorporateAction",
    "CorporateActionObservation",
    "CorporateActionRequest",
    "CorporateActionType",
    "DataRequest",
    "DividendObservation",
    "ExternalHttpsUrl",
    "FakeDataset",
    "FilingReference",
    "FundamentalObservation",
    "FundamentalRequest",
    "InstrumentId",
    "InstrumentMetadataObservation",
    "InstrumentMetadataRequest",
    "NewsEventObservation",
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
    "ReportingPeriod",
    "SecFundamentalObservation",
    "SplitObservation",
    "TimeCertainty",
]
