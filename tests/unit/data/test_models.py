"""Boundary and failure-path tests for P04-T0 data models and fakes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ainvest.data import (
    DataErrorCode,
    DataOperation,
    DataProviderError,
    DataSchemaError,
    DataUnsupportedError,
    DeterministicFakeDataProvider,
    FundamentalObservation,
    InstrumentMetadataRequest,
    ObservationBatch,
    OhlcvRequest,
    PriceBook,
    PriceBookRequest,
    PriceLevel,
    QuoteRequest,
    fixture_dataset,
)
from ainvest.schemas.common import Provenance, QualityFlag


@pytest.mark.unit
@pytest.mark.parametrize("timeout", (0, 121))
def test_request_timeout_is_bounded(timeout: int) -> None:
    with pytest.raises(ValidationError):
        QuoteRequest(
            instrument_ids=("rh_inst_aapl_xnas",),
            timeout_seconds=timeout,
        )


@pytest.mark.unit
def test_request_rejects_duplicates_and_missing_metadata_filters() -> None:
    with pytest.raises(ValidationError, match="instrument_ids must be unique"):
        QuoteRequest(
            instrument_ids=("rh_inst_aapl_xnas", "rh_inst_aapl_xnas"),
        )
    with pytest.raises(ValidationError, match="at least one"):
        InstrumentMetadataRequest()


@pytest.mark.unit
def test_request_rejects_naive_or_inverted_time_windows() -> None:
    with pytest.raises(ValidationError):
        OhlcvRequest.model_validate(
            {
                "instrument_id": "rh_inst_aapl_xnas",
                "interval": "1d",
                "start_at": "2026-07-20T00:00:00",
                "end_at": "2026-07-25T00:00:00Z",
            }
        )
    with pytest.raises(ValidationError, match="start_at must be before end_at"):
        OhlcvRequest.model_validate(
            {
                "instrument_id": "rh_inst_aapl_xnas",
                "interval": "1d",
                "start_at": "2026-07-25T00:00:00Z",
                "end_at": "2026-07-20T00:00:00Z",
            }
        )


@pytest.mark.unit
def test_fake_rejects_unavailable_adjustment_instead_of_relabeling_bars() -> None:
    request = OhlcvRequest.model_validate(
        {
            "instrument_id": "rh_inst_aapl_xnas",
            "interval": "1d",
            "start_at": "2026-07-20T00:00:00Z",
            "end_at": "2026-07-25T00:00:00Z",
            "adjustment": "SPLIT",
        }
    )

    with pytest.raises(DataUnsupportedError) as caught:
        DeterministicFakeDataProvider().get_ohlcv(request)

    assert caught.value.code is DataErrorCode.UNSUPPORTED
    assert caught.value.reason_code == "FAKE_ADJUSTMENT_UNSUPPORTED"


@pytest.mark.unit
def test_price_book_rejects_unsorted_duplicate_and_crossed_levels() -> None:
    provider = DeterministicFakeDataProvider()
    book = provider.get_price_books(
        request=PriceBookRequest(instrument_ids=("rh_inst_aapl_xnas",))
    ).items[0]
    payload = book.model_dump(mode="json")
    payload["bids"] = [
        PriceLevel.model_validate({"price": "215.39", "quantity": "10"}).model_dump(mode="json"),
        PriceLevel.model_validate({"price": "215.40", "quantity": "20"}).model_dump(mode="json"),
    ]
    with pytest.raises(ValidationError, match="highest to lowest"):
        PriceBook.model_validate(payload)

    payload = book.model_dump(mode="json")
    payload["asks"] = [
        PriceLevel.model_validate({"price": "215.44", "quantity": "10"}).model_dump(mode="json"),
        PriceLevel.model_validate({"price": "215.44", "quantity": "20"}).model_dump(mode="json"),
    ]
    with pytest.raises(ValidationError, match="unique"):
        PriceBook.model_validate(payload)

    payload = book.model_dump(mode="json")
    payload["bids"] = [
        PriceLevel.model_validate({"price": "215.45", "quantity": "10"}).model_dump(mode="json")
    ]
    with pytest.raises(ValidationError, match="best bid"):
        PriceBook.model_validate(payload)


@pytest.mark.unit
@pytest.mark.parametrize(("missing_side", "remaining_side"), (("bids", "asks"), ("asks", "bids")))
def test_one_sided_book_requires_explicit_quality_flag(
    missing_side: str,
    remaining_side: str,
) -> None:
    provider = DeterministicFakeDataProvider()
    book = provider.get_price_books(
        request=PriceBookRequest(instrument_ids=("rh_inst_aapl_xnas",))
    ).items[0]
    payload = book.model_dump(mode="json")
    payload[missing_side] = []
    assert payload[remaining_side]
    with pytest.raises(ValidationError, match="one-sided or empty"):
        PriceBook.model_validate(payload)

    payload["provenance"]["quality_flags"] = [QualityFlag.MISSING_FIELDS.value]
    assert getattr(PriceBook.model_validate(payload), missing_side) == ()


@pytest.mark.unit
def test_empty_book_requires_explicit_quality_flag() -> None:
    book = (
        DeterministicFakeDataProvider()
        .get_price_books(request=PriceBookRequest(instrument_ids=("rh_inst_aapl_xnas",)))
        .items[0]
    )
    payload = book.model_dump(mode="json")
    payload["bids"] = []
    payload["asks"] = []
    with pytest.raises(ValidationError, match="one-sided or empty"):
        PriceBook.model_validate(payload)

    payload["provenance"]["quality_flags"] = [QualityFlag.PARTIAL.value]
    validated = PriceBook.model_validate(payload)
    assert validated.bids == validated.asks == ()


@pytest.mark.unit
def test_batch_rejects_mixed_sources_and_missing_envelope_flags() -> None:
    provider = DeterministicFakeDataProvider()
    first = provider.get_quotes(QuoteRequest(instrument_ids=("rh_inst_aapl_xnas",)))
    quote = first.items[0]
    other_payload = quote.model_dump(mode="json")
    other_payload["provenance"]["source"] = "other.fake.v1"
    other = type(quote).model_validate(other_payload)
    with pytest.raises(ValidationError, match="mix sources"):
        ObservationBatch(items=(quote, other), provenance=first.provenance)

    delayed_payload = quote.model_dump(mode="json")
    delayed_payload["provenance"]["is_delayed"] = True
    delayed_payload["provenance"]["quality_flags"] = [QualityFlag.DELAYED.value]
    delayed = type(quote).model_validate(delayed_payload)
    with pytest.raises(ValidationError, match="delayed response envelope"):
        ObservationBatch(items=(delayed,), provenance=first.provenance)


@pytest.mark.unit
def test_fake_preserves_non_utc_source_timezone_in_response_envelope() -> None:
    payload = fixture_dataset().model_dump(mode="json")
    payload["provenance"]["timezone"] = "America/New_York"
    payload["quotes"] = payload["quotes"][:1]
    payload["quotes"][0]["provenance"]["timezone"] = "America/New_York"
    payload["price_books"] = []
    payload["ohlcv"] = []
    payload["fundamentals"] = []
    payload["news_events"] = []
    payload["instrument_metadata"] = []
    provider = DeterministicFakeDataProvider(dataset=payload)

    result = provider.get_quotes(QuoteRequest(instrument_ids=("rh_inst_aapl_xnas",)))

    assert result.items[0].provenance.timezone == "America/New_York"
    assert result.provenance.timezone == "America/New_York"


@pytest.mark.unit
def test_inconsistent_raw_fake_dataset_becomes_stable_schema_error() -> None:
    payload = fixture_dataset().model_dump(mode="json")
    payload["quotes"][0]["provenance"]["source"] = "other.fake.v1"

    with pytest.raises(DataSchemaError) as caught:
        DeterministicFakeDataProvider(dataset=payload)

    assert caught.value.code is DataErrorCode.SCHEMA_INCOMPATIBLE
    assert caught.value.operation is DataOperation.DATASET
    assert caught.value.reason_code == "FAKE_DATASET_INVALID"


@pytest.mark.unit
def test_fundamental_observation_requires_filing_bound_citation() -> None:
    observation = fixture_dataset().fundamentals[0]
    payload = observation.model_dump(mode="json")
    payload["citations"] = [
        citation for citation in payload["citations"] if citation["kind"] != "FILING"
    ]

    with pytest.raises(ValidationError, match="filing citation"):
        FundamentalObservation.model_validate(payload)


@pytest.mark.unit
def test_data_provider_error_base_is_abstract() -> None:
    with pytest.raises(TypeError, match="abstract"):
        DataProviderError(
            "bad",
            operation=DataOperation.QUOTES,
            reason_code="TEST_FAILURE",
        )


@pytest.mark.unit
def test_provenance_enforces_delayed_flag_locally() -> None:
    with pytest.raises(ValidationError, match="DELAYED"):
        Provenance.model_validate(
            {
                "source": "test.fake",
                "observed_at": "2026-07-24T18:29:58Z",
                "received_at": "2026-07-24T18:30:00Z",
                "is_delayed": True,
            }
        )
