"""Unit tests for market and research schemas (P02-T1)."""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from ainvest.schemas.market import (
    FactValueKind,
    FundamentalFact,
    FundamentalSnapshot,
    MarketEvent,
    MarketQuote,
    OhlcvBar,
    TechnicalIndicators,
)
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
def test_rejects_look_ahead_instrument_identity() -> None:
    payload = _example()
    instrument = payload["instrument"]
    assert isinstance(instrument, dict)
    instrument["identity_as_of"] = "2026-07-24T19:00:00Z"
    with pytest.raises(ValidationError, match="identity_as_of"):
        parse_research_packet(payload)


@pytest.mark.unit
def test_rejects_look_ahead_technical_provenance() -> None:
    payload = _example()
    technical = payload["technical"]
    assert isinstance(technical, dict)
    provenance = technical["provenance"]
    assert isinstance(provenance, dict)
    provenance["observed_at"] = "2026-07-24T19:00:00Z"
    provenance["received_at"] = "2026-07-24T19:00:00Z"
    with pytest.raises(ValidationError, match=r"technical\.provenance"):
        parse_research_packet(payload)


@pytest.mark.unit
def test_rejects_look_ahead_evidence_provenance() -> None:
    payload = _example()
    payload["evidence"] = [
        {
            "evidence_id": "evd_01CALC0001",
            "kind": "CALCULATED",
            "summary": "future calc",
            "locator": "tool:ainvest.indicators.sma#run1",
            "numeric_value": "211.30",
            "calculation_source": "ainvest.indicators.sma",
            "provenance": {
                "source": "ainvest.indicators.v1",
                "observed_at": "2026-07-24T19:00:00Z",
                "received_at": "2026-07-24T19:00:00Z",
            },
        }
    ]
    with pytest.raises(ValidationError, match=r"evidence\[0\]\.provenance"):
        parse_research_packet(payload)


@pytest.mark.unit
def test_rejects_currency_mismatch_between_instrument_and_market() -> None:
    payload = _example()
    instrument = payload["instrument"]
    assert isinstance(instrument, dict)
    instrument["currency"] = "EUR"
    with pytest.raises(ValidationError, match="currency"):
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
def test_delayed_market_cannot_borrow_sibling_delayed_flag() -> None:
    """DELAYED must appear on the delayed provenance itself."""
    payload = _example()
    market = payload["market"]
    assert isinstance(market, dict)
    market_prov = market["provenance"]
    assert isinstance(market_prov, dict)
    market_prov["is_delayed"] = True
    market_prov["quality_flags"] = []

    technical = payload["technical"]
    assert isinstance(technical, dict)
    tech_prov = technical["provenance"]
    assert isinstance(tech_prov, dict)
    tech_prov["quality_flags"] = ["DELAYED"]

    with pytest.raises(ValidationError, match="DELAYED"):
        parse_research_packet(payload)


@pytest.mark.unit
def test_flagged_stale_aggregates_technical_and_evidence_provenance() -> None:
    payload = _example()
    technical = payload["technical"]
    assert isinstance(technical, dict)
    tech_prov = technical["provenance"]
    assert isinstance(tech_prov, dict)
    tech_prov["is_delayed"] = True
    with pytest.raises(ValidationError, match="DELAYED"):
        parse_research_packet(payload)

    tech_prov["quality_flags"] = ["DELAYED"]
    packet = parse_research_packet(payload)
    assert packet.flagged_stale()

    payload = _example()
    payload["evidence"] = [
        {
            "evidence_id": "evd_01STALE0001",
            "kind": "QUOTE",
            "summary": "Delayed quote citation",
            "locator": "quote:robinhood.mcp.quotes#AAPL",
            "provenance": {
                "source": "robinhood.mcp.quotes",
                "observed_at": "2026-07-24T18:29:58Z",
                "received_at": "2026-07-24T18:30:00Z",
                "timezone": "UTC",
                "is_delayed": False,
                "quality_flags": ["STALE"],
            },
        }
    ]
    packet = parse_research_packet(payload)
    assert packet.flagged_stale()


@pytest.mark.unit
def test_rejects_duplicate_evidence_ids() -> None:
    payload = _example()
    citation = {
        "evidence_id": "evd_01DUP000001",
        "kind": "QUOTE",
        "summary": "quote evidence",
        "locator": "quote:robinhood.mcp.quotes#AAPL",
        "provenance": {
            "source": "robinhood.mcp.quotes",
            "observed_at": "2026-07-24T18:29:58Z",
            "received_at": "2026-07-24T18:30:00Z",
        },
    }
    payload["evidence"] = [citation, {**citation, "summary": "same id again"}]
    with pytest.raises(ValidationError, match="evidence_id"):
        parse_research_packet(payload)


@pytest.mark.unit
def test_natural_language_without_locator_cannot_become_evidence() -> None:
    with pytest.raises(ValidationError):
        EvidenceCitation.model_validate(
            {
                "evidence_id": "evd_01NARRATIVE01",
                "kind": EvidenceKind.FUNDAMENTAL,
                "summary": "Revenue will grow a lot next year",
                "provenance": {
                    "source": "analyst.note",
                    "observed_at": "2026-07-24T18:00:00Z",
                    "received_at": "2026-07-24T18:01:00Z",
                },
            }
        )


@pytest.mark.unit
def test_rejects_pseudo_and_unstructured_evidence_locators() -> None:
    base = {
        "evidence_id": "evd_01NARRATIVE01",
        "kind": EvidenceKind.FUNDAMENTAL,
        "summary": "Unsupported narrative claim",
        "provenance": {
            "source": "analyst.note",
            "observed_at": "2026-07-24T18:00:00Z",
            "received_at": "2026-07-24T18:01:00Z",
        },
    }
    for locator in ("x", "http://example.com/note", "tool:", "notes:freeform", "TOOL:aapl"):
        with pytest.raises(ValidationError):
            EvidenceCitation.model_validate({**base, "locator": locator})

    citation = EvidenceCitation.model_validate(
        {**base, "locator": "filing:sec.edgar/0000320193-24-000001#Item8"}
    )
    assert citation.locator.startswith("filing:")


@pytest.mark.unit
def test_numeric_evidence_requires_calculation_source_and_allows_negative() -> None:
    with pytest.raises(ValidationError, match="calculation_source"):
        EvidenceCitation.model_validate(
            {
                "evidence_id": "evd_01NARRATIVE01",
                "kind": EvidenceKind.FUNDAMENTAL,
                "summary": "EPS estimate",
                "locator": "tool:edgar.xbrl#eps",
                "numeric_value": "-1.25",
                "provenance": {
                    "source": "sec.edgar",
                    "observed_at": "2026-07-24T18:00:00Z",
                    "received_at": "2026-07-24T18:01:00Z",
                },
            }
        )

    citation = EvidenceCitation.model_validate(
        {
            "evidence_id": "evd_01CALC0001",
            "kind": "CALCULATED",
            "summary": "Trailing EPS",
            "locator": "tool:ainvest.fundamentals.eps#ttm",
            "numeric_value": "-1.25",
            "calculation_source": "ainvest.fundamentals.eps",
            "provenance": {
                "source": "ainvest.fundamentals.v1",
                "observed_at": "2026-07-24T18:29:58Z",
                "received_at": "2026-07-24T18:30:00Z",
                "timezone": "UTC",
                "is_delayed": False,
            },
        }
    )
    assert citation.numeric_value == Decimal("-1.25")
    assert citation.provenance.timezone == "UTC"


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
def test_fundamental_snapshot_uses_typed_immutable_facts() -> None:
    snapshot = FundamentalSnapshot.model_validate(
        {
            "symbol": "AAPL",
            "as_of": "2026-07-24T18:30:00Z",
            "facts": [
                {
                    "key": "net_income",
                    "kind": FactValueKind.DECIMAL,
                    "decimal_value": "-10.50",
                    "unit": "USD",
                }
            ],
            "provenance": {
                "source": "sec.edgar",
                "observed_at": "2026-07-24T18:00:00Z",
                "received_at": "2026-07-24T18:30:00Z",
            },
        }
    )
    assert snapshot.facts[0].decimal_value == Decimal("-10.50")
    with pytest.raises((ValidationError, TypeError)):
        snapshot.facts[0].key = "tampered"
    with pytest.raises(ValidationError):
        FundamentalFact.model_validate(
            {
                "key": "bad_nan",
                "kind": "DECIMAL",
                "decimal_value": "NaN",
            }
        )


@pytest.mark.unit
def test_fundamental_fact_rejects_boolean_coercion() -> None:
    for coerced in (1, 0, "true", "false"):
        with pytest.raises(ValidationError):
            FundamentalFact.model_validate(
                {
                    "key": "is_active",
                    "kind": "BOOLEAN",
                    "boolean_value": coerced,
                }
            )
    fact = FundamentalFact.model_validate(
        {
            "key": "is_active",
            "kind": "BOOLEAN",
            "boolean_value": True,
        }
    )
    assert fact.boolean_value is True


@pytest.mark.unit
def test_fundamental_snapshot_rejects_received_after_as_of() -> None:
    with pytest.raises(ValidationError, match="received_at"):
        FundamentalSnapshot.model_validate(
            {
                "symbol": "AAPL",
                "as_of": "2026-07-24T18:30:00Z",
                "facts": [
                    {
                        "key": "net_income",
                        "kind": "DECIMAL",
                        "decimal_value": "1.00",
                        "unit": "USD",
                    }
                ],
                "provenance": {
                    "source": "sec.edgar",
                    "observed_at": "2026-07-24T18:00:00Z",
                    "received_at": "2026-07-24T19:00:00Z",
                },
            }
        )


@pytest.mark.unit
def test_market_event_time_order() -> None:
    event = MarketEvent.model_validate(
        {
            "event_id": "evt_01_8k",
            "symbol": "AAPL",
            "event_type": "SEC_8K",
            "headline": "Item 2.02 results",
            "occurred_at": "2026-07-24T17:00:00Z",
            "provenance": {
                "source": "sec.edgar",
                "observed_at": "2026-07-24T17:00:00Z",
                "received_at": "2026-07-24T17:05:00Z",
            },
        }
    )
    assert event.event_type == "SEC_8K"
    with pytest.raises(ValidationError, match="occurred_at"):
        MarketEvent.model_validate(
            {
                "event_id": "evt_01_8k",
                "symbol": "AAPL",
                "event_type": "SEC_8K",
                "headline": "Item 2.02 results",
                "occurred_at": "2026-07-24T18:00:00Z",
                "provenance": {
                    "source": "sec.edgar",
                    "observed_at": "2026-07-24T17:00:00Z",
                    "received_at": "2026-07-24T17:05:00Z",
                },
            }
        )
    # occurred before received but after observed must still fail
    with pytest.raises(ValidationError, match="occurred_at"):
        MarketEvent.model_validate(
            {
                "event_id": "evt_01_8k",
                "symbol": "AAPL",
                "event_type": "SEC_8K",
                "headline": "Item 2.02 results",
                "occurred_at": "2026-07-24T18:00:00Z",
                "provenance": {
                    "source": "sec.edgar",
                    "observed_at": "2026-07-24T17:00:00Z",
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
    from ainvest.schemas.research import EVIDENCE_LOCATOR_PATTERN

    schema = ResearchPacket.model_json_schema()
    assert schema["title"] == "ResearchPacket"
    assert "research_id" in schema["properties"]
    evidence = schema["$defs"]["EvidenceCitation"]["properties"]
    assert "provenance" in evidence
    assert evidence["locator"]["type"] == "string"
    assert evidence["locator"]["pattern"] == EVIDENCE_LOCATOR_PATTERN
