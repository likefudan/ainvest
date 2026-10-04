"""Untrusted discovery never invents a publication time or primary source."""

from datetime import datetime

import httpx
import pytest

from ainvest.data.models import NewsEventRequest
from ainvest.data.ports import (
    DataInvalidRequestError,
    DataRateLimitError,
    DataSchemaError,
    DataTimeoutError,
    DataUpstreamError,
)
from ainvest.data.providers.news import (
    NewsAdapter,
    NewsPage,
    SourceRecord,
    capture_gdelt,
    normalize_gdelt,
    normalize_ir,
    sec_events,
)
from ainvest.data.providers.sec import SecFilingMetadata
from ainvest.schemas.common import Provenance

NOW = datetime.fromisoformat("2026-07-23T16:00:00Z")


def record(**updates: object) -> SourceRecord:
    payload: dict[str, object] = dict(
        url="https://issuer.example/news/one",
        publisher="Example issuer",
        headline="Example announcement",
        published_at="2026-07-23T14:00:00Z",
        received_at=NOW,
        symbols=("TEST",),
        source_kind="COMPANY_IR",
        license_name="Unspecified; metadata only",
        event_type="COMPANY_ANNOUNCEMENT",
    )
    payload.update(updates)
    return SourceRecord.model_validate(payload)


def request(**updates: object) -> NewsEventRequest:
    return NewsEventRequest.model_validate(
        dict(start_at="2026-07-23T00:00:00Z", end_at="2026-07-24T00:00:00Z", **updates)
    )


def test_dedup_preserves_sources_and_roundtrip() -> None:
    one = record(event_key="event:example/one")
    two = record(
        url="https://publisher.example/story",
        source_kind="DISCOVERY",
        event_key="event:example/one",
    )
    adapter = NewsAdapter(records=(two, one, one), captured_at=NOW)
    page = adapter.get_news_events(request())
    assert len(page.items) == 1
    item = page.items[0]
    assert len(item.sources) == len(item.citations) == 2
    assert "UNVERIFIED" in item.citations[1].provenance.quality_flags
    assert str(item.url) == str(one.url)
    assert NewsPage.model_validate_json(page.model_dump_json()) == page


def test_query_bound_pagination_and_knowledge_cutoff() -> None:
    adapter = NewsAdapter(
        records=(record(), record(url="https://issuer.example/news/two")), captured_at=NOW
    )
    first = adapter.get_news_events(request(page_size=1))
    assert first.next_cursor
    second = adapter.get_news_events(request(page_size=1, cursor=first.next_cursor))
    assert second.items != first.items and second.next_cursor is None
    with pytest.raises(DataInvalidRequestError):
        adapter.get_news_events(request(symbols=("OTHER",), cursor=first.next_cursor))
    assert (
        adapter.get_news_events(
            NewsEventRequest(
                start_at=datetime.fromisoformat("2026-07-23T00:00:00Z"),
                end_at=datetime.fromisoformat("2026-07-23T15:00:00Z"),
            )
        ).items
        == ()
    )


def test_future_and_naive_publication_rejected() -> None:
    for published in ("2026-07-24T00:00:00Z", "2026-07-23T12:00:00"):
        with pytest.raises(ValueError):
            record(published_at=published)


def test_gdelt_is_discovery_not_publication_or_symbol_evidence() -> None:
    payload = {
        "articles": [
            {
                "url": "https://publisher.example/story",
                "title": "Example",
                "domain": "publisher.example",
                "seendate": "20260723T140000Z",
            }
        ]
    }
    result = normalize_gdelt(payload, received_at=NOW)
    assert result[0].published_at is None
    assert result[0].symbols == ()
    assert result[0].seen_at.hour == 14
    payload["articles"][0]["seendate"] = "20260724T140000Z"
    with pytest.raises(DataSchemaError):
        normalize_gdelt(payload, received_at=NOW)


def test_ir_exact_host_and_sec_projection() -> None:
    rows = ({"url": "https://issuer.example/one", "title": "Release", "published_at": NOW},)
    assert (
        normalize_ir(
            rows,
            expected_host="issuer.example",
            publisher="Issuer",
            symbols=("TEST",),
            received_at=NOW,
            license_name="metadata only",
        )[0].source_kind
        == "COMPANY_IR"
    )
    with pytest.raises(DataSchemaError):
        normalize_ir(
            rows,
            expected_host="other.example",
            publisher="Issuer",
            symbols=("TEST",),
            received_at=NOW,
            license_name="metadata only",
        )
    filing = SecFilingMetadata.model_validate(
        dict(
            accession_number="0000001234-26-000001",
            form_type="8-K",
            filed_at=NOW,
            report_period_end=None,
            primary_document_url="https://www.sec.gov/Archives/edgar/data/1234/000000123426000001/report.htm",
            provenance=Provenance(source="sec.edgar.v1", observed_at=NOW, received_at=NOW),
        )
    )
    records = sec_events((filing,), symbol="TEST")
    page = NewsAdapter(records=records, captured_at=NOW).get_news_events(request())
    assert page.items[0].citations[0].locator == "filing:sec.edgar/0000001234-26-000001"


@pytest.mark.parametrize("status", [302, 403, 404, 429, 500])
def test_transport_no_redirect_or_retry(status: int) -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        assert req.url.host == "api.gdeltproject.org"
        return httpx.Response(status, headers={"location": "https://elsewhere.example/"})

    with pytest.raises(DataRateLimitError if status == 429 else DataUpstreamError):
        capture_gdelt(query="example", transport=httpx.MockTransport(handler))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body", [b"not json", b"[]", b'{"articles":[],"articles":[]}', b"x" * (2 * 1024 * 1024 + 1)]
)
def test_bad_transport_json(body: bytes) -> None:
    with pytest.raises(DataSchemaError):
        capture_gdelt(
            query="example",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
        )


def test_transport_success_timeout_and_invalid_config() -> None:
    assert (
        capture_gdelt(
            query="example",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"articles": []})),
        )
        == ()
    )

    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private provider body")

    with pytest.raises(DataTimeoutError, match=r"^Discovery timeout$"):
        capture_gdelt(query="example", transport=httpx.MockTransport(timeout))
    with pytest.raises(DataInvalidRequestError):
        capture_gdelt(query=" ", transport=httpx.MockTransport(timeout))


def test_bad_groups_and_capture() -> None:
    with pytest.raises(DataSchemaError):
        NewsAdapter(records=(record(),), captured_at=datetime.fromisoformat("2026-07-23T13:00:00Z"))
    with pytest.raises(DataSchemaError):
        NewsAdapter(records=(record(), record(event_type="OTHER_EVENT")), captured_at=NOW)
