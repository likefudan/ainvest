"""Development-only Yahoo Finance market-data adapter.

This module is deliberately outside the live quote boundary.  It implements
only the ordinary research/offline ports and rejects Live construction before
loading yfinance or touching a transport.  The provider is delayed,
unofficial, and unsuitable for risk or execution decisions.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import partial
from time import monotonic
from types import ModuleType
from typing import Any, Final, Literal, NoReturn, Protocol, TypeVar, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ValidationError

from ainvest.config import TradingMode
from ainvest.data.models import (
    CorporateAction,
    CorporateActionRequest,
    DividendObservation,
    ObservationBatch,
    ObservationPage,
    OhlcvPage,
    OhlcvRequest,
    PaginatedDataRequest,
    PriceAdjustment,
    QuoteRequest,
    SplitObservation,
)
from ainvest.data.ports import (
    DataConflictError,
    DataIncompleteError,
    DataInvalidRequestError,
    DataNotFoundError,
    DataOperation,
    DataProviderError,
    DataRateLimitError,
    DataSchemaError,
    DataTimeoutError,
    DataUnsupportedError,
    DataUpstreamError,
)
from ainvest.schemas.common import (
    InstrumentIdentity,
    Provenance,
    QualityFlag,
    SourceId,
    format_canonical_decimal,
)
from ainvest.schemas.market import MarketQuote, OhlcvBar

YAHOO_DEVELOPMENT_SOURCE: Final[SourceId] = "yahoo.development_only.v1"
_MAX_PROVIDER_ROWS: Final = 10_000
_MAX_ACTION_WINDOW_DAYS: Final = 3_660
_ALLOWED_INTERVAL_DAYS: Final[Mapping[str, int]] = {
    "1m": 7,
    "2m": 60,
    "5m": 60,
    "15m": 60,
    "30m": 60,
    "60m": 60,
    "90m": 60,
    "1h": 60,
    "1d": 3_660,
    "5d": 3_660,
    "1w": 7_320,
}
_YFINANCE_INTERVAL: Final[Mapping[str, str]] = {"1w": "1wk"}


@dataclass(frozen=True, slots=True)
class YahooInstrumentConfig:
    """Development-only binding from canonical identity to exchange timezone."""

    instrument: InstrumentIdentity
    exchange_timezone: str

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.exchange_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("exchange_timezone must be a valid IANA timezone") from exc


@dataclass(frozen=True, slots=True)
class _YahooQuote:
    observed_at: datetime
    last_price: object


@dataclass(frozen=True, slots=True)
class _YahooBar:
    timestamp: datetime
    open: object
    high: object
    low: object
    close: object
    volume: object


@dataclass(frozen=True, slots=True)
class _YahooAction:
    timestamp: datetime
    dividend: object
    split: object


class _YahooBoundary(Protocol):
    """Narrow injectable seam around yfinance; never exposed above this module."""

    def quote(self, symbol: str, *, timeout_seconds: float) -> _YahooQuote | None: ...

    def history(
        self,
        symbol: str,
        *,
        start_at: datetime,
        end_at: datetime,
        interval: str,
        auto_adjust: bool,
        timeout_seconds: float,
    ) -> tuple[_YahooBar, ...]: ...

    def actions(
        self,
        symbol: str,
        *,
        effective_from: date,
        effective_to: date,
        timeout_seconds: float,
    ) -> tuple[_YahooAction, ...]: ...


class _YahooRateLimited(Exception):
    """Private transport signal stripped of provider exception details."""


class _YahooTransportFailed(Exception):
    """Private transport signal stripped of provider exception details."""


class _YahooMalformedResponse(Exception):
    """Private signal for a response that cannot satisfy the pinned shape."""


TickerFactory = Callable[[str], Any]
ResultT = TypeVar("ResultT")
ModelT = TypeVar("ModelT", bound=BaseModel)


class _YFinanceBoundary:
    """Thin lazy wrapper around the optional yfinance Ticker API."""

    def __init__(self, ticker_factory: TickerFactory | None = None) -> None:
        self._ticker_factory = ticker_factory

    def quote(self, symbol: str, *, timeout_seconds: float) -> _YahooQuote | None:
        rows = self._history_rows(
            symbol,
            period="1d",
            interval="1m",
            auto_adjust=False,
            actions=False,
            timeout=timeout_seconds,
        )
        if not rows:
            return None
        timestamp, row = rows[-1]
        return _YahooQuote(observed_at=timestamp, last_price=_row_value(row, "Close"))

    def history(
        self,
        symbol: str,
        *,
        start_at: datetime,
        end_at: datetime,
        interval: str,
        auto_adjust: bool,
        timeout_seconds: float,
    ) -> tuple[_YahooBar, ...]:
        rows = self._history_rows(
            symbol,
            start=start_at,
            end=end_at,
            interval=interval,
            auto_adjust=auto_adjust,
            actions=False,
            timeout=timeout_seconds,
        )
        return tuple(
            _YahooBar(
                timestamp=timestamp,
                open=_row_value(row, "Open"),
                high=_row_value(row, "High"),
                low=_row_value(row, "Low"),
                close=_row_value(row, "Close"),
                volume=_row_value(row, "Volume"),
            )
            for timestamp, row in rows
        )

    def actions(
        self,
        symbol: str,
        *,
        effective_from: date,
        effective_to: date,
        timeout_seconds: float,
    ) -> tuple[_YahooAction, ...]:
        rows = self._history_rows(
            symbol,
            start=effective_from,
            end=effective_to,
            interval="1d",
            auto_adjust=False,
            actions=True,
            timeout=timeout_seconds,
        )
        return tuple(
            _YahooAction(
                timestamp=timestamp,
                dividend=_row_value(row, "Dividends"),
                split=_row_value(row, "Stock Splits"),
            )
            for timestamp, row in rows
        )

    def _history_rows(self, symbol: str, **kwargs: object) -> tuple[tuple[datetime, Any], ...]:
        ticker = self._ticker(symbol)
        try:
            frame = ticker.history(raise_errors=True, **kwargs)
            if bool(frame.empty):
                return ()
            return tuple((_as_datetime(index), row) for index, row in frame.iterrows())
        except _YahooMalformedResponse:
            raise
        except (KeyError, TypeError, ValueError):
            raise _YahooMalformedResponse from None
        except TimeoutError:
            raise
        except Exception as exc:
            if type(exc).__name__ in {"YFRateLimitError", "RateLimitError"}:
                raise _YahooRateLimited from None
            if type(exc).__name__ in {"Timeout", "ConnectTimeout", "ReadTimeout"}:
                raise TimeoutError from None
            raise _YahooTransportFailed from None

    def _ticker(self, symbol: str) -> Any:
        if self._ticker_factory is None:
            try:
                module: ModuleType = importlib.import_module("yfinance")
            except ModuleNotFoundError:
                raise DataUnsupportedError(
                    "Yahoo development adapter requires the offline-data extra",
                    operation=DataOperation.DATASET,
                    reason_code="YAHOO_OPTIONAL_DEPENDENCY_MISSING",
                    source=YAHOO_DEVELOPMENT_SOURCE,
                ) from None
            self._ticker_factory = cast(TickerFactory, module.Ticker)
        return self._ticker_factory(symbol)


class YahooDevelopmentAdapter:
    """Delayed yfinance adapter for development, replay, and offline research only.

    It intentionally does not implement ``LiveQuotePort`` and cannot be
    constructed for ``TradingMode.LIVE``.  Calls are never a fallback from a
    Robinhood capability.
    """

    development_only: Literal[True] = True

    def __init__(
        self,
        *,
        mode: TradingMode,
        instruments: tuple[YahooInstrumentConfig, ...],
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        boundary: _YahooBoundary | None = None,
    ) -> None:
        if mode is TradingMode.LIVE:
            raise DataUnsupportedError(
                "Yahoo development adapter is forbidden in Live mode",
                operation=DataOperation.DATASET,
                reason_code="YAHOO_LIVE_FORBIDDEN",
                source=YAHOO_DEVELOPMENT_SOURCE,
            )
        by_id = {item.instrument.instrument_id: item for item in instruments}
        if not instruments or len(by_id) != len(instruments):
            raise DataInvalidRequestError(
                "Yahoo instrument bindings must be non-empty and unique",
                operation=DataOperation.DATASET,
                reason_code="YAHOO_INSTRUMENT_BINDINGS_INVALID",
                source=YAHOO_DEVELOPMENT_SOURCE,
            )
        symbols = [item.instrument.symbol for item in instruments]
        if len(symbols) != len(set(symbols)):
            raise DataInvalidRequestError(
                "Yahoo symbol bindings must be unique",
                operation=DataOperation.DATASET,
                reason_code="YAHOO_SYMBOL_BINDINGS_INVALID",
                source=YAHOO_DEVELOPMENT_SOURCE,
            )
        self._mode_value = mode.value
        self._instruments = by_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic_clock or monotonic
        self._boundary = boundary or _YFinanceBoundary()

    @property
    def source_id(self) -> SourceId:
        """Stable source identifier declaring the development-only restriction."""
        return YAHOO_DEVELOPMENT_SOURCE

    def get_quotes(self, request: QuoteRequest) -> ObservationBatch[MarketQuote]:
        self._require_non_live(DataOperation.QUOTES)
        deadline = self._deadline(request.timeout_seconds)
        configured = self._resolve_instruments(request.instrument_ids, DataOperation.QUOTES)
        timezone = self._single_timezone(configured, DataOperation.QUOTES)
        raw_items: list[tuple[YahooInstrumentConfig, _YahooQuote]] = []
        for item in configured:
            symbol = item.instrument.symbol
            remaining = self._remaining(deadline, DataOperation.QUOTES)
            raw = self._call(
                DataOperation.QUOTES,
                partial(
                    self._boundary.quote,
                    symbol,
                    timeout_seconds=remaining,
                ),
            )
            self._remaining(deadline, DataOperation.QUOTES)
            if raw is None:
                raise DataIncompleteError(
                    "Yahoo returned no quote for a configured instrument",
                    operation=DataOperation.QUOTES,
                    reason_code="YAHOO_EMPTY_QUOTE",
                    source=self.source_id,
                )
            raw_items.append((item, raw))
        received_at = self._now(DataOperation.QUOTES)
        quotes = tuple(self._quote(item, raw, received_at=received_at) for item, raw in raw_items)
        return self._build_batch(quotes, received_at=received_at, timezone=timezone)

    def get_ohlcv(self, request: OhlcvRequest) -> OhlcvPage:
        self._require_non_live(DataOperation.OHLCV)
        deadline = self._deadline(request.timeout_seconds)
        item = self._resolve_instruments((request.instrument_id,), DataOperation.OHLCV)[0]
        provider_interval = self._validate_ohlcv_request(request)
        auto_adjust = request.adjustment is PriceAdjustment.SPLIT_AND_DIVIDEND
        raw_bars = self._call(
            DataOperation.OHLCV,
            lambda: self._boundary.history(
                item.instrument.symbol,
                start_at=request.start_at,
                end_at=request.end_at,
                interval=provider_interval,
                auto_adjust=auto_adjust,
                timeout_seconds=self._remaining(deadline, DataOperation.OHLCV),
            ),
        )
        self._remaining(deadline, DataOperation.OHLCV)
        self._validate_ordered_timestamps(
            tuple(bar.timestamp for bar in raw_bars), DataOperation.OHLCV
        )
        if len(raw_bars) > _MAX_PROVIDER_ROWS:
            self._schema_error(DataOperation.OHLCV, "YAHOO_RESULT_TOO_LARGE")
        received_at = self._now(DataOperation.OHLCV)
        bars = tuple(self._bar(item, raw, request, received_at=received_at) for raw in raw_bars)
        offset = self._decode_cursor(request, DataOperation.OHLCV)
        selected = bars[offset : offset + request.page_size]
        next_cursor = self._next_cursor(offset, len(selected), len(bars), request)
        provenance = self._provenance(
            observed_at=received_at,
            received_at=received_at,
            timezone=item.exchange_timezone,
        )
        return self._build(
            OhlcvPage,
            DataOperation.OHLCV,
            items=selected,
            next_cursor=next_cursor,
            provenance=provenance,
            instrument_id=request.instrument_id,
            interval=request.interval,
            adjustment=request.adjustment,
        )

    def get_corporate_actions(
        self, request: CorporateActionRequest
    ) -> ObservationPage[CorporateAction]:
        self._require_non_live(DataOperation.CORPORATE_ACTIONS)
        deadline = self._deadline(request.timeout_seconds)
        self._validate_corporate_action_request(request)
        configured = self._resolve_instruments(
            request.instrument_ids, DataOperation.CORPORATE_ACTIONS
        )
        timezone = self._single_timezone(configured, DataOperation.CORPORATE_ACTIONS)
        raw_by_instrument: list[tuple[YahooInstrumentConfig, _YahooAction]] = []
        for item in configured:
            symbol = item.instrument.symbol
            remaining = self._remaining(deadline, DataOperation.CORPORATE_ACTIONS)
            raw_actions = self._call(
                DataOperation.CORPORATE_ACTIONS,
                partial(
                    self._boundary.actions,
                    symbol,
                    effective_from=request.effective_from,
                    effective_to=request.effective_to,
                    timeout_seconds=remaining,
                ),
            )
            self._remaining(deadline, DataOperation.CORPORATE_ACTIONS)
            self._validate_ordered_timestamps(
                tuple(action.timestamp for action in raw_actions),
                DataOperation.CORPORATE_ACTIONS,
            )
            if len(raw_actions) > _MAX_PROVIDER_ROWS - len(raw_by_instrument):
                self._schema_error(DataOperation.CORPORATE_ACTIONS, "YAHOO_RESULT_TOO_LARGE")
            raw_by_instrument.extend((item, action) for action in raw_actions)
        received_at = self._now(DataOperation.CORPORATE_ACTIONS)
        normalized_actions: list[CorporateAction] = []
        for item, raw in raw_by_instrument:
            for action in self._actions(item, raw, received_at=received_at):
                if not request.effective_from <= action.effective_date < request.effective_to:
                    continue
                if len(normalized_actions) >= _MAX_PROVIDER_ROWS:
                    self._schema_error(
                        DataOperation.CORPORATE_ACTIONS,
                        "YAHOO_RESULT_TOO_LARGE",
                    )
                normalized_actions.append(action)
        actions = tuple(normalized_actions)
        ordered = tuple(
            sorted(actions, key=lambda action: (action.effective_date, action.action_id))
        )
        offset = self._decode_cursor(request, DataOperation.CORPORATE_ACTIONS)
        selected = ordered[offset : offset + request.page_size]
        next_cursor = self._next_cursor(offset, len(selected), len(ordered), request)
        provenance = self._provenance(
            observed_at=received_at,
            received_at=received_at,
            timezone=timezone,
            extra_flags=(QualityFlag.MISSING_FIELDS, QualityFlag.PARTIAL) if selected else (),
        )
        return self._build(
            ObservationPage[CorporateAction],
            DataOperation.CORPORATE_ACTIONS,
            items=selected,
            next_cursor=next_cursor,
            provenance=provenance,
        )

    def _quote(
        self,
        item: YahooInstrumentConfig,
        raw: _YahooQuote,
        *,
        received_at: datetime,
    ) -> MarketQuote:
        observed_at = self._aware_utc(raw.observed_at, DataOperation.QUOTES)
        if observed_at > received_at:
            self._schema_error(DataOperation.QUOTES, "YAHOO_QUOTE_FROM_FUTURE")
        provenance = self._provenance(
            observed_at=observed_at,
            received_at=received_at,
            timezone=item.exchange_timezone,
            extra_flags=(QualityFlag.MISSING_FIELDS,),
        )
        return self._build(
            MarketQuote,
            DataOperation.QUOTES,
            instrument=item.instrument,
            last_price=self._decimal(raw.last_price, DataOperation.QUOTES, positive=True),
            currency=item.instrument.currency,
            provenance=provenance,
        )

    def _bar(
        self,
        item: YahooInstrumentConfig,
        raw: _YahooBar,
        request: OhlcvRequest,
        *,
        received_at: datetime,
    ) -> OhlcvBar:
        bar_start = self._aware_utc(raw.timestamp, DataOperation.OHLCV)
        if not request.start_at <= bar_start < request.end_at:
            self._schema_error(DataOperation.OHLCV, "YAHOO_BAR_OUTSIDE_REQUEST")
        provenance = self._provenance(
            observed_at=received_at,
            received_at=received_at,
            timezone=item.exchange_timezone,
        )
        return self._build(
            OhlcvBar,
            DataOperation.OHLCV,
            instrument=item.instrument,
            interval=request.interval,
            bar_start=bar_start,
            open=self._decimal(raw.open, DataOperation.OHLCV, positive=True),
            high=self._decimal(raw.high, DataOperation.OHLCV, positive=True),
            low=self._decimal(raw.low, DataOperation.OHLCV, positive=True),
            close=self._decimal(raw.close, DataOperation.OHLCV, positive=True),
            volume=self._decimal(raw.volume, DataOperation.OHLCV, positive=False),
            provenance=provenance,
        )

    def _actions(
        self,
        item: YahooInstrumentConfig,
        raw: _YahooAction,
        *,
        received_at: datetime,
    ) -> Iterator[CorporateAction]:
        timestamp = self._aware_utc(raw.timestamp, DataOperation.CORPORATE_ACTIONS)
        effective_date = timestamp.astimezone(ZoneInfo(item.exchange_timezone)).date()
        dividend = self._decimal(raw.dividend, DataOperation.CORPORATE_ACTIONS, positive=False)
        split = self._decimal(raw.split, DataOperation.CORPORATE_ACTIONS, positive=False)
        if dividend < 0 or split < 0:
            self._schema_error(DataOperation.CORPORATE_ACTIONS, "YAHOO_ACTION_AMOUNT_INVALID")
        provenance = self._provenance(
            observed_at=received_at,
            received_at=received_at,
            timezone=item.exchange_timezone,
            extra_flags=(QualityFlag.MISSING_FIELDS, QualityFlag.PARTIAL),
        )
        if split > 0:
            yield self._build(
                SplitObservation,
                DataOperation.CORPORATE_ACTIONS,
                action_id=self._action_id(item.instrument.instrument_id, effective_date, "split"),
                instrument=item.instrument,
                effective_date=effective_date,
                split_ratio=format_canonical_decimal(split),
                provenance=provenance,
            )
        if dividend > 0:
            yield self._build(
                DividendObservation,
                DataOperation.CORPORATE_ACTIONS,
                action_id=self._action_id(
                    item.instrument.instrument_id, effective_date, "dividend"
                ),
                instrument=item.instrument,
                effective_date=effective_date,
                cash_amount=format_canonical_decimal(dividend),
                currency=item.instrument.currency,
                provenance=provenance,
            )

    def _validate_ohlcv_request(self, request: OhlcvRequest) -> str:
        if request.adjustment is PriceAdjustment.SPLIT:
            raise DataUnsupportedError(
                "Yahoo cannot provide split-only prices without dividend adjustment ambiguity",
                operation=DataOperation.OHLCV,
                reason_code="YAHOO_SPLIT_ONLY_UNSUPPORTED",
                source=self.source_id,
            )
        max_days = _ALLOWED_INTERVAL_DAYS.get(request.interval)
        if max_days is None or request.end_at - request.start_at > timedelta(days=max_days):
            raise DataInvalidRequestError(
                "Yahoo interval or date window is outside development adapter bounds",
                operation=DataOperation.OHLCV,
                reason_code="YAHOO_OHLCV_WINDOW_INVALID",
                source=self.source_id,
            )
        return _YFINANCE_INTERVAL.get(request.interval, request.interval)

    def _validate_corporate_action_request(self, request: CorporateActionRequest) -> None:
        if (request.effective_to - request.effective_from).days > _MAX_ACTION_WINDOW_DAYS:
            raise DataInvalidRequestError(
                "Yahoo corporate-action window exceeds the development adapter bound",
                operation=DataOperation.CORPORATE_ACTIONS,
                reason_code="YAHOO_ACTION_WINDOW_INVALID",
                source=self.source_id,
            )
        today = self._now(DataOperation.CORPORATE_ACTIONS).date()
        if request.effective_from > today or request.effective_to > today + timedelta(days=1):
            raise DataInvalidRequestError(
                "Yahoo corporate-action request cannot include future effective dates",
                operation=DataOperation.CORPORATE_ACTIONS,
                reason_code="YAHOO_ACTION_FUTURE_WINDOW",
                source=self.source_id,
            )

    def _resolve_instruments(
        self, instrument_ids: tuple[str, ...], operation: DataOperation
    ) -> tuple[YahooInstrumentConfig, ...]:
        try:
            return tuple(self._instruments[instrument_id] for instrument_id in instrument_ids)
        except KeyError:
            raise DataNotFoundError(
                "Yahoo instrument is not present in the explicit development mapping",
                operation=operation,
                reason_code="YAHOO_INSTRUMENT_NOT_FOUND",
                source=self.source_id,
            ) from None

    def _single_timezone(
        self, items: tuple[YahooInstrumentConfig, ...], operation: DataOperation
    ) -> str:
        timezones = {item.exchange_timezone for item in items}
        if len(timezones) != 1:
            raise DataInvalidRequestError(
                "one Yahoo response cannot mix exchange timezones",
                operation=operation,
                reason_code="YAHOO_MIXED_TIMEZONES",
                source=self.source_id,
            )
        return next(iter(timezones))

    def _require_non_live(self, operation: DataOperation) -> None:
        if self._mode_value == TradingMode.LIVE.value:
            raise DataUnsupportedError(
                "Yahoo development adapter is forbidden in Live mode",
                operation=operation,
                reason_code="YAHOO_LIVE_FORBIDDEN",
                source=self.source_id,
            )

    def _call(self, operation: DataOperation, call: Callable[[], ResultT]) -> ResultT:
        try:
            return call()
        except DataProviderError:
            raise
        except TimeoutError:
            raise DataTimeoutError(
                "Yahoo request exceeded its deadline",
                operation=operation,
                reason_code="YAHOO_TIMEOUT",
                source=self.source_id,
            ) from None
        except _YahooRateLimited:
            raise DataRateLimitError(
                "Yahoo rate limited the development adapter",
                operation=operation,
                reason_code="YAHOO_RATE_LIMIT",
                source=self.source_id,
            ) from None
        except _YahooTransportFailed:
            raise DataUpstreamError(
                "Yahoo development data request failed",
                operation=operation,
                reason_code="YAHOO_UPSTREAM_FAILURE",
                source=self.source_id,
            ) from None
        except _YahooMalformedResponse:
            raise DataSchemaError(
                "Yahoo response is incompatible with the development adapter contract",
                operation=operation,
                reason_code="YAHOO_RESPONSE_MALFORMED",
                source=self.source_id,
            ) from None
        except Exception:
            raise DataUpstreamError(
                "Yahoo development data request failed",
                operation=operation,
                reason_code="YAHOO_UPSTREAM_FAILURE",
                source=self.source_id,
            ) from None

    def _deadline(self, timeout_seconds: int) -> float:
        return self._monotonic() + timeout_seconds

    def _remaining(self, deadline: float, operation: DataOperation) -> float:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise DataTimeoutError(
                "Yahoo request exhausted its shared deadline",
                operation=operation,
                reason_code="YAHOO_DEADLINE_EXHAUSTED",
                source=self.source_id,
            )
        return remaining

    def _now(self, operation: DataOperation) -> datetime:
        return self._aware_utc(self._clock(), operation)

    def _aware_utc(self, value: datetime, operation: DataOperation) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            self._schema_error(operation, "YAHOO_TIMEZONE_MISSING")
        return value.astimezone(UTC)

    def _validate_ordered_timestamps(
        self, timestamps: tuple[datetime, ...], operation: DataOperation
    ) -> None:
        normalized = tuple(self._aware_utc(value, operation) for value in timestamps)
        if len(normalized) != len(set(normalized)):
            raise DataConflictError(
                "Yahoo response contains duplicate timestamps",
                operation=operation,
                reason_code="YAHOO_DUPLICATE_INDEX",
                source=self.source_id,
            )
        if normalized != tuple(sorted(normalized)):
            raise DataConflictError(
                "Yahoo response index is out of order",
                operation=operation,
                reason_code="YAHOO_INDEX_OUT_OF_ORDER",
                source=self.source_id,
            )

    def _decimal(self, value: object, operation: DataOperation, *, positive: bool) -> Decimal:
        if value is None or isinstance(value, bool):
            self._incomplete(operation, "YAHOO_REQUIRED_VALUE_MISSING")
        try:
            parsed = Decimal(str(value))
            canonical = Decimal(format_canonical_decimal(parsed))
        except (InvalidOperation, ValueError):
            self._incomplete(operation, "YAHOO_REQUIRED_VALUE_MALFORMED")
        if not canonical.is_finite() or (positive and canonical <= 0) or canonical < 0:
            self._incomplete(operation, "YAHOO_REQUIRED_VALUE_MALFORMED")
        return canonical

    def _provenance(
        self,
        *,
        observed_at: datetime,
        received_at: datetime,
        timezone: str,
        extra_flags: tuple[QualityFlag, ...] = (),
    ) -> Provenance:
        flags = tuple(
            sorted(
                {QualityFlag.DELAYED, QualityFlag.UNVERIFIED, *extra_flags},
                key=lambda flag: flag.value,
            )
        )
        return self._build(
            Provenance,
            DataOperation.DATASET,
            source=self.source_id,
            observed_at=observed_at,
            received_at=received_at,
            timezone=timezone,
            is_delayed=True,
            quality_flags=flags,
        )

    def _build_batch(
        self,
        items: tuple[MarketQuote, ...],
        *,
        received_at: datetime,
        timezone: str,
    ) -> ObservationBatch[MarketQuote]:
        provenance = self._provenance(
            observed_at=max(item.provenance.observed_at for item in items),
            received_at=received_at,
            timezone=timezone,
            extra_flags=(QualityFlag.MISSING_FIELDS,),
        )
        return self._build(
            ObservationBatch[MarketQuote],
            DataOperation.QUOTES,
            items=items,
            provenance=provenance,
        )

    def _decode_cursor(self, request: PaginatedDataRequest, operation: DataOperation) -> int:
        if request.cursor is None:
            return 0
        prefix = f"{self.source_id}:{operation.value}:{self._request_digest(request)}:"
        if not request.cursor.startswith(prefix):
            self._invalid_cursor(operation)
        raw_offset = request.cursor.removeprefix(prefix)
        if not raw_offset.isdigit():
            self._invalid_cursor(operation)
        offset = int(raw_offset)
        if offset > _MAX_PROVIDER_ROWS:
            self._invalid_cursor(operation)
        return offset

    def _next_cursor(
        self,
        offset: int,
        selected_count: int,
        total_count: int,
        request: PaginatedDataRequest,
    ) -> str | None:
        next_offset = offset + selected_count
        if next_offset >= total_count:
            return None
        operation = (
            DataOperation.OHLCV
            if isinstance(request, OhlcvRequest)
            else DataOperation.CORPORATE_ACTIONS
        )
        digest = self._request_digest(request)
        return f"{self.source_id}:{operation.value}:{digest}:{next_offset}"

    @staticmethod
    def _request_digest(request: PaginatedDataRequest) -> str:
        filters = request.model_dump(
            mode="json",
            exclude={"cursor", "page_size", "schema_version", "timeout_seconds"},
        )
        encoded = json.dumps(filters, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _invalid_cursor(self, operation: DataOperation) -> NoReturn:
        raise DataInvalidRequestError(
            "cursor does not belong to this Yahoo query",
            operation=operation,
            reason_code="INVALID_PAGE_CURSOR",
            source=self.source_id,
        )

    def _incomplete(self, operation: DataOperation, reason_code: str) -> NoReturn:
        raise DataIncompleteError(
            "Yahoo response is missing a required normalized value",
            operation=operation,
            reason_code=reason_code,
            source=self.source_id,
        )

    def _schema_error(self, operation: DataOperation, reason_code: str) -> NoReturn:
        raise DataSchemaError(
            "Yahoo response is incompatible with the development adapter contract",
            operation=operation,
            reason_code=reason_code,
            source=self.source_id,
        )

    def _build(self, model: type[ModelT], operation: DataOperation, **values: object) -> ModelT:
        try:
            return model.model_validate(values)
        except ValidationError as exc:
            raise DataSchemaError(
                "Yahoo response could not be normalized",
                operation=operation,
                reason_code="YAHOO_NORMALIZATION_FAILED",
                source=self.source_id,
                details={"error_count": str(exc.error_count())},
            ) from None

    @staticmethod
    def _action_id(instrument_id: str, effective_date: date, kind: str) -> str:
        payload = f"{instrument_id}:{effective_date.isoformat()}:{kind}".encode()
        return f"yahoo_action_{hashlib.sha256(payload).hexdigest()[:24]}"


def _row_value(row: Any, name: str) -> object:
    try:
        return row[name]
    except (KeyError, TypeError):
        raise _YahooMalformedResponse from None


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    converter = getattr(value, "to_pydatetime", None)
    if callable(converter):
        converted = converter()
        if isinstance(converted, datetime):
            return converted
    raise _YahooMalformedResponse


__all__ = [
    "YAHOO_DEVELOPMENT_SOURCE",
    "YahooDevelopmentAdapter",
    "YahooInstrumentConfig",
]
