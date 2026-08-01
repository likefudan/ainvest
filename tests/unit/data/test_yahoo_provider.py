"""P04-T1 Yahoo development-adapter tests; no test performs public I/O."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import ainvest.data.providers.yahoo as yahoo_module
from ainvest.config import TradingMode
from ainvest.data import (
    CorporateActionPort,
    CorporateActionRequest,
    DataConflictError,
    DataIncompleteError,
    DataInvalidRequestError,
    DataNotFoundError,
    DataProviderError,
    DataRateLimitError,
    DataSchemaError,
    DataTimeoutError,
    DataUnsupportedError,
    DataUpstreamError,
    DividendObservation,
    LiveQuotePort,
    OhlcvPort,
    OhlcvRequest,
    PriceAdjustment,
    QuotePort,
    QuoteRequest,
    SplitObservation,
)
from ainvest.data.providers.yahoo import (
    YAHOO_DEVELOPMENT_SOURCE,
    YahooDevelopmentAdapter,
    YahooInstrumentConfig,
)
from ainvest.schemas.common import InstrumentIdentity, QualityFlag

NOW = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "yahoo_recording.json").read_text(encoding="utf-8")
)


class RecordingBoundary:
    """Deterministic provider recording with explicit failure injection."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failure: Exception | None = None
        self.quote_value: yahoo_module._YahooQuote | None = yahoo_module._YahooQuote(
            observed_at=datetime.fromisoformat(FIXTURE["quote"]["observed_at"]),
            last_price=FIXTURE["quote"]["last_price"],
        )
        self.bars = tuple(
            yahoo_module._YahooBar(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
            for row in FIXTURE["bars"]
        )
        self.action_rows = tuple(
            yahoo_module._YahooAction(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                dividend=row["dividend"],
                split=row["split"],
            )
            for row in FIXTURE["actions"]
        )

    def quote(self, symbol: str, *, timeout_seconds: int) -> yahoo_module._YahooQuote | None:
        self.calls.append(f"quote:{symbol}:{timeout_seconds}")
        self._raise_failure()
        return self.quote_value

    def history(
        self,
        symbol: str,
        *,
        start_at: datetime,
        end_at: datetime,
        interval: str,
        auto_adjust: bool,
        timeout_seconds: int,
    ) -> tuple[yahoo_module._YahooBar, ...]:
        del start_at, end_at
        self.calls.append(f"history:{symbol}:{interval}:{auto_adjust}:{timeout_seconds}")
        self._raise_failure()
        return self.bars

    def actions(
        self,
        symbol: str,
        *,
        effective_from: date,
        effective_to: date,
        timeout_seconds: int,
    ) -> tuple[yahoo_module._YahooAction, ...]:
        del effective_from, effective_to
        self.calls.append(f"actions:{symbol}:{timeout_seconds}")
        self._raise_failure()
        return self.action_rows

    def _raise_failure(self) -> None:
        if self.failure is not None:
            raise self.failure


class _Frame:
    def __init__(self, rows: tuple[tuple[datetime, dict[str, object]], ...]) -> None:
        self._rows = rows
        self.empty = not rows

    def iterrows(self) -> Any:
        return iter(self._rows)


class _Ticker:
    def __init__(self, frames: list[_Frame]) -> None:
        self.frames = frames
        self.calls: list[dict[str, object]] = []

    def history(self, **kwargs: object) -> _Frame:
        self.calls.append(kwargs)
        return self.frames.pop(0)


def _instrument(
    instrument_id: str = "rh_inst_aapl_xnas", symbol: str = "AAPL"
) -> YahooInstrumentConfig:
    identity = InstrumentIdentity.model_validate(
        {
            "instrument_id": instrument_id,
            "symbol": symbol,
            "exchange": "XNAS",
            "currency": "USD",
            "asset_type": "EQUITY",
            "identity_as_of": "2026-07-24T18:30:00Z",
            "provider": "robinhood.mcp",
        }
    )
    return YahooInstrumentConfig(instrument=identity, exchange_timezone="America/New_York")


def _adapter(boundary: RecordingBoundary | None = None) -> YahooDevelopmentAdapter:
    return YahooDevelopmentAdapter(
        mode=TradingMode.RESEARCH,
        instruments=(_instrument(),),
        clock=lambda: NOW,
        boundary=boundary or RecordingBoundary(),
    )


def _ohlcv_request(**updates: object) -> OhlcvRequest:
    values: dict[str, object] = {
        "instrument_id": "rh_inst_aapl_xnas",
        "interval": "1d",
        "start_at": "2026-07-20T00:00:00Z",
        "end_at": "2026-07-25T00:00:00Z",
        "page_size": 1,
    }
    values.update(updates)
    return OhlcvRequest.model_validate(values)


def _actions_request(**updates: object) -> CorporateActionRequest:
    values: dict[str, object] = {
        "instrument_ids": ("rh_inst_aapl_xnas",),
        "effective_from": "2026-06-01",
        "effective_to": "2026-08-01",
        "page_size": 10,
    }
    values.update(updates)
    return CorporateActionRequest.model_validate(values)


@pytest.mark.unit
@pytest.mark.live_safety
def test_live_construction_rejects_before_boundary_or_optional_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def forbidden_import(name: str, package: str | None = None) -> Any:
        nonlocal calls
        del name, package
        calls += 1
        raise AssertionError("transport dependency must not load")

    monkeypatch.setattr(importlib, "import_module", forbidden_import)
    with pytest.raises(DataUnsupportedError) as caught:
        YahooDevelopmentAdapter(mode=TradingMode.LIVE, instruments=(_instrument(),))

    assert caught.value.reason_code == "YAHOO_LIVE_FORBIDDEN"
    assert calls == 0


@pytest.mark.unit
def test_quote_is_delayed_development_only_and_retains_source_observed_time() -> None:
    adapter = _adapter()
    result = adapter.get_quotes(QuoteRequest(instrument_ids=("rh_inst_aapl_xnas",)))

    assert adapter.development_only is True
    assert adapter.source_id == YAHOO_DEVELOPMENT_SOURCE
    assert result.items[0].last_price == Decimal("215.42")
    assert result.items[0].currency == "USD"
    assert result.items[0].provenance.observed_at == datetime(2026, 7, 24, 19, 59, tzinfo=UTC)
    assert result.items[0].provenance.received_at == NOW
    assert result.provenance.timezone == "America/New_York"
    assert result.provenance.is_delayed is True
    assert set(result.provenance.quality_flags) >= {
        QualityFlag.DELAYED,
        QualityFlag.UNVERIFIED,
        QualityFlag.MISSING_FIELDS,
    }
    assert isinstance(adapter, QuotePort)
    assert isinstance(adapter, OhlcvPort)
    assert isinstance(adapter, CorporateActionPort)
    assert not isinstance(adapter, LiveQuotePort)


@pytest.mark.unit
def test_ohlcv_preserves_adjustment_timezone_and_query_bound_pagination() -> None:
    boundary = RecordingBoundary()
    adapter = _adapter(boundary)
    first = adapter.get_ohlcv(_ohlcv_request(adjustment=PriceAdjustment.SPLIT_AND_DIVIDEND))
    assert len(first.items) == 1
    assert first.items[0].bar_start == datetime(2026, 7, 22, 13, 30, tzinfo=UTC)
    assert first.adjustment is PriceAdjustment.SPLIT_AND_DIVIDEND
    assert first.provenance.timezone == "America/New_York"
    assert "history:AAPL:1d:True:30" in boundary.calls
    assert first.next_cursor is not None

    second = adapter.get_ohlcv(
        _ohlcv_request(
            adjustment=PriceAdjustment.SPLIT_AND_DIVIDEND,
            cursor=first.next_cursor,
        )
    )
    assert len(second.items) == 1
    assert second.next_cursor is None
    with pytest.raises(DataInvalidRequestError):
        adapter.get_ohlcv(_ohlcv_request(cursor=first.next_cursor))


@pytest.mark.unit
def test_raw_and_total_return_adjustment_are_explicit_and_split_only_is_rejected() -> None:
    boundary = RecordingBoundary()
    adapter = _adapter(boundary)
    adapter.get_ohlcv(_ohlcv_request(page_size=10, adjustment=PriceAdjustment.RAW))
    assert "history:AAPL:1d:False:30" in boundary.calls

    before = tuple(boundary.calls)
    with pytest.raises(DataUnsupportedError) as caught:
        adapter.get_ohlcv(_ohlcv_request(adjustment=PriceAdjustment.SPLIT))
    assert caught.value.reason_code == "YAHOO_SPLIT_ONLY_UNSUPPORTED"
    assert tuple(boundary.calls) == before


@pytest.mark.unit
def test_splits_and_dividends_are_normalized_with_missing_date_quality() -> None:
    result = _adapter().get_corporate_actions(_actions_request())

    assert [item.action_type.value for item in result.items] == ["DIVIDEND", "SPLIT"]
    assert isinstance(result.items[0], DividendObservation)
    assert isinstance(result.items[1], SplitObservation)
    assert result.items[0].cash_amount == Decimal("0.25")
    assert result.items[1].split_ratio == Decimal("4")
    assert all(QualityFlag.MISSING_FIELDS in item.provenance.quality_flags for item in result.items)
    assert result.provenance.is_delayed is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "error_type", "reason_code"),
    [
        (
            lambda rows: (rows[0], rows[0]),
            DataConflictError,
            "YAHOO_DUPLICATE_INDEX",
        ),
        (
            lambda rows: tuple(reversed(rows)),
            DataConflictError,
            "YAHOO_INDEX_OUT_OF_ORDER",
        ),
        (
            lambda rows: (
                yahoo_module._YahooBar(
                    timestamp=rows[0].timestamp.replace(tzinfo=None),
                    open=rows[0].open,
                    high=rows[0].high,
                    low=rows[0].low,
                    close=rows[0].close,
                    volume=rows[0].volume,
                ),
            ),
            DataSchemaError,
            "YAHOO_TIMEZONE_MISSING",
        ),
    ],
)
def test_invalid_history_indexes_fail_closed(
    mutation: Callable[[tuple[yahoo_module._YahooBar, ...]], tuple[yahoo_module._YahooBar, ...]],
    error_type: type[DataProviderError],
    reason_code: str,
) -> None:
    boundary = RecordingBoundary()
    boundary.bars = mutation(boundary.bars)

    with pytest.raises(error_type) as caught:
        _adapter(boundary).get_ohlcv(_ohlcv_request(page_size=10))
    assert caught.value.reason_code == reason_code


@pytest.mark.unit
@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_missing_or_malformed_bar_values_are_stable_incomplete_errors(field: str) -> None:
    boundary = RecordingBoundary()
    bar = boundary.bars[0]
    boundary.bars = (
        yahoo_module._YahooBar(
            timestamp=bar.timestamp,
            open=None if field == "open" else bar.open,
            high=None if field == "high" else bar.high,
            low=None if field == "low" else bar.low,
            close=None if field == "close" else bar.close,
            volume=None if field == "volume" else bar.volume,
        ),
    )

    with pytest.raises(DataIncompleteError) as caught:
        _adapter(boundary).get_ohlcv(_ohlcv_request(page_size=10))
    assert caught.value.reason_code == "YAHOO_REQUIRED_VALUE_MISSING"


@pytest.mark.unit
def test_empty_results_have_capability_specific_semantics() -> None:
    boundary = RecordingBoundary()
    boundary.quote_value = None
    with pytest.raises(DataIncompleteError) as caught:
        _adapter(boundary).get_quotes(QuoteRequest(instrument_ids=("rh_inst_aapl_xnas",)))
    assert caught.value.reason_code == "YAHOO_EMPTY_QUOTE"

    boundary.bars = ()
    empty_bars = _adapter(boundary).get_ohlcv(_ohlcv_request())
    assert empty_bars.items == ()
    boundary.action_rows = ()
    empty_actions = _adapter(boundary).get_corporate_actions(_actions_request())
    assert empty_actions.items == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "error_type", "reason_code"),
    [
        (TimeoutError(), DataTimeoutError, "YAHOO_TIMEOUT"),
        (yahoo_module._YahooRateLimited(), DataRateLimitError, "YAHOO_RATE_LIMIT"),
        (
            yahoo_module._YahooTransportFailed(),
            DataUpstreamError,
            "YAHOO_UPSTREAM_FAILURE",
        ),
        (RuntimeError("sensitive upstream detail"), DataUpstreamError, "YAHOO_UPSTREAM_FAILURE"),
    ],
)
def test_transport_failures_are_stable_and_sanitized(
    failure: Exception, error_type: type[DataProviderError], reason_code: str
) -> None:
    boundary = RecordingBoundary()
    boundary.failure = failure
    with pytest.raises(error_type) as caught:
        _adapter(boundary).get_quotes(QuoteRequest(instrument_ids=("rh_inst_aapl_xnas",)))
    assert caught.value.reason_code == reason_code
    assert "sensitive" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.unit
def test_action_amounts_reject_negative_and_non_numeric_values() -> None:
    for bad in ("-0.1", "not-a-number", None):
        boundary = RecordingBoundary()
        boundary.action_rows = (
            yahoo_module._YahooAction(
                timestamp=datetime(2026, 6, 10, tzinfo=UTC),
                dividend=bad,
                split="0",
            ),
        )
        with pytest.raises((DataIncompleteError, DataSchemaError)):
            _adapter(boundary).get_corporate_actions(_actions_request())


@pytest.mark.unit
def test_request_bounds_and_unknown_instrument_fail_before_transport() -> None:
    boundary = RecordingBoundary()
    adapter = _adapter(boundary)
    with pytest.raises(DataInvalidRequestError):
        adapter.get_ohlcv(
            _ohlcv_request(
                interval="1m",
                start_at="2026-07-01T00:00:00Z",
                end_at="2026-07-20T00:00:00Z",
            )
        )
    with pytest.raises(DataInvalidRequestError):
        adapter.get_ohlcv(_ohlcv_request(interval="3h"))
    with pytest.raises(DataNotFoundError) as caught:
        adapter.get_quotes(QuoteRequest(instrument_ids=("rh_inst_missing_xnas",)))
    assert caught.value.reason_code == "YAHOO_INSTRUMENT_NOT_FOUND"
    assert boundary.calls == []


@pytest.mark.unit
def test_optional_dependency_is_lazy_and_has_stable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = importlib.import_module

    def missing_yfinance(name: str, package: str | None = None) -> Any:
        if name == "yfinance":
            raise ModuleNotFoundError(name)
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", missing_yfinance)
    adapter = YahooDevelopmentAdapter(
        mode=TradingMode.RESEARCH,
        instruments=(_instrument(),),
        clock=lambda: NOW,
    )
    with pytest.raises(DataUnsupportedError) as caught:
        adapter.get_quotes(QuoteRequest(instrument_ids=("rh_inst_aapl_xnas",)))
    assert caught.value.reason_code == "YAHOO_OPTIONAL_DEPENDENCY_MISSING"


@pytest.mark.unit
def test_fake_boundary_proves_all_canonical_calls_are_network_free() -> None:
    boundary = RecordingBoundary()
    adapter = _adapter(boundary)
    adapter.get_quotes(QuoteRequest(instrument_ids=("rh_inst_aapl_xnas",)))
    adapter.get_ohlcv(_ohlcv_request(page_size=10))
    adapter.get_corporate_actions(_actions_request())
    assert boundary.calls == [
        "quote:AAPL:30",
        "history:AAPL:1d:False:30",
        "actions:AAPL:30",
    ]


@pytest.mark.unit
def test_injected_ticker_boundary_maps_frames_without_import_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    price_row: dict[str, object] = {
        "Open": "210",
        "High": "214",
        "Low": "209",
        "Close": "213",
        "Volume": "1000",
        "Dividends": "0.25",
        "Stock Splits": "4",
    }
    timestamp = datetime(2026, 7, 22, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    ticker = _Ticker([_Frame(((timestamp, price_row),)) for _ in range(3)])

    def forbidden_import(name: str, package: str | None = None) -> Any:
        del name, package
        raise AssertionError("injected ticker must avoid optional import")

    monkeypatch.setattr(importlib, "import_module", forbidden_import)
    boundary = yahoo_module._YFinanceBoundary(ticker_factory=lambda symbol: ticker)
    quote = boundary.quote("AAPL", timeout_seconds=7)
    bars = boundary.history(
        "AAPL",
        start_at=datetime(2026, 7, 20, tzinfo=UTC),
        end_at=datetime(2026, 7, 25, tzinfo=UTC),
        interval="1d",
        auto_adjust=True,
        timeout_seconds=8,
    )
    actions = boundary.actions(
        "AAPL",
        effective_from=date(2026, 7, 1),
        effective_to=date(2026, 8, 1),
        timeout_seconds=9,
    )

    assert quote is not None and quote.last_price == "213"
    assert bars[0].volume == "1000"
    assert actions[0].dividend == "0.25"
    assert ticker.calls[0]["timeout"] == 7
    assert ticker.calls[1]["auto_adjust"] is True
    assert ticker.calls[2]["actions"] is True


@pytest.mark.unit
def test_malformed_ticker_frame_maps_to_schema_error_without_leaking_details() -> None:
    timestamp = datetime(2026, 7, 24, 15, 59, tzinfo=ZoneInfo("America/New_York"))
    ticker = _Ticker([_Frame(((timestamp, {"not_close": "sensitive-value"}),))])
    adapter = YahooDevelopmentAdapter(
        mode=TradingMode.RESEARCH,
        instruments=(_instrument(),),
        clock=lambda: NOW,
        boundary=yahoo_module._YFinanceBoundary(ticker_factory=lambda symbol: ticker),
    )

    with pytest.raises(DataSchemaError) as caught:
        adapter.get_quotes(QuoteRequest(instrument_ids=("rh_inst_aapl_xnas",)))
    assert caught.value.reason_code == "YAHOO_RESPONSE_MALFORMED"
    assert "sensitive-value" not in str(caught.value)
    assert caught.value.__cause__ is None
