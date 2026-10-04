"""Provider-local details survive serialization; shared ports remain unchanged."""

from datetime import date, datetime

from ainvest.data.calendar import ExchangeCalendar
from ainvest.data.calendar_port import MarketCalendar
from ainvest.data.models import NewsEventRequest
from ainvest.data.ports import LiveQuotePort, NewsEventPort
from ainvest.data.providers.news import NewsAdapter, NewsPage, SourceRecord


def test_read_only_contract() -> None:
    now = datetime.fromisoformat("2026-07-23T16:00:00Z")
    source = SourceRecord.model_validate(
        dict(
            url="https://publisher.example/macro",
            publisher="Example",
            headline="Macro discovery",
            published_at=now,
            received_at=now,
            source_kind="DISCOVERY",
            license_name="metadata only",
            event_type="MACRO_EVENT",
        )
    )
    adapter = NewsAdapter(records=(source,), captured_at=now)
    assert isinstance(adapter, NewsEventPort)
    assert not isinstance(adapter, LiveQuotePort)
    query = NewsEventRequest.model_validate(
        dict(start_at="2026-07-23T00:00:00Z", end_at="2026-07-24T00:00:00Z")
    )
    result = adapter.get_news_events(query)
    assert NewsPage.model_validate_json(result.model_dump_json()) == result
    assert adapter.get_news_events(query) == result
    assert result.items[0].symbols == ()
    assert isinstance(
        ExchangeCalendar(valid_from=date(2026, 1, 1), valid_through=date(2026, 12, 31)),
        MarketCalendar,
    )
