"""Unit tests for shared domain primitives (P02-T0)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from ainvest.schemas.common import (
    DECIMAL_STRING_PATTERN,
    InstrumentIdentity,
    Money,
    PnL,
    Price,
    Provenance,
    QualityFlag,
    Ratio,
    Weight,
    decimal_json_schema,
    ensure_utc,
)
from ainvest.schemas.market import ResearchMarketSection
from ainvest.schemas.research import ResearchPacket


@pytest.mark.unit
def test_decimal_money_round_trip_rejects_float() -> None:
    adapter = TypeAdapter(Money)
    value = adapter.validate_python("12.34")
    assert value == Decimal("12.34")
    assert adapter.dump_python(value, mode="json") == "12.34"
    with pytest.raises(ValidationError, match="binary floats"):
        adapter.validate_python(1.25)


@pytest.mark.unit
def test_decimal_json_schema_is_string_only() -> None:
    """Generated validation schema must not allow JSON numbers."""
    fragment = decimal_json_schema()
    assert fragment["type"] == "string"
    assert fragment["pattern"] == DECIMAL_STRING_PATTERN

    price_schema = TypeAdapter(Price).json_schema()
    assert price_schema["type"] == "string"
    assert "anyOf" not in price_schema
    assert price_schema["pattern"] == DECIMAL_STRING_PATTERN

    packet_schema = ResearchPacket.model_json_schema()
    market_price = packet_schema["$defs"]["ResearchMarketSection"]["properties"]["last_price"]
    assert market_price["type"] == "string"
    assert "anyOf" not in market_price
    assert market_price["pattern"] == DECIMAL_STRING_PATTERN


@pytest.mark.unit
def test_pnl_allows_negative_money_does_not() -> None:
    pnl = TypeAdapter(PnL).validate_python("-15.50")
    assert pnl == Decimal("-15.50")
    with pytest.raises(ValidationError):
        TypeAdapter(Money).validate_python("-1.00")


@pytest.mark.unit
def test_ratio_and_weight_bounds() -> None:
    assert TypeAdapter(Ratio).validate_python("0.5") == Decimal("0.5")
    assert TypeAdapter(Weight).validate_python("1") == Decimal("1")
    with pytest.raises(ValidationError):
        TypeAdapter(Ratio).validate_python("1.01")
    with pytest.raises(ValidationError):
        TypeAdapter(Weight).validate_python("-0.01")


@pytest.mark.unit
def test_utc_datetime_rejects_naive_and_normalizes_z() -> None:
    aware = ensure_utc("2026-07-24T18:30:00Z")
    assert aware == datetime(2026, 7, 24, 18, 30, tzinfo=UTC)
    with pytest.raises(ValidationError):
        TypeAdapter(InstrumentIdentity).validate_python(
            {
                "instrument_id": "rh_inst_aapl",
                "symbol": "AAPL",
                "exchange": "XNAS",
                "currency": "USD",
                "asset_type": "EQUITY",
                "identity_as_of": "2026-07-24T18:30:00",
            }
        )


@pytest.mark.unit
def test_instrument_identity_requires_canonical_fields() -> None:
    identity = InstrumentIdentity.model_validate(
        {
            "instrument_id": "rh_inst_aapl_xnas",
            "symbol": "AAPL",
            "exchange": "XNAS",
            "currency": "USD",
            "asset_type": "EQUITY",
            "identity_as_of": "2026-07-24T18:30:00Z",
            "provider": "robinhood.mcp",
        }
    )
    payload = identity.model_dump(mode="json")
    assert payload["identity_as_of"].endswith("Z")
    assert "symbol" in payload and payload["instrument_id"] != payload["symbol"]
    with pytest.raises(ValidationError):
        InstrumentIdentity.model_validate(
            {
                "instrument_id": "rh_inst_aapl_xnas",
                "symbol": "aapl",
                "exchange": "XNAS",
                "currency": "USD",
                "asset_type": "EQUITY",
                "identity_as_of": "2026-07-24T18:30:00Z",
            }
        )


@pytest.mark.unit
def test_provenance_rejects_received_before_observed_and_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="received_at"):
        Provenance.model_validate(
            {
                "source": "robinhood.mcp.quotes",
                "observed_at": "2026-07-24T18:30:00Z",
                "received_at": "2026-07-24T18:29:00Z",
            }
        )
    with pytest.raises(ValidationError):
        Provenance.model_validate(
            {
                "source": "robinhood.mcp.quotes",
                "observed_at": "2026-07-24T18:30:00Z",
                "received_at": "2026-07-24T18:30:00Z",
                "unexpected": True,
            }
        )


@pytest.mark.unit
def test_nan_and_infinity_rejected() -> None:
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValidationError):
            TypeAdapter(Money).validate_python(bad)


@pytest.mark.unit
def test_json_round_trip_preserves_decimal_types() -> None:
    identity = InstrumentIdentity.model_validate(
        {
            "instrument_id": "rh_inst_msft_xnas",
            "symbol": "MSFT",
            "exchange": "XNAS",
            "currency": "USD",
            "asset_type": "EQUITY",
            "identity_as_of": "2026-07-24T18:30:00Z",
        }
    )
    raw = json.loads(identity.model_dump_json())
    restored = InstrumentIdentity.model_validate(raw)
    assert restored == identity
    assert QualityFlag.STALE.value == "STALE"
    section = ResearchMarketSection.model_validate(
        {
            "last_price": "1.00",
            "currency": "USD",
            "observed_at": "2026-07-24T18:30:00Z",
            "provenance": {
                "source": "robinhood.mcp.quotes",
                "observed_at": "2026-07-24T18:30:00Z",
                "received_at": "2026-07-24T18:30:00Z",
            },
        }
    )
    assert section.last_price == Decimal("1.00")
