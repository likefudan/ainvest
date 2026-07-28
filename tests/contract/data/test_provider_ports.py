"""Shared provider contract tests for normalized P04-T0 data ports."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from ainvest.data import (
    DataErrorCode,
    DataInvalidRequestError,
    DataNotFoundError,
    DataOperation,
    DataTimeoutError,
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
)
from ainvest.schemas.common import Provenance
from ainvest.schemas.market import MarketQuote

ProviderFactory = Callable[[], DeterministicFakeDataProvider]

# Future provider contract suites add their recorded/no-network factory here or
# reuse the individual assertions below. No factory may call a public network.
PROVIDER_FACTORIES: tuple[ProviderFactory, ...] = (DeterministicFakeDataProvider,)


@pytest.fixture(params=PROVIDER_FACTORIES, ids=("deterministic-fake",))
def provider(request: pytest.FixtureRequest) -> DeterministicFakeDataProvider:
    factory: ProviderFactory = request.param
    return factory()


@pytest.mark.contract
def test_provider_exposes_every_read_port(provider: DeterministicFakeDataProvider) -> None:
    assert isinstance(provider, QuotePort)
    assert isinstance(provider, PriceBookPort)
    assert isinstance(provider, OhlcvPort)
    assert isinstance(provider, FundamentalsPort)
    assert isinstance(provider, InstrumentMetadataPort)
    assert isinstance(provider, NewsEventPort)


@pytest.mark.contract
def test_quote_contract_is_normalized_complete_and_deterministic(
    provider: DeterministicFakeDataProvider,
) -> None:
    request = QuoteRequest(instrument_ids=("rh_inst_spy_arcx", "rh_inst_aapl_xnas"))

    first = provider.get_quotes(request)
    second = provider.get_quotes(request)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert tuple(item.instrument.instrument_id for item in first.items) == request.instrument_ids
    _assert_provenance(first, provider.source_id)


@pytest.mark.contract
def test_price_book_contract_applies_depth_and_preserves_provenance(
    provider: DeterministicFakeDataProvider,
) -> None:
    result = provider.get_price_books(
        PriceBookRequest(instrument_ids=("rh_inst_aapl_xnas",), depth=1)
    )

    assert len(result.items) == 1
    assert len(result.items[0].bids) == 1
    assert len(result.items[0].asks) == 1
    _assert_provenance(result, provider.source_id)


@pytest.mark.contract
def test_ohlcv_contract_uses_opaque_stable_pagination(
    provider: DeterministicFakeDataProvider,
) -> None:
    first = provider.get_ohlcv(
        OhlcvRequest.model_validate(
            {
                "instrument_id": "rh_inst_aapl_xnas",
                "interval": "1d",
                "start_at": "2026-07-20T00:00:00Z",
                "end_at": "2026-07-25T00:00:00Z",
                "page_size": 1,
            }
        )
    )
    assert len(first.items) == 1
    assert first.next_cursor is not None
    assert first.adjustment.value == "RAW"
    assert first.instrument_id == "rh_inst_aapl_xnas"

    second = provider.get_ohlcv(
        OhlcvRequest.model_validate(
            {
                "instrument_id": "rh_inst_aapl_xnas",
                "interval": "1d",
                "start_at": "2026-07-20T00:00:00Z",
                "end_at": "2026-07-25T00:00:00Z",
                "page_size": 1,
                "cursor": first.next_cursor,
            }
        )
    )

    assert len(second.items) == 1
    assert second.next_cursor is None
    assert first.items[0].bar_start < second.items[0].bar_start
    _assert_provenance(first, provider.source_id)
    _assert_provenance(second, provider.source_id)


@pytest.mark.contract
def test_fundamental_news_and_metadata_contracts(
    provider: DeterministicFakeDataProvider,
) -> None:
    fundamentals = provider.get_fundamentals(
        FundamentalRequest.model_validate({"symbols": ("AAPL",), "as_of": "2026-07-24T18:30:00Z"})
    )
    news = provider.get_news_events(
        NewsEventRequest.model_validate(
            {
                "start_at": "2026-07-23T00:00:00Z",
                "end_at": "2026-07-25T00:00:00Z",
            }
        )
    )
    metadata = provider.get_instrument_metadata(InstrumentMetadataRequest(symbols=("AAPL",)))

    assert fundamentals.items[0].symbol == "AAPL"
    assert {event.event_type for event in news.items} == {"EARNINGS", "MACRO_RATE"}
    assert metadata.items[0].instrument.instrument_id == "rh_inst_aapl_xnas"
    assert metadata.items[0].tradable is True
    for result in (fundamentals, news, metadata):
        _assert_provenance(result, provider.source_id)


@pytest.mark.contract
def test_missing_quote_uses_stable_not_found_error(
    provider: DeterministicFakeDataProvider,
) -> None:
    with pytest.raises(DataNotFoundError) as caught:
        provider.get_quotes(QuoteRequest(instrument_ids=("rh_inst_missing_xnas",)))

    assert caught.value.code is DataErrorCode.NOT_FOUND
    assert caught.value.operation is DataOperation.QUOTES
    assert caught.value.reason_code == "FAKE_INSTRUMENT_NOT_FOUND"
    assert caught.value.details == {"missing_count": "1"}


@pytest.mark.contract
def test_injected_failure_uses_code_not_message() -> None:
    provider = DeterministicFakeDataProvider(failures={DataOperation.QUOTES: DataErrorCode.TIMEOUT})

    with pytest.raises(DataTimeoutError) as caught:
        provider.get_quotes(QuoteRequest(instrument_ids=("rh_inst_aapl_xnas",)))

    assert caught.value.code is DataErrorCode.TIMEOUT
    assert caught.value.retryable is True


@pytest.mark.contract
def test_cursor_is_scoped_to_dataset_and_operation(
    provider: DeterministicFakeDataProvider,
) -> None:
    request = NewsEventRequest.model_validate(
        {
            "start_at": "2026-07-23T00:00:00Z",
            "end_at": "2026-07-25T00:00:00Z",
            "cursor": "another_dataset:NEWS_EVENTS:1",
        }
    )

    with pytest.raises(DataInvalidRequestError) as caught:
        provider.get_news_events(request)

    assert caught.value.code is DataErrorCode.INVALID_REQUEST
    assert caught.value.reason_code == "INVALID_PAGE_CURSOR"


@pytest.mark.contract
def test_live_ports_are_explicit_and_offer_no_fallback(
    provider: DeterministicFakeDataProvider,
) -> None:
    assert not isinstance(provider, LiveQuotePort)
    assert not isinstance(provider, LivePriceBookPort)
    assert not hasattr(LiveQuotePort, "fallback")
    assert not hasattr(LiveQuotePort, "fallback_sources")
    assert not hasattr(LivePriceBookPort, "fallback")


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
