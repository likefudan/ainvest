"""Deterministic no-network data provider and reusable fixture dataset (P04-T0)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NoReturn, TypeVar

from ainvest.data.models import (
    FakeDataset,
    FundamentalRequest,
    InstrumentMetadataObservation,
    InstrumentMetadataRequest,
    NewsEventRequest,
    ObservationBatch,
    ObservationPage,
    OhlcvPage,
    OhlcvRequest,
    PriceAdjustment,
    PriceBook,
    PriceBookRequest,
    QuoteRequest,
)
from ainvest.data.ports import (
    DataAuthError,
    DataConflictError,
    DataErrorCode,
    DataIncompleteError,
    DataInvalidRequestError,
    DataNotFoundError,
    DataOperation,
    DataProviderError,
    DataRateLimitError,
    DataSchemaError,
    DataStaleError,
    DataTimeoutError,
    DataUnsupportedError,
    DataUpstreamError,
)
from ainvest.schemas.common import (
    InstrumentIdentity,
    Provenance,
    QualityFlag,
    SourceId,
)
from ainvest.schemas.market import (
    FactValueKind,
    FundamentalSnapshot,
    MarketEvent,
    MarketQuote,
    OhlcvBar,
)

ItemT = TypeVar(
    "ItemT",
    MarketQuote,
    PriceBook,
    OhlcvBar,
    FundamentalSnapshot,
    MarketEvent,
    InstrumentMetadataObservation,
)

_ERROR_TYPES: Mapping[DataErrorCode, type[DataProviderError]] = {
    DataErrorCode.AUTH: DataAuthError,
    DataErrorCode.TIMEOUT: DataTimeoutError,
    DataErrorCode.RATE_LIMIT: DataRateLimitError,
    DataErrorCode.NOT_FOUND: DataNotFoundError,
    DataErrorCode.INVALID_REQUEST: DataInvalidRequestError,
    DataErrorCode.UNSUPPORTED: DataUnsupportedError,
    DataErrorCode.PROVIDER_FAILURE: DataUpstreamError,
    DataErrorCode.SCHEMA_INCOMPATIBLE: DataSchemaError,
    DataErrorCode.INCOMPLETE_DATA: DataIncompleteError,
    DataErrorCode.STALE_DATA: DataStaleError,
    DataErrorCode.CONFLICTING_DATA: DataConflictError,
}


class DeterministicFakeDataProvider:
    """Read-only provider backed only by an immutable normalized dataset.

    The fake has no clock and never performs I/O. Identical requests against an
    identical dataset therefore produce byte-for-byte equivalent model dumps.
    Failures may be injected by stable operation/error code.
    """

    def __init__(
        self,
        dataset: FakeDataset | None = None,
        *,
        failures: Mapping[DataOperation, DataErrorCode] | None = None,
    ) -> None:
        self._dataset = dataset or fixture_dataset()
        self._failures = dict(failures or {})

    @property
    def source_id(self) -> SourceId:
        """Stable source identifier for this fixture provider."""
        return self._dataset.provenance.source

    def get_quotes(self, request: QuoteRequest) -> ObservationBatch[MarketQuote]:
        self._raise_injected(DataOperation.QUOTES)
        by_id = {item.instrument.instrument_id: item for item in self._dataset.quotes}
        self._require_all(request.instrument_ids, by_id, DataOperation.QUOTES)
        items = tuple(by_id[instrument_id] for instrument_id in request.instrument_ids)
        return ObservationBatch(items=items, provenance=self._envelope(items))

    def get_price_books(self, request: PriceBookRequest) -> ObservationBatch[PriceBook]:
        self._raise_injected(DataOperation.PRICE_BOOKS)
        by_id = {item.instrument.instrument_id: item for item in self._dataset.price_books}
        self._require_all(request.instrument_ids, by_id, DataOperation.PRICE_BOOKS)
        items = tuple(
            PriceBook(
                instrument=by_id[instrument_id].instrument,
                bids=by_id[instrument_id].bids[: request.depth],
                asks=by_id[instrument_id].asks[: request.depth],
                provenance=by_id[instrument_id].provenance,
            )
            for instrument_id in request.instrument_ids
        )
        return ObservationBatch(items=items, provenance=self._envelope(items))

    def get_ohlcv(self, request: OhlcvRequest) -> OhlcvPage:
        self._raise_injected(DataOperation.OHLCV)
        if request.adjustment is not PriceAdjustment.RAW:
            raise DataUnsupportedError(
                "fixture dataset contains raw bars only",
                operation=DataOperation.OHLCV,
                reason_code="FAKE_ADJUSTMENT_UNSUPPORTED",
                source=self.source_id,
            )
        filtered = tuple(
            bar
            for bar in self._dataset.ohlcv
            if bar.instrument.instrument_id == request.instrument_id
            and bar.interval == request.interval
            and request.start_at <= bar.bar_start < request.end_at
        )
        selected, next_cursor = self._select_page(filtered, request, DataOperation.OHLCV)
        return OhlcvPage(
            items=selected,
            next_cursor=next_cursor,
            provenance=self._envelope(selected),
            instrument_id=request.instrument_id,
            interval=request.interval,
            adjustment=request.adjustment,
        )

    def get_fundamentals(
        self,
        request: FundamentalRequest,
    ) -> ObservationPage[FundamentalSnapshot]:
        self._raise_injected(DataOperation.FUNDAMENTALS)
        filtered = tuple(
            snapshot
            for snapshot in self._dataset.fundamentals
            if snapshot.symbol in request.symbols and snapshot.as_of <= request.as_of
        )
        return self._page(filtered, request, DataOperation.FUNDAMENTALS)

    def get_news_events(self, request: NewsEventRequest) -> ObservationPage[MarketEvent]:
        self._raise_injected(DataOperation.NEWS_EVENTS)
        event_types = set(request.event_types)
        symbols = set(request.symbols)
        filtered = tuple(
            event
            for event in self._dataset.news_events
            if request.start_at <= event.occurred_at < request.end_at
            and (not symbols or event.symbol in symbols)
            and (not event_types or event.event_type in event_types)
        )
        return self._page(filtered, request, DataOperation.NEWS_EVENTS)

    def get_instrument_metadata(
        self,
        request: InstrumentMetadataRequest,
    ) -> ObservationPage[InstrumentMetadataObservation]:
        self._raise_injected(DataOperation.INSTRUMENT_METADATA)
        instrument_ids = set(request.instrument_ids)
        symbols = set(request.symbols)
        by_id = {item.instrument.instrument_id: item for item in self._dataset.instrument_metadata}
        by_symbol = {item.instrument.symbol: item for item in self._dataset.instrument_metadata}
        self._require_all(
            request.instrument_ids,
            by_id,
            DataOperation.INSTRUMENT_METADATA,
        )
        self._require_all(
            request.symbols,
            by_symbol,
            DataOperation.INSTRUMENT_METADATA,
        )
        filtered = tuple(
            item
            for item in self._dataset.instrument_metadata
            if item.instrument.instrument_id in instrument_ids or item.instrument.symbol in symbols
        )
        return self._page(filtered, request, DataOperation.INSTRUMENT_METADATA)

    def _page(
        self,
        items: tuple[ItemT, ...],
        request: OhlcvRequest | FundamentalRequest | NewsEventRequest | InstrumentMetadataRequest,
        operation: DataOperation,
    ) -> ObservationPage[ItemT]:
        selected, next_cursor = self._select_page(items, request, operation)
        return ObservationPage(
            items=selected,
            next_cursor=next_cursor,
            provenance=self._envelope(selected),
        )

    def _select_page(
        self,
        items: tuple[ItemT, ...],
        request: OhlcvRequest | FundamentalRequest | NewsEventRequest | InstrumentMetadataRequest,
        operation: DataOperation,
    ) -> tuple[tuple[ItemT, ...], str | None]:
        offset = self._decode_cursor(request.cursor, operation)
        selected = items[offset : offset + request.page_size]
        next_offset = offset + len(selected)
        next_cursor = (
            self._encode_cursor(next_offset, operation) if next_offset < len(items) else None
        )
        return selected, next_cursor

    def _envelope(self, items: Sequence[ItemT]) -> Provenance:
        if not items:
            return self._dataset.provenance
        provenances = tuple(item.provenance for item in items)
        flags: tuple[QualityFlag, ...] = tuple(
            sorted(
                {flag for provenance in provenances for flag in provenance.quality_flags},
                key=str,
            )
        )
        delayed = any(provenance.is_delayed for provenance in provenances)
        if delayed and QualityFlag.DELAYED not in flags:
            flags = (*flags, QualityFlag.DELAYED)
        return Provenance(
            source=self.source_id,
            observed_at=max(provenance.observed_at for provenance in provenances),
            received_at=max(provenance.received_at for provenance in provenances),
            timezone="UTC",
            is_delayed=delayed,
            quality_flags=flags,
        )

    def _require_all(
        self,
        requested_ids: tuple[str, ...],
        available: Mapping[str, object],
        operation: DataOperation,
    ) -> None:
        missing = tuple(
            instrument_id for instrument_id in requested_ids if instrument_id not in available
        )
        if missing:
            raise DataNotFoundError(
                "one or more requested instruments are absent from the fixture dataset",
                operation=operation,
                reason_code="FAKE_INSTRUMENT_NOT_FOUND",
                source=self.source_id,
                details={"missing_count": str(len(missing))},
            )

    def _raise_injected(self, operation: DataOperation) -> None:
        code = self._failures.get(operation)
        if code is None:
            return
        error_type = _ERROR_TYPES[code]
        raise error_type(
            "deterministic injected provider failure",
            operation=operation,
            reason_code=f"FAKE_{code.value}",
            source=self.source_id,
        )

    def _encode_cursor(self, offset: int, operation: DataOperation) -> str:
        return f"{self._dataset.dataset_id}:{operation.value}:{offset}"

    def _decode_cursor(self, cursor: str | None, operation: DataOperation) -> int:
        if cursor is None:
            return 0
        prefix = f"{self._dataset.dataset_id}:{operation.value}:"
        if not cursor.startswith(prefix):
            self._invalid_cursor(operation)
        raw_offset = cursor.removeprefix(prefix)
        if not raw_offset.isdigit():
            self._invalid_cursor(operation)
        return int(raw_offset)

    def _invalid_cursor(self, operation: DataOperation) -> NoReturn:
        raise DataInvalidRequestError(
            "cursor does not belong to this dataset and operation",
            operation=operation,
            reason_code="INVALID_PAGE_CURSOR",
            source=self.source_id,
        )


def fixture_dataset() -> FakeDataset:
    """Return the canonical two-instrument P04-T0 fixture dataset."""
    source = "ainvest.fake.v1"
    envelope = _provenance(source=source, observed_at="2026-07-24T18:30:00Z")
    aapl = _instrument(
        instrument_id="rh_inst_aapl_xnas",
        symbol="AAPL",
        exchange="XNAS",
        source=source,
    )
    spy = _instrument(
        instrument_id="rh_inst_spy_arcx",
        symbol="SPY",
        exchange="ARCX",
        asset_type="ETF",
        source=source,
    )
    return FakeDataset(
        dataset_id="data_fixture_v1",
        provenance=envelope,
        quotes=(
            _quote(aapl, "215.42", "215.40", "215.44", source),
            _quote(spy, "636.12", "636.10", "636.14", source),
        ),
        price_books=(
            _book(aapl, (("215.40", "200"), ("215.39", "150")), (("215.44", "180"),), source),
            _book(spy, (("636.10", "75"),), (("636.14", "90"),), source),
        ),
        ohlcv=(
            _bar(aapl, "2026-07-22T00:00:00Z", "210", "214", "209", "213", source),
            _bar(aapl, "2026-07-23T00:00:00Z", "213", "216", "212", "215.42", source),
            _bar(spy, "2026-07-23T00:00:00Z", "631", "638", "630", "636.12", source),
        ),
        fundamentals=(
            FundamentalSnapshot.model_validate(
                {
                    "symbol": "AAPL",
                    "as_of": "2026-07-24T18:30:00Z",
                    "facts": (
                        {
                            "key": "market_cap_usd",
                            "kind": FactValueKind.DECIMAL,
                            "decimal_value": "3200000000000",
                            "unit": "USD",
                        },
                    ),
                    "provenance": envelope,
                }
            ),
        ),
        news_events=(
            MarketEvent.model_validate(
                {
                    "event_id": "event_aapl_earnings_2026q3",
                    "symbol": "AAPL",
                    "event_type": "EARNINGS",
                    "headline": "Synthetic earnings fixture",
                    "occurred_at": "2026-07-23T20:00:00Z",
                    "provenance": _provenance(
                        source=source,
                        observed_at="2026-07-23T20:01:00Z",
                    ),
                }
            ),
            MarketEvent.model_validate(
                {
                    "event_id": "event_macro_rates_20260724",
                    "event_type": "MACRO_RATE",
                    "headline": "Synthetic macro fixture",
                    "occurred_at": "2026-07-24T14:00:00Z",
                    "provenance": _provenance(
                        source=source,
                        observed_at="2026-07-24T14:01:00Z",
                    ),
                }
            ),
        ),
        instrument_metadata=(
            _metadata(aapl, source),
            _metadata(spy, source),
        ),
    )


def _instrument(
    *,
    instrument_id: str,
    symbol: str,
    exchange: str,
    source: str,
    asset_type: str = "EQUITY",
) -> InstrumentIdentity:
    return InstrumentIdentity.model_validate(
        {
            "instrument_id": instrument_id,
            "symbol": symbol,
            "exchange": exchange,
            "currency": "USD",
            "asset_type": asset_type,
            "identity_as_of": "2026-07-24T18:30:00Z",
            "provider": source,
        }
    )


def _provenance(*, source: str, observed_at: str) -> Provenance:
    return Provenance.model_validate(
        {
            "source": source,
            "observed_at": observed_at,
            "received_at": "2026-07-24T18:30:00Z",
            "timezone": "UTC",
            "is_delayed": False,
            "quality_flags": (),
        }
    )


def _quote(
    instrument: InstrumentIdentity,
    last: str,
    bid: str,
    ask: str,
    source: str,
) -> MarketQuote:
    return MarketQuote.model_validate(
        {
            "instrument": instrument,
            "last_price": last,
            "bid": bid,
            "ask": ask,
            "provenance": _provenance(source=source, observed_at="2026-07-24T18:29:58Z"),
        }
    )


def _book(
    instrument: InstrumentIdentity,
    bids: tuple[tuple[str, str], ...],
    asks: tuple[tuple[str, str], ...],
    source: str,
) -> PriceBook:
    return PriceBook.model_validate(
        {
            "instrument": instrument,
            "bids": tuple({"price": price, "quantity": quantity} for price, quantity in bids),
            "asks": tuple({"price": price, "quantity": quantity} for price, quantity in asks),
            "provenance": _provenance(source=source, observed_at="2026-07-24T18:29:58Z"),
        }
    )


def _bar(
    instrument: InstrumentIdentity,
    bar_start: str,
    open_price: str,
    high: str,
    low: str,
    close: str,
    source: str,
) -> OhlcvBar:
    return OhlcvBar.model_validate(
        {
            "instrument": instrument,
            "interval": "1d",
            "bar_start": bar_start,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": "1000000",
            "provenance": _provenance(source=source, observed_at="2026-07-24T18:30:00Z"),
        }
    )


def _metadata(
    instrument: InstrumentIdentity,
    source: str,
) -> InstrumentMetadataObservation:
    return InstrumentMetadataObservation.model_validate(
        {
            "instrument": instrument,
            "tradable": True,
            "price_increment": "0.01",
            "quantity_increment": "1",
            "provenance": _provenance(source=source, observed_at="2026-07-24T18:30:00Z"),
        }
    )


__all__ = [
    "DeterministicFakeDataProvider",
    "fixture_dataset",
]
