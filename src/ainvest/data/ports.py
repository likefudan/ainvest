"""Read-only provider ports and stable data-error taxonomy (P04-T0).

The interfaces are synchronous and accept an explicit bounded timeout on every
request.  Adapters may use synchronous SDKs directly or hide async transports
behind their own boundary, but upper layers see only normalized ainvest models.

Live quotes and books have dedicated single-capability protocols.  They expose
no provider list, fallback chain, or automatic failover operation (DEC-003).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import ClassVar, Literal, Protocol, runtime_checkable

from ainvest.data.models import (
    FundamentalObservation,
    FundamentalRequest,
    InstrumentMetadataObservation,
    InstrumentMetadataRequest,
    NewsEventObservation,
    NewsEventRequest,
    ObservationBatch,
    ObservationPage,
    OhlcvPage,
    OhlcvRequest,
    PriceBook,
    PriceBookRequest,
    QuoteRequest,
)
from ainvest.schemas.common import MachineCode, SourceId
from ainvest.schemas.market import MarketQuote

ROBINHOOD_LIVE_QUOTE_CAPABILITY = "robinhood.mcp.get_equity_quotes"
ROBINHOOD_LIVE_PRICE_BOOK_CAPABILITY = "robinhood.mcp.get_equity_price_book"


class DataOperation(StrEnum):
    """Stable operation identifiers for errors, metrics, and tests."""

    DATASET = "DATASET"
    QUOTES = "QUOTES"
    PRICE_BOOKS = "PRICE_BOOKS"
    OHLCV = "OHLCV"
    FUNDAMENTALS = "FUNDAMENTALS"
    NEWS_EVENTS = "NEWS_EVENTS"
    INSTRUMENT_METADATA = "INSTRUMENT_METADATA"


class DataErrorCode(StrEnum):
    """Provider-independent error categories; messages are never control flow."""

    AUTH = "AUTH"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    NOT_FOUND = "NOT_FOUND"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNSUPPORTED = "UNSUPPORTED"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    SCHEMA_INCOMPATIBLE = "SCHEMA_INCOMPATIBLE"
    INCOMPLETE_DATA = "INCOMPLETE_DATA"
    STALE_DATA = "STALE_DATA"
    CONFLICTING_DATA = "CONFLICTING_DATA"


class DataProviderError(Exception):
    """Base normalized provider failure with a stable machine-readable code."""

    code: ClassVar[DataErrorCode]
    retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        operation: DataOperation,
        reason_code: MachineCode,
        source: SourceId | None = None,
        details: Mapping[str, str] | None = None,
    ) -> None:
        if type(self) is DataProviderError:
            raise TypeError("DataProviderError is abstract; instantiate a concrete subclass")
        super().__init__(message)
        self.operation = operation
        self.reason_code = reason_code
        self.source = source
        self.details: Mapping[str, str] = dict(details or {})


class DataAuthError(DataProviderError):
    """Provider authentication or authorization failed."""

    code: ClassVar[DataErrorCode] = DataErrorCode.AUTH


class DataTimeoutError(DataProviderError):
    """A read-only provider request exceeded its deadline."""

    code: ClassVar[DataErrorCode] = DataErrorCode.TIMEOUT
    retryable: ClassVar[bool] = True


class DataRateLimitError(DataProviderError):
    """The provider returned rate-limit or back-pressure."""

    code: ClassVar[DataErrorCode] = DataErrorCode.RATE_LIMIT
    retryable: ClassVar[bool] = True


class DataNotFoundError(DataProviderError):
    """The requested provider data does not exist."""

    code: ClassVar[DataErrorCode] = DataErrorCode.NOT_FOUND


class DataInvalidRequestError(DataProviderError):
    """The normalized request is unsupported or internally inconsistent."""

    code: ClassVar[DataErrorCode] = DataErrorCode.INVALID_REQUEST


class DataUnsupportedError(DataProviderError):
    """The provider does not expose the requested capability."""

    code: ClassVar[DataErrorCode] = DataErrorCode.UNSUPPORTED


class DataUpstreamError(DataProviderError):
    """The provider failed without a safe, more specific classification."""

    code: ClassVar[DataErrorCode] = DataErrorCode.PROVIDER_FAILURE


class DataSchemaError(DataProviderError):
    """The provider response is incompatible with the pinned contract."""

    code: ClassVar[DataErrorCode] = DataErrorCode.SCHEMA_INCOMPATIBLE


class DataIncompleteError(DataProviderError):
    """Required source, timestamp, or value fields are absent."""

    code: ClassVar[DataErrorCode] = DataErrorCode.INCOMPLETE_DATA


class DataStaleError(DataProviderError):
    """The observation is older than the caller's accepted cutoff."""

    code: ClassVar[DataErrorCode] = DataErrorCode.STALE_DATA


class DataConflictError(DataProviderError):
    """Required sources or fields conflict and no safe result exists."""

    code: ClassVar[DataErrorCode] = DataErrorCode.CONFLICTING_DATA


@runtime_checkable
class QuotePort(Protocol):
    """Current quote observations for research or explicitly offline use."""

    @property
    def source_id(self) -> SourceId:
        """Stable provider/capability identifier."""
        ...

    def get_quotes(self, request: QuoteRequest) -> ObservationBatch[MarketQuote]:
        """Return normalized quotes or raise :class:`DataProviderError`."""
        ...


@runtime_checkable
class PriceBookPort(Protocol):
    """Normalized Level 2 books."""

    @property
    def source_id(self) -> SourceId:
        """Stable provider/capability identifier."""
        ...

    def get_price_books(self, request: PriceBookRequest) -> ObservationBatch[PriceBook]:
        """Return normalized books or raise :class:`DataProviderError`."""
        ...


@runtime_checkable
class OhlcvPort(Protocol):
    """Historical price/volume observations with explicit adjustment."""

    @property
    def source_id(self) -> SourceId:
        """Stable provider/capability identifier."""
        ...

    def get_ohlcv(self, request: OhlcvRequest) -> OhlcvPage:
        """Return one page of normalized historical bars."""
        ...


@runtime_checkable
class FundamentalsPort(Protocol):
    """Standardized point-in-time fundamental facts."""

    @property
    def source_id(self) -> SourceId:
        """Stable provider/capability identifier."""
        ...

    def get_fundamentals(
        self,
        request: FundamentalRequest,
    ) -> ObservationPage[FundamentalObservation]:
        """Return one page of standardized fundamental snapshots."""
        ...


@runtime_checkable
class NewsEventPort(Protocol):
    """Company, industry, and macro news/event discovery."""

    @property
    def source_id(self) -> SourceId:
        """Stable provider/capability identifier."""
        ...

    def get_news_events(self, request: NewsEventRequest) -> ObservationPage[NewsEventObservation]:
        """Return one page of normalized events."""
        ...


@runtime_checkable
class InstrumentMetadataPort(Protocol):
    """Instrument identity, precision, and tradability observations."""

    @property
    def source_id(self) -> SourceId:
        """Stable provider/capability identifier."""
        ...

    def get_instrument_metadata(
        self,
        request: InstrumentMetadataRequest,
    ) -> ObservationPage[InstrumentMetadataObservation]:
        """Return one page of normalized instrument metadata."""
        ...


@runtime_checkable
class LiveQuotePort(QuotePort, Protocol):
    """Robinhood MCP live quote capability, with no automatic fallback."""

    @property
    def live_quote_capability(
        self,
    ) -> Literal["robinhood.mcp.get_equity_quotes"]:
        """Pinned MCP capability implemented by this port."""
        ...


@runtime_checkable
class LivePriceBookPort(PriceBookPort, Protocol):
    """Robinhood MCP live price-book capability, with no automatic fallback."""

    @property
    def live_price_book_capability(
        self,
    ) -> Literal["robinhood.mcp.get_equity_price_book"]:
        """Pinned MCP capability implemented by this port."""
        ...


__all__ = [
    "ROBINHOOD_LIVE_PRICE_BOOK_CAPABILITY",
    "ROBINHOOD_LIVE_QUOTE_CAPABILITY",
    "DataAuthError",
    "DataConflictError",
    "DataErrorCode",
    "DataIncompleteError",
    "DataInvalidRequestError",
    "DataNotFoundError",
    "DataOperation",
    "DataProviderError",
    "DataRateLimitError",
    "DataSchemaError",
    "DataStaleError",
    "DataTimeoutError",
    "DataUnsupportedError",
    "DataUpstreamError",
    "FundamentalsPort",
    "InstrumentMetadataPort",
    "LivePriceBookPort",
    "LiveQuotePort",
    "NewsEventPort",
    "OhlcvPort",
    "PriceBookPort",
    "QuotePort",
]
