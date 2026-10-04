"""Fixed monotonic bars and explicit expected timestamps; no live data."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ainvest.data.indicators import digest_bytes
from ainvest.data.models import OhlcvPage, PriceAdjustment
from ainvest.data.quality import BarExpectation
from ainvest.data.snapshots import ResearchSnapshot, ResponseDigest, SnapshotInputs, build_snapshot
from ainvest.schemas.common import AssetType, InstrumentIdentity, Provenance
from ainvest.schemas.market import MarketQuote, OhlcvBar


def sample(count: int = 60) -> tuple[OhlcvPage, BarExpectation]:
    start = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)
    instrument = InstrumentIdentity(
        instrument_id="test_instrument_001",
        symbol="TEST",
        exchange="XNAS",
        currency="USD",
        asset_type=AssetType.EQUITY,
        identity_as_of=start,
    )
    bars = tuple(
        OhlcvBar(
            instrument=instrument,
            interval="1m",
            bar_start=start + timedelta(minutes=i),
            open=Decimal(101 + i),
            high=Decimal(102 + i),
            low=Decimal(100 + i),
            close=Decimal(101 + i),
            volume=Decimal(1000),
            provenance=Provenance(
                source="fixture.market",
                observed_at=start + timedelta(minutes=i + 1),
                received_at=start + timedelta(minutes=i + 1),
            ),
        )
        for i in range(count)
    )
    end = start + timedelta(minutes=count)
    page = OhlcvPage(
        instrument_id=instrument.instrument_id,
        interval="1m",
        adjustment=PriceAdjustment.RAW,
        items=bars,
        provenance=Provenance(source="fixture.market", observed_at=end, received_at=end),
    )
    expected = BarExpectation(
        instrument=instrument,
        provider="fixture.market",
        timeframe="1m",
        adjustment=PriceAdjustment.RAW,
        as_of=end + timedelta(minutes=1),
        max_age_seconds=120,
        expected_starts=tuple(bar.bar_start for bar in bars),
    )
    return page, expected


def snapshot() -> ResearchSnapshot:
    page, expected = sample()
    quote = MarketQuote(
        instrument=expected.instrument,
        last_price=Decimal(160),
        bid=Decimal("159.9"),
        ask=Decimal("160.1"),
        provenance=page.provenance,
    )
    digest = digest_bytes(b"synthetic response")
    inputs = SnapshotInputs(
        expected=expected,
        quote=quote,
        bars=page,
        raw_digests=(
            ResponseDigest(source="fixture.market", role="quote", raw_response_digest=digest),
            ResponseDigest(source="fixture.market", role="bars", raw_response_digest=digest),
        ),
    )
    return build_snapshot(inputs, research_id="research_fixture_001")
