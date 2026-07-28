"""Deterministic no-network data provider and reusable fixture dataset (P04-T0)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, NoReturn, TypeVar

from pydantic import BaseModel, ValidationError

from ainvest.data.models import (
    CorporateAction,
    CorporateActionObservation,
    CorporateActionRequest,
    DividendObservation,
    FakeDataset,
    FilingReference,
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
    PriceAdjustment,
    PriceBook,
    PriceBookRequest,
    QuoteRequest,
    ReportingPeriod,
    SecFundamentalObservation,
    SplitObservation,
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
from ainvest.schemas.research import EvidenceCitation, EvidenceKind

ItemT = TypeVar(
    "ItemT",
    bound=(
        MarketQuote
        | PriceBook
        | OhlcvBar
        | FundamentalObservation
        | CorporateActionObservation
        | NewsEventObservation
        | InstrumentMetadataObservation
    ),
)
ModelT = TypeVar("ModelT", bound=BaseModel)
PaginatedFakeRequest = (
    OhlcvRequest
    | FundamentalRequest
    | CorporateActionRequest
    | NewsEventRequest
    | InstrumentMetadataRequest
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
        dataset: FakeDataset | Mapping[str, Any] | None = None,
        *,
        failures: Mapping[DataOperation, DataErrorCode] | None = None,
    ) -> None:
        try:
            if dataset is None:
                normalized_dataset = fixture_dataset()
            elif isinstance(dataset, FakeDataset):
                normalized_dataset = dataset
            else:
                normalized_dataset = FakeDataset.model_validate(dataset)
        except ValidationError as exc:
            raise DataSchemaError(
                "fake provider dataset is inconsistent",
                operation=DataOperation.DATASET,
                reason_code="FAKE_DATASET_INVALID",
                details={"error_count": str(exc.error_count())},
            ) from exc
        self._dataset = normalized_dataset
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
        return self._build(
            ObservationBatch[MarketQuote],
            DataOperation.QUOTES,
            items=items,
            provenance=self._envelope(items, DataOperation.QUOTES),
        )

    def get_price_books(self, request: PriceBookRequest) -> ObservationBatch[PriceBook]:
        self._raise_injected(DataOperation.PRICE_BOOKS)
        by_id = {item.instrument.instrument_id: item for item in self._dataset.price_books}
        self._require_all(request.instrument_ids, by_id, DataOperation.PRICE_BOOKS)
        items = tuple(
            self._build(
                PriceBook,
                DataOperation.PRICE_BOOKS,
                instrument=by_id[instrument_id].instrument,
                bids=by_id[instrument_id].bids[: request.depth],
                asks=by_id[instrument_id].asks[: request.depth],
                provenance=by_id[instrument_id].provenance,
            )
            for instrument_id in request.instrument_ids
        )
        return self._build(
            ObservationBatch[PriceBook],
            DataOperation.PRICE_BOOKS,
            items=items,
            provenance=self._envelope(items, DataOperation.PRICE_BOOKS),
        )

    def get_ohlcv(self, request: OhlcvRequest) -> OhlcvPage:
        self._raise_injected(DataOperation.OHLCV)
        if request.adjustment is not PriceAdjustment.RAW:
            raise DataUnsupportedError(
                "fixture dataset contains raw bars only",
                operation=DataOperation.OHLCV,
                reason_code="FAKE_ADJUSTMENT_UNSUPPORTED",
                source=self.source_id,
            )
        known_instruments = {
            item.instrument.instrument_id for item in self._dataset.instrument_metadata
        }
        if request.instrument_id not in known_instruments:
            raise DataNotFoundError(
                "requested OHLCV instrument is unknown",
                operation=DataOperation.OHLCV,
                reason_code="FAKE_INSTRUMENT_NOT_FOUND",
                source=self.source_id,
            )
        available_series = {
            (bar.instrument.instrument_id, bar.interval) for bar in self._dataset.ohlcv
        }
        if (request.instrument_id, request.interval) not in available_series:
            raise DataNotFoundError(
                "requested OHLCV series is unavailable",
                operation=DataOperation.OHLCV,
                reason_code="FAKE_SERIES_NOT_FOUND",
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
        return self._build(
            OhlcvPage,
            DataOperation.OHLCV,
            items=selected,
            next_cursor=next_cursor,
            provenance=self._envelope(selected, DataOperation.OHLCV),
            instrument_id=request.instrument_id,
            interval=request.interval,
            adjustment=request.adjustment,
        )

    def get_fundamentals(
        self,
        request: FundamentalRequest,
    ) -> ObservationPage[FundamentalObservation]:
        self._raise_injected(DataOperation.FUNDAMENTALS)
        filtered = tuple(
            observation
            for observation in self._dataset.fundamentals
            if observation.snapshot.symbol in request.symbols
            and observation.snapshot.as_of <= request.as_of
        )
        found_symbols = {observation.snapshot.symbol for observation in filtered}
        missing_symbols = set(request.symbols) - found_symbols
        missing_flags = (QualityFlag.PARTIAL, QualityFlag.MISSING_FIELDS) if missing_symbols else ()
        return self._page(
            filtered,
            request,
            DataOperation.FUNDAMENTALS,
            extra_flags=missing_flags,
        )

    def get_news_events(self, request: NewsEventRequest) -> ObservationPage[NewsEventObservation]:
        self._raise_injected(DataOperation.NEWS_EVENTS)
        event_types = set(request.event_types)
        symbols = set(request.symbols)
        filtered = tuple(
            observation
            for observation in self._dataset.news_events
            if request.start_at <= observation.event.occurred_at < request.end_at
            and (not symbols or not symbols.isdisjoint(observation.symbols))
            and (not event_types or observation.event.event_type in event_types)
        )
        return self._page(filtered, request, DataOperation.NEWS_EVENTS)

    def get_corporate_actions(
        self,
        request: CorporateActionRequest,
    ) -> ObservationPage[CorporateAction]:
        self._raise_injected(DataOperation.CORPORATE_ACTIONS)
        known_by_id = {
            item.instrument.instrument_id: item for item in self._dataset.instrument_metadata
        }
        self._require_all(
            request.instrument_ids,
            known_by_id,
            DataOperation.CORPORATE_ACTIONS,
        )
        requested_ids = set(request.instrument_ids)
        filtered = tuple(
            action
            for action in self._dataset.corporate_actions
            if action.instrument.instrument_id in requested_ids
            and request.effective_from <= action.effective_date < request.effective_to
        )
        return self._page(filtered, request, DataOperation.CORPORATE_ACTIONS)

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
        request: PaginatedFakeRequest,
        operation: DataOperation,
        *,
        extra_flags: tuple[QualityFlag, ...] = (),
    ) -> ObservationPage[ItemT]:
        selected, next_cursor = self._select_page(items, request, operation)
        return self._build(
            ObservationPage[ItemT],
            operation,
            items=selected,
            next_cursor=next_cursor,
            provenance=self._envelope(selected, operation, extra_flags=extra_flags),
        )

    def _select_page(
        self,
        items: tuple[ItemT, ...],
        request: PaginatedFakeRequest,
        operation: DataOperation,
    ) -> tuple[tuple[ItemT, ...], str | None]:
        offset = self._decode_cursor(request.cursor, operation, request)
        selected = items[offset : offset + request.page_size]
        next_offset = offset + len(selected)
        next_cursor = (
            self._encode_cursor(next_offset, operation, request)
            if next_offset < len(items)
            else None
        )
        return selected, next_cursor

    def _envelope(
        self,
        items: Sequence[ItemT],
        operation: DataOperation,
        *,
        extra_flags: tuple[QualityFlag, ...] = (),
    ) -> Provenance:
        provenances: tuple[Provenance, ...] = (
            tuple(item.provenance for item in items) if items else (self._dataset.provenance,)
        )
        flags: tuple[QualityFlag, ...] = tuple(
            sorted(
                {
                    *extra_flags,
                    *(flag for provenance in provenances for flag in provenance.quality_flags),
                },
                key=lambda flag: flag.value,
            )
        )
        delayed = any(provenance.is_delayed for provenance in provenances)
        if delayed and QualityFlag.DELAYED not in flags:
            flags = (*flags, QualityFlag.DELAYED)
        return self._build(
            Provenance,
            operation,
            source=self.source_id,
            observed_at=max(provenance.observed_at for provenance in provenances),
            received_at=max(provenance.received_at for provenance in provenances),
            timezone=provenances[0].timezone,
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

    def _encode_cursor(
        self,
        offset: int,
        operation: DataOperation,
        request: PaginatedFakeRequest,
    ) -> str:
        digest = self._request_digest(request)
        return f"{self._dataset.dataset_id}:{operation.value}:{digest}:{offset}"

    def _decode_cursor(
        self,
        cursor: str | None,
        operation: DataOperation,
        request: PaginatedFakeRequest,
    ) -> int:
        if cursor is None:
            return 0
        prefix = f"{self._dataset.dataset_id}:{operation.value}:{self._request_digest(request)}:"
        if not cursor.startswith(prefix):
            self._invalid_cursor(operation)
        raw_offset = cursor.removeprefix(prefix)
        if not raw_offset.isdigit():
            self._invalid_cursor(operation)
        return int(raw_offset)

    @staticmethod
    def _request_digest(
        request: PaginatedFakeRequest,
    ) -> str:
        filters = request.model_dump(
            mode="json",
            exclude={"cursor", "page_size", "schema_version", "timeout_seconds"},
        )
        canonical = json.dumps(filters, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _build(
        self,
        model: type[ModelT],
        operation: DataOperation,
        **values: Any,
    ) -> ModelT:
        try:
            return model.model_validate(values)
        except ValidationError as exc:
            raise DataSchemaError(
                "fake provider could not normalize its configured dataset",
                operation=operation,
                reason_code="FAKE_NORMALIZATION_FAILED",
                source=self.source_id,
                details={"error_count": str(exc.error_count())},
            ) from exc

    def _invalid_cursor(self, operation: DataOperation) -> NoReturn:
        raise DataInvalidRequestError(
            "cursor does not belong to this dataset and operation",
            operation=operation,
            reason_code="INVALID_PAGE_CURSOR",
            source=self.source_id,
        )


def fixture_dataset() -> FakeDataset:
    """Return the canonical three-instrument P04-T0 fixture dataset."""
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
    sap = _instrument(
        instrument_id="rh_inst_sap_xnys",
        symbol="SAP",
        exchange="XNYS",
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
            _fundamental(aapl, envelope),
            _generic_fundamental(sap, envelope),
        ),
        corporate_actions=(
            _split(aapl, source),
            _dividend(aapl, source),
            _dividend(spy, source, missing_pay_date=True),
        ),
        news_events=(
            _news_event(
                source=source,
                event_id="event_aapl_earnings_2026q3",
                event_type="EARNINGS",
                headline="Synthetic earnings fixture",
                occurred_at="2026-07-23T20:00:00Z",
                observed_at="2026-07-23T20:01:00Z",
                published_at="2026-07-23T19:59:00Z",
                symbols=("AAPL", "MSFT"),
                citations=(
                    _citation(
                        evidence_id="evid_news_aapl_earnings",
                        kind=EvidenceKind.EVENT,
                        locator="event:example.publisher/aapl-earnings-2026q3",
                        source=source,
                    ),
                    _citation(
                        evidence_id="evid_filing_aapl_2026q3",
                        kind=EvidenceKind.FILING,
                        locator="filing:sec.edgar/0000320193-26-000001#10-Q",
                        source=source,
                    ),
                ),
                related_filings=(_filing(source),),
            ),
            _news_event(
                source=source,
                event_id="event_macro_rates_20260724",
                event_type="MACRO_RATE",
                headline="Synthetic macro fixture",
                occurred_at="2026-07-24T14:00:00Z",
                observed_at="2026-07-24T14:01:00Z",
                published_at="2026-07-24T13:55:00Z",
                symbols=(),
                citations=(
                    _citation(
                        evidence_id="evid_macro_rates_20260724",
                        kind=EvidenceKind.EVENT,
                        locator="event:example.publisher/macro-rates-20260724",
                        source=source,
                    ),
                ),
            ),
        ),
        instrument_metadata=(
            _metadata(aapl, source),
            _metadata(spy, source),
            _metadata(sap, source),
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


def _provenance(
    *,
    source: str,
    observed_at: str,
    quality_flags: tuple[QualityFlag, ...] = (),
) -> Provenance:
    return Provenance.model_validate(
        {
            "source": source,
            "observed_at": observed_at,
            "received_at": "2026-07-24T18:30:00Z",
            "timezone": "UTC",
            "is_delayed": False,
            "quality_flags": quality_flags,
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


def _split(instrument: InstrumentIdentity, source: str) -> SplitObservation:
    return SplitObservation.model_validate(
        {
            "action_id": "action_aapl_split_20260615",
            "instrument": instrument,
            "effective_date": "2026-06-15",
            "declared_date": "2026-05-01",
            "split_ratio": "4",
            "provenance": _provenance(
                source=source,
                observed_at="2026-06-15T13:30:00Z",
            ),
        }
    )


def _dividend(
    instrument: InstrumentIdentity,
    source: str,
    *,
    missing_pay_date: bool = False,
) -> DividendObservation:
    flags = (QualityFlag.PARTIAL, QualityFlag.MISSING_FIELDS) if missing_pay_date else ()
    return DividendObservation.model_validate(
        {
            "action_id": f"action_{instrument.symbol.lower()}_dividend_20260710",
            "instrument": instrument,
            "effective_date": "2026-07-10",
            "declared_date": "2026-06-20",
            "cash_amount": "0.25",
            "currency": instrument.currency,
            "pay_date": None if missing_pay_date else "2026-07-17",
            "provenance": _provenance(
                source=source,
                observed_at="2026-07-10T13:30:00Z",
                quality_flags=flags,
            ),
        }
    )


def _filing(source: str) -> FilingReference:
    return FilingReference.model_validate(
        {
            "accession_number": "0000320193-26-000001",
            "form_type": "10-Q",
            "filed_at": "2026-07-24T12:00:00Z",
            "report_period_end": "2026-06-30",
            "primary_document_url": (
                "https://www.sec.gov/Archives/edgar/data/320193/"
                "000032019326000001/aapl-20260630.htm"
            ),
            "provenance": _provenance(
                source=source,
                observed_at="2026-07-24T12:00:00Z",
            ),
        }
    )


def _citation(
    *,
    evidence_id: str,
    kind: EvidenceKind,
    locator: str,
    source: str,
) -> EvidenceCitation:
    return EvidenceCitation.model_validate(
        {
            "evidence_id": evidence_id,
            "kind": kind,
            "summary": "Synthetic provider-contract citation",
            "provenance": _provenance(
                source=source,
                observed_at="2026-07-24T18:00:00Z",
            ),
            "locator": locator,
        }
    )


def _fundamental(
    instrument: InstrumentIdentity,
    provenance: Provenance,
) -> SecFundamentalObservation:
    filing = _filing(provenance.source)
    snapshot = FundamentalSnapshot.model_validate(
        {
            "symbol": instrument.symbol,
            "as_of": "2026-07-24T18:30:00Z",
            "facts": (
                {
                    "key": "market_cap_usd",
                    "kind": FactValueKind.DECIMAL,
                    "decimal_value": "3200000000000",
                    "unit": "USD",
                },
            ),
            "provenance": provenance,
        }
    )
    return SecFundamentalObservation.model_validate(
        {
            "instrument": instrument,
            "snapshot": snapshot,
            "period": ReportingPeriod.model_validate(
                {
                    "start_date": "2026-04-01",
                    "end_date": "2026-06-30",
                    "fiscal_year": 2026,
                    "fiscal_period": "Q3",
                }
            ),
            "reporting_currency": "USD",
            "filing": filing,
            "earnings_at": "2026-07-23T20:00:00Z",
            "earnings_time_certainty": "CONFIRMED",
            "citations": (
                _citation(
                    evidence_id="evid_filing_aapl_fund",
                    kind=EvidenceKind.FILING,
                    locator=f"filing:sec.edgar/{filing.accession_number}#10-Q",
                    source=provenance.source,
                ),
                _citation(
                    evidence_id="evid_fundamental_aapl_mc",
                    kind=EvidenceKind.FUNDAMENTAL,
                    locator="fundamental:ainvest.fake.v1/AAPL#market_cap_usd",
                    source=provenance.source,
                ),
            ),
        }
    )


def _generic_fundamental(
    instrument: InstrumentIdentity,
    provenance: Provenance,
) -> FundamentalObservation:
    snapshot = FundamentalSnapshot.model_validate(
        {
            "symbol": instrument.symbol,
            "as_of": "2026-07-24T18:30:00Z",
            "facts": (
                {
                    "key": "revenue_eur",
                    "kind": FactValueKind.DECIMAL,
                    "decimal_value": "36000000000",
                    "unit": "EUR",
                },
            ),
            "provenance": provenance,
        }
    )
    return FundamentalObservation.model_validate(
        {
            "instrument": instrument,
            "snapshot": snapshot,
            "period": ReportingPeriod.model_validate(
                {
                    "start_date": "2026-01-01",
                    "end_date": "2026-06-30",
                    "fiscal_year": 2026,
                    "fiscal_period": "H1",
                }
            ),
            "reporting_currency": "EUR",
            "earnings_at": None,
            "earnings_time_certainty": "UNKNOWN",
            "citations": (
                _citation(
                    evidence_id="evid_fundamental_sap_revenue",
                    kind=EvidenceKind.FUNDAMENTAL,
                    locator="fundamental:ainvest.fake.v1/SAP#revenue_eur",
                    source=provenance.source,
                ),
            ),
        }
    )


def _news_event(
    *,
    source: str,
    event_id: str,
    event_type: str,
    headline: str,
    occurred_at: str,
    observed_at: str,
    published_at: str,
    symbols: tuple[str, ...],
    citations: tuple[EvidenceCitation, ...],
    related_filings: tuple[FilingReference, ...] = (),
) -> NewsEventObservation:
    event = MarketEvent.model_validate(
        {
            "event_id": event_id,
            "symbol": symbols[0] if symbols else None,
            "event_type": event_type,
            "headline": headline,
            "occurred_at": occurred_at,
            "provenance": _provenance(source=source, observed_at=observed_at),
        }
    )
    return NewsEventObservation.model_validate(
        {
            "event": event,
            "symbols": symbols,
            "url": f"https://example.com/events/{event_id}",
            "publisher": "Synthetic Fixture Publisher",
            "published_at": published_at,
            "license_name": "synthetic-test-only",
            "event_time_certainty": "CONFIRMED",
            "citations": citations,
            "related_filings": related_filings,
        }
    )


__all__ = [
    "DeterministicFakeDataProvider",
    "fixture_dataset",
]
