"""SEC's capability projection stays typed, deterministic and non-trading."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ainvest.data.models import FundamentalRequest, ObservationPage, SecFundamentalObservation
from ainvest.data.ports import FundamentalsPort, LivePriceBookPort, LiveQuotePort
from ainvest.data.providers.sec import SecEdgarAdapter
from ainvest.schemas.common import AssetType, InstrumentIdentity, QualityFlag

pytestmark = pytest.mark.contract


def test_sec_provider_has_only_ordinary_fundamental_capability() -> None:
    root = Path(__file__).resolve().parents[2] / "fixtures/sec"
    captured = datetime(2026, 5, 4, tzinfo=UTC)
    provider: FundamentalsPort = SecEdgarAdapter(
        instrument=InstrumentIdentity(
            instrument_id="synthetic-test",
            symbol="TEST",
            exchange="XNAS",
            currency="USD",
            asset_type=AssetType.EQUITY,
            identity_as_of=datetime(2025, 1, 1, tzinfo=UTC),
        ),
        cik="1234",
        submissions=json.loads((root / "company.json").read_text()),
        companyfacts=json.loads((root / "facts.json").read_text()),
        captured_at=captured,
    )
    assert isinstance(provider, FundamentalsPort)
    assert not isinstance(provider, LiveQuotePort)
    assert not isinstance(provider, LivePriceBookPort)
    request = FundamentalRequest(symbols=("TEST",), as_of=captured)
    first = provider.get_fundamentals(request)
    assert first.model_dump_json() == provider.get_fundamentals(request).model_dump_json()
    restored = ObservationPage[SecFundamentalObservation].model_validate_json(
        first.model_dump_json()
    )
    assert len(restored.items) == 3
    assert QualityFlag.PARTIAL in restored.provenance.quality_flags
    assert all(item.provenance.received_at <= request.as_of for item in restored.items)
