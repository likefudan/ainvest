"""Capability-scoped provider contract tests for normalized P04-T0 ports."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest
from pydantic import ValidationError

from ainvest.data import (
    DataErrorCode,
    DataIncompleteError,
    DataInvalidRequestError,
    DataNotFoundError,
    DataOperation,
    DataProviderError,
    DataRateLimitError,
    DataStaleError,
    DataTimeoutError,
    DataUpstreamError,
    DeterministicFakeDataProvider,
    FundamentalRequest,
    FundamentalsPort,
    InstrumentMetadataPort,
    InstrumentMetadataRequest,
    LivePriceBookPort,
    LiveQuotePort,
    NewsEventPort,
    NewsEventRequest,
    ObservationBatch,
    OhlcvPort,
    OhlcvRequest,
    PriceBookPort,
    PriceBookRequest,
    QuotePort,
    QuoteRequest,
    TimeCertainty,
)
from ainvest.schemas.common import Provenance, QualityFlag
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.research import EvidenceKind

QuoteFactory = Callable[[], QuotePort]
PriceBookFactory = Callable[[], PriceBookPort]
OhlcvFactory = Callable[[], OhlcvPort]
FundamentalsFactory = Callable[[], FundamentalsPort]
NewsEventFactory = Callable[[], NewsEventPort]
MetadataFactory = Callable[[], InstrumentMetadataPort]

# Each capability owns its factory list. A partial adapter joins only the lists
# for protocols it implements; no test requires one provider to expose all six.
QUOTE_FACTORIES: tuple[QuoteFactory, ...] = (DeterministicFakeDataProvider,)
PRICE_BOOK_FACTORIES: tuple[PriceBookFactory, ...] = (DeterministicFakeDataProvider,)
OHLCV_FACTORIES: tuple[OhlcvFactory, ...] = (DeterministicFakeDataProvider,)
FUNDAMENTALS_FACTORIES: tuple[FundamentalsFactory, ...] = (DeterministicFakeDataProvider,)
NEWS_EVENT_FACTORIES: tuple[NewsEventFactory, ...] = (DeterministicFakeDataProvider,)
METADATA_FACTORIES: tuple[MetadataFactory, ...] = (DeterministicFakeDataProvider,)


@pytest.fixture(params=QUOTE_FACTORIES, ids=("deterministic-fake",))
def quote_port(request: pytest.FixtureRequest) -> QuotePort:
    return cast(QuoteFactory, request.param)()


@pytest.fixture(params=PRICE_BOOK_FACTORIES, ids=("deterministic-fake",))
def price_book_port(request: pytest.FixtureRequest) -> PriceBookPort:
    return cast(PriceBookFactory, request.param)()


@pytest.fixture(params=OHLCV_FACTORIES, ids=("deterministic-fake",))
def ohlcv_port(request: pytest.FixtureRequest) -> OhlcvPort:
    return cast(OhlcvFactory, request.param)()


@pytest.fixture(params=FUNDAMENTALS_FACTORIES, ids=("deterministic-fake",))
def fundamentals_port(request: pytest.FixtureRequest) -> FundamentalsPort:
    return cast(FundamentalsFactory, request.param)()


@pytest.fixture(params=NEWS_EVENT_FACTORIES, ids=("deterministic-fake",))
def news_event_port(request: pytest.FixtureRequest) -> NewsEventPort:
    return cast(NewsEventFactory, request.param)()


@pytest.fixture(params=METADATA_FACTORIES, ids=("deterministic-fake",))
def metadata_port(request: pytest.FixtureRequest) -> InstrumentMetadataPort:
    return cast(MetadataFactory, request.param)()


# Quote capability


@pytest.mark.contract
def test_quote_contract_is_normalized_complete_and_deterministic(quote_port: QuotePort) -> None:
    request = QuoteRequest(instrument_ids=("rh_inst_spy_arcx", "rh_inst_aapl_xnas"))

    first = quote_port.get_quotes(request)
    second = quote_port.get_quotes(request)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert tuple(item.instrument.instrument_id for item in first.items) == request.instrument_ids
    _assert_provenance(first, quote_port.source_id)


@pytest.mark.contract
def test_missing_quote_uses_stable_not_found_error(quote_port: QuotePort) -> None:
    with pytest.raises(DataNotFoundError) as caught:
        quote_port.get_quotes(QuoteRequest(instrument_ids=("rh_inst_missing_xnas",)))

    assert caught.value.code is DataErrorCode.NOT_FOUND
    assert caught.value.operation is DataOperation.QUOTES


# Price-book capability


@pytest.mark.contract
def test_price_book_contract_applies_depth_and_preserves_provenance(
    price_book_port: PriceBookPort,
) -> None:
    result = price_book_port.get_price_books(
        PriceBookRequest(instrument_ids=("rh_inst_aapl_xnas",), depth=1)
    )

    assert len(result.items) == 1
    assert len(result.items[0].bids) == 1
    assert len(result.items[0].asks) == 1
    _assert_provenance(result, price_book_port.source_id)


# OHLCV capability


def _ohlcv_request(**updates: object) -> OhlcvRequest:
    payload: dict[str, object] = {
        "instrument_id": "rh_inst_aapl_xnas",
        "interval": "1d",
        "start_at": "2026-07-20T00:00:00Z",
        "end_at": "2026-07-25T00:00:00Z",
        "page_size": 1,
    }
    payload.update(updates)
    return OhlcvRequest.model_validate(payload)


@pytest.mark.contract
def test_ohlcv_contract_uses_query_bound_pagination(ohlcv_port: OhlcvPort) -> None:
    first = ohlcv_port.get_ohlcv(_ohlcv_request())
    assert len(first.items) == 1
    assert first.next_cursor is not None
    assert first.adjustment.value == "RAW"

    second = ohlcv_port.get_ohlcv(_ohlcv_request(cursor=first.next_cursor))

    assert len(second.items) == 1
    assert second.next_cursor is None
    assert first.items[0].bar_start < second.items[0].bar_start
    _assert_provenance(first, ohlcv_port.source_id)
    _assert_provenance(second, ohlcv_port.source_id)


@pytest.mark.contract
def test_ohlcv_cursor_cannot_be_reused_for_different_filters(ohlcv_port: OhlcvPort) -> None:
    first = ohlcv_port.get_ohlcv(_ohlcv_request())
    assert first.next_cursor is not None

    with pytest.raises(DataInvalidRequestError) as caught:
        ohlcv_port.get_ohlcv(
            _ohlcv_request(
                end_at="2026-07-24T00:00:00Z",
                cursor=first.next_cursor,
            )
        )

    assert caught.value.reason_code == "INVALID_PAGE_CURSOR"


@pytest.mark.contract
def test_unknown_ohlcv_instrument_is_not_a_valid_empty_window(ohlcv_port: OhlcvPort) -> None:
    with pytest.raises(DataNotFoundError) as caught:
        ohlcv_port.get_ohlcv(_ohlcv_request(instrument_id="rh_inst_unknown_xnas"))

    assert caught.value.operation is DataOperation.OHLCV


@pytest.mark.contract
def test_known_ohlcv_series_may_have_a_valid_empty_time_window(ohlcv_port: OhlcvPort) -> None:
    result = ohlcv_port.get_ohlcv(
        _ohlcv_request(
            start_at="2020-01-01T00:00:00Z",
            end_at="2020-01-02T00:00:00Z",
        )
    )

    assert result.items == ()
    assert QualityFlag.PARTIAL not in result.provenance.quality_flags
    assert QualityFlag.MISSING_FIELDS not in result.provenance.quality_flags


# Fundamental/filing capability


def _fundamental_request(*symbols: str) -> FundamentalRequest:
    return FundamentalRequest.model_validate({"symbols": symbols, "as_of": "2026-07-24T18:30:00Z"})


@pytest.mark.contract
def test_fundamental_contract_retains_period_filing_currency_and_citations(
    fundamentals_port: FundamentalsPort,
) -> None:
    result = fundamentals_port.get_fundamentals(_fundamental_request("AAPL"))
    observation = result.items[0]

    assert observation.instrument.symbol == observation.snapshot.symbol == "AAPL"
    assert observation.currency == "USD"
    assert observation.period.end_date == observation.filing.report_period_end
    assert observation.filing.accession_number == "0000320193-26-000001"
    assert observation.earnings_time_certainty is TimeCertainty.CONFIRMED
    assert observation.earnings_at is not None
    assert {citation.kind for citation in observation.citations} >= {
        EvidenceKind.FILING,
        EvidenceKind.FUNDAMENTAL,
    }
    _assert_provenance(result, fundamentals_port.source_id)


@pytest.mark.contract
def test_partial_fundamentals_are_explicitly_quality_flagged(
    fundamentals_port: FundamentalsPort,
) -> None:
    result = fundamentals_port.get_fundamentals(_fundamental_request("AAPL", "SPY"))

    assert tuple(item.snapshot.symbol for item in result.items) == ("AAPL",)
    assert {
        QualityFlag.PARTIAL,
        QualityFlag.MISSING_FIELDS,
    }.issubset(result.provenance.quality_flags)


@pytest.mark.contract
def test_fully_missing_fundamentals_are_not_an_unqualified_empty_page(
    fundamentals_port: FundamentalsPort,
) -> None:
    result = fundamentals_port.get_fundamentals(_fundamental_request("ZZZZ"))

    assert result.items == ()
    assert {
        QualityFlag.PARTIAL,
        QualityFlag.MISSING_FIELDS,
    }.issubset(result.provenance.quality_flags)


# News/event capability


@pytest.mark.contract
def test_news_event_contract_retains_publication_license_symbols_and_citations(
    news_event_port: NewsEventPort,
) -> None:
    result = news_event_port.get_news_events(
        NewsEventRequest.model_validate(
            {
                "start_at": "2026-07-23T00:00:00Z",
                "end_at": "2026-07-25T00:00:00Z",
                "symbols": ("MSFT",),
            }
        )
    )
    observation = result.items[0]

    assert observation.event.event_type == "EARNINGS"
    assert observation.symbols == ("AAPL", "MSFT")
    assert observation.url.scheme == "https"
    assert observation.publisher
    assert observation.license_name
    assert observation.published_at <= observation.provenance.observed_at
    assert observation.event_time_certainty is TimeCertainty.CONFIRMED
    assert len(observation.citations) == 2
    assert observation.related_filings[0].accession_number == "0000320193-26-000001"
    _assert_provenance(result, news_event_port.source_id)


# Instrument-metadata capability


@pytest.mark.contract
def test_metadata_contract_preserves_identity_precision_and_provenance(
    metadata_port: InstrumentMetadataPort,
) -> None:
    result = metadata_port.get_instrument_metadata(InstrumentMetadataRequest(symbols=("AAPL",)))
    observation = result.items[0]

    assert observation.instrument.instrument_id == "rh_inst_aapl_xnas"
    assert observation.tradable is True
    assert str(observation.price_increment) == "0.01"
    _assert_provenance(result, metadata_port.source_id)


# Cross-capability invariants


@pytest.mark.contract
@pytest.mark.parametrize(
    ("code", "error_type", "retryable"),
    (
        (DataErrorCode.TIMEOUT, DataTimeoutError, True),
        (DataErrorCode.RATE_LIMIT, DataRateLimitError, True),
        (DataErrorCode.STALE_DATA, DataStaleError, False),
        (DataErrorCode.INCOMPLETE_DATA, DataIncompleteError, False),
        (DataErrorCode.PROVIDER_FAILURE, DataUpstreamError, False),
    ),
)
def test_injected_failure_uses_stable_code_and_retryability(
    code: DataErrorCode,
    error_type: type[DataProviderError],
    retryable: bool,
) -> None:
    port: QuotePort = DeterministicFakeDataProvider(failures={DataOperation.QUOTES: code})

    with pytest.raises(error_type) as caught:
        port.get_quotes(QuoteRequest(instrument_ids=("rh_inst_aapl_xnas",)))

    assert caught.value.code is code
    assert caught.value.operation is DataOperation.QUOTES
    assert caught.value.reason_code == f"FAKE_{code.value}"
    assert caught.value.retryable is retryable


@pytest.mark.contract
def test_live_port_protocols_expose_no_fallback_api() -> None:
    assert not hasattr(LiveQuotePort, "fallback")
    assert not hasattr(LiveQuotePort, "fallback_sources")
    assert not hasattr(LivePriceBookPort, "fallback")
    assert not hasattr(LivePriceBookPort, "fallback_sources")


@pytest.mark.contract
def test_observation_without_provenance_cannot_cross_port() -> None:
    quote = (
        DeterministicFakeDataProvider()
        .get_quotes(QuoteRequest(instrument_ids=("rh_inst_aapl_xnas",)))
        .items[0]
    )
    incomplete = quote.model_dump(mode="json")
    incomplete.pop("provenance")

    with pytest.raises(ValidationError):
        MarketQuote.model_validate(incomplete)


def _assert_provenance(result: object, expected_source: str) -> None:
    assert isinstance(result, ObservationBatch)
    assert result.provenance.source == expected_source
    assert result.provenance.observed_at.tzinfo is not None
    assert result.provenance.received_at.tzinfo is not None
    for item in result.items:
        provenance = getattr(item, "provenance", None)
        assert isinstance(provenance, Provenance)
        assert provenance.source == expected_source
        assert provenance.observed_at.tzinfo is not None
        assert provenance.received_at.tzinfo is not None
