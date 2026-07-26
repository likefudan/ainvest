"""Unit tests for market and research schemas (P02-T1)."""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from ainvest.schemas.market import MarketQuote, OhlcvBar, TechnicalIndicators
from ainvest.schemas.research import (
    EvidenceCitation,
    EvidenceKind,
    ResearchPacket,
    parse_research_packet,
    research_packet_example,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _example() -> dict[str, object]:
    return deepcopy(research_packet_example())


@pytest.mark.unit
def test_design_research_packet_example_validates() -> None:
    packet = parse_research_packet(_example())
    assert packet.symbol == "AAPL"
    assert packet.market.last_price == Decimal("215.42")
    assert packet.technical is not None
    assert packet.technical.sma_20 == Decimal("211.30")
    dumped = json.loads(packet.model_dump_json())
    assert dumped["market"]["last_price"] == "215.42"
    assert dumped["as_of"].endswith("Z")


@pytest.mark.unit
def test_golden_fixture_matches_design_example() -> None:
    golden = json.loads((FIXTURES / "research_packet_valid.json").read_text(encoding="utf-8"))
    packet = ResearchPacket.model_validate(golden)
    assert packet.research_id == "res_01HZYEXAMPLE0001"
    round_trip = json.loads(packet.model_dump_json())
    assert round_trip["symbol"] == "AAPL"


@pytest.mark.unit
def test_rejects_float_prices_and_naive_timestamps() -> None:
    payload = _example()
    market = payload["market"]
    assert isinstance(market, dict)
    market["last_price"] = 215.42
    with pytest.raises(ValidationError, match="binary floats"):
        parse_research_packet(payload)

    payload = _example()
    payload["as_of"] = "2026-07-24T18:30:00"
    with pytest.raises(ValidationError, match="naive"):
        parse_research_packet(payload)


@pytest.mark.unit
def test_rejects_invalid_time_ordering() -> None:
    payload = _example()
    market = payload["market"]
    assert isinstance(market, dict)
    provenance = market["provenance"]
    assert isinstance(provenance, dict)
    provenance["received_at"] = "2026-07-24T18:29:00Z"
    with pytest.raises(ValidationError, match="received_at"):
        parse_research_packet(payload)


@pytest.mark.unit
def test_stale_or_delayed_data_is_explicitly_flagged() -> None:
    payload = _example()
    market = payload["market"]
    assert isinstance(market, dict)
    provenance = market["provenance"]
    assert isinstance(provenance, dict)
    provenance["is_delayed"] = True
    with pytest.raises(ValidationError, match="DELAYED"):
        parse_research_packet(payload)

    provenance["quality_flags"] = ["DELAYED"]
    packet = parse_research_packet(payload)
    assert packet.flagged_stale()


@pytest.mark.unit
def test_natural_language_cannot_become_numeric_evidence() -> None:
    with pytest.raises(ValidationError, match="calculation_source"):
        EvidenceCitation.model_validate(
            {
                "evidence_id": "evd_01NARRATIVE01",
                "kind": EvidenceKind.FUNDAMENTAL,
                "source": "analyst.note",
                "summary": "Revenue will grow a lot next year",
                "observed_at": "2026-07-24T18:00:00Z",
                "received_at": "2026-07-24T18:01:00Z",
                "numeric_value": "12.5",
            }
        )


@pytest.mark.unit
def test_calculated_evidence_requires_numeric_and_source() -> None:
    citation = EvidenceCitation.model_validate(
        {
            "evidence_id": "evd_01CALC0001",
            "kind": "CALCULATED",
            "source": "ainvest.indicators.v1",
            "summary": "SMA20 computed from daily closes",
            "observed_at": "2026-07-24T18:29:58Z",
            "received_at": "2026-07-24T18:30:00Z",
            "numeric_value": "211.30",
            "calculation_source": "ainvest.indicators.sma",
        }
    )
    assert citation.numeric_value == Decimal("211.30")


@pytest.mark.unit
def test_market_quote_and_ohlcv_validation() -> None:
    instrument = {
        "instrument_id": "rh_inst_aapl_xnas",
        "symbol": "AAPL",
        "exchange": "XNAS",
        "currency": "USD",
        "asset_type": "EQUITY",
        "identity_as_of": "2026-07-24T18:30:00Z",
    }
    provenance = {
        "source": "robinhood.mcp.quotes",
        "observed_at": "2026-07-24T18:29:58Z",
        "received_at": "2026-07-24T18:30:00Z",
    }
    quote = MarketQuote.model_validate(
        {
            "instrument": instrument,
            "last_price": "215.42",
            "bid": "215.40",
            "ask": "215.44",
            "provenance": provenance,
        }
    )
    assert quote.ask == Decimal("215.44")
    with pytest.raises(ValidationError):
        MarketQuote.model_validate(
            {
                "instrument": instrument,
                "last_price": "215.42",
                "bid": "215.50",
                "ask": "215.40",
                "provenance": provenance,
            }
        )

    bar = OhlcvBar.model_validate(
        {
            "instrument": instrument,
            "interval": "1d",
            "bar_start": "2026-07-23T13:30:00Z",
            "open": "210.00",
            "high": "216.00",
            "low": "209.00",
            "close": "215.00",
            "volume": "1000000",
            "provenance": provenance,
        }
    )
    assert bar.volume == Decimal("1000000")


@pytest.mark.unit
def test_technical_rsi_bounds() -> None:
    with pytest.raises(ValidationError):
        TechnicalIndicators.model_validate(
            {
                "symbol": "AAPL",
                "rsi_14": "101.0",
                "provenance": {
                    "source": "ainvest.indicators.v1",
                    "observed_at": "2026-07-24T18:29:58Z",
                    "received_at": "2026-07-24T18:30:00Z",
                },
            }
        )


@pytest.mark.unit
def test_unknown_fields_rejected_on_research_packet() -> None:
    payload = _example()
    payload["surprise"] = True
    with pytest.raises(ValidationError):
        parse_research_packet(payload)


@pytest.mark.unit
def test_json_schema_can_be_emitted_for_research_packet() -> None:
    """P02-T1 requires schema generation; versioned export lands in P02-T5."""
    schema = ResearchPacket.model_json_schema()
    assert schema["title"] == "ResearchPacket"
    assert "research_id" in schema["properties"]
