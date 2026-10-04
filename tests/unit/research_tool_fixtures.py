"""Synthetic normalized reads; no credentials, accounts or network."""

from datetime import timedelta
from decimal import Decimal

from indicator_fixtures import snapshot
from pydantic import AnyUrl

from ainvest.agents.tools import ReadSources, ToolScope
from ainvest.agents.tools.models import FilingPage
from ainvest.data.models import NewsEventRequest, PriceBook, PriceLevel
from ainvest.data.providers.news import NewsAdapter, SourceRecord
from ainvest.data.providers.sec import SecFilingMetadata
from ainvest.schemas.portfolio import (
    AccountScope,
    ExposureSnapshot,
    PortfolioSnapshot,
    PositionSnapshot,
)


def fixtures() -> tuple[ToolScope, ReadSources]:
    saved = snapshot()
    instrument = saved.inputs.expected.instrument
    now = saved.inputs.expected.as_of
    provenance = saved.inputs.quote.provenance
    scope = ToolScope(
        run_id="research_tools_001", instrument=instrument, as_of=now, max_age_seconds=120
    )
    position = PositionSnapshot(
        instrument=instrument,
        quantity=Decimal(2),
        market_value=Decimal(320),
        portfolio_weight=Decimal("0.32"),
    )
    portfolio = PortfolioSnapshot(
        snapshot_id="portfolio_synthetic_001",
        account_scope=AccountScope.PAPER,
        as_of=now,
        cash=Decimal(680),
        buying_power=Decimal(680),
        equity=Decimal(1000),
        positions=(position,),
        exposure=ExposureSnapshot(
            cash=Decimal(680),
            equity=Decimal(1000),
            gross_market_value=Decimal(320),
            net_market_value=Decimal(320),
            largest_position_weight=Decimal("0.32"),
            position_count=1,
        ),
        provenance=provenance,
    )
    book = PriceBook(
        instrument=instrument,
        bids=(PriceLevel(price=Decimal("159.9"), quantity=Decimal(100)),),
        asks=(PriceLevel(price=Decimal("160.1"), quantity=Decimal(100)),),
        provenance=provenance,
    )
    record = SourceRecord(
        url=AnyUrl("https://issuer.example/news"),
        publisher="Synthetic issuer",
        headline="Synthetic announcement",
        published_at=now - timedelta(minutes=1),
        received_at=now,
        symbols=("TEST",),
        source_kind="COMPANY_IR",
        license_name="Metadata only",
        event_type="COMPANY_ANNOUNCEMENT",
    )
    news = NewsAdapter(records=(record,), captured_at=now).get_news_events(
        NewsEventRequest(start_at=now - timedelta(days=1), end_at=now)
    )
    filings = FilingPage(
        instrument=instrument,
        items=(
            SecFilingMetadata(
                accession_number="0000000001-26-000001",
                form_type="8-K",
                filed_at=now - timedelta(days=1),
                report_period_end=None,
                primary_document_url=AnyUrl(
                    "https://www.sec.gov/Archives/edgar/data/1/synthetic.htm"
                ),
                provenance=provenance,
            ),
        ),
        provenance=provenance,
    )
    return scope, ReadSources(
        quote=lambda request: saved.inputs.quote,
        price_book=lambda request: book,
        history=lambda request, history: saved.inputs.bars,
        filings=lambda request: filings,
        news=lambda request: news,
        portfolio=lambda request: portfolio,
    )
