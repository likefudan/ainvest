"""Offline contract evidence for the pinned rh-mcp v0.4.1 Part 2 reads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from ainvest.execution.robinhood import pins
from ainvest.execution.robinhood.mappers import (
    map_closed_equity_orders,
    map_equity_fundamentals,
    map_equity_historicals,
    map_equity_price_books,
    map_equity_tradability,
    map_financials,
)
from ainvest.execution.robinhood.prose import discard_provider_prose
from ainvest.execution.robinhood.read_client import GatewayReadResult

ROOT: Final = Path(__file__).resolve().parents[2] / "fixtures" / "rh_mcp" / "v0.4.1"
MANIFEST: Final = ROOT / "read-manifest.json"
FIXTURES: Final = ROOT / "p06-t1-part2"
CAPABILITIES: Final = (
    "get_equity_price_book",
    "get_equity_tradability",
    "get_equity_historicals",
    "get_equity_fundamentals",
    "get_financials",
    "get_equity_orders",
)
OBSERVED_AT: Final = "2026-08-08T15:00:02Z"
RECEIVED_AT: Final = "2026-08-08T15:00:03Z"
RESULT_DIGEST: Final = f"sha256:{'c' * 64}"


def _manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _fixture(capability: str) -> dict[str, Any]:
    value = json.loads((FIXTURES / f"{capability}.json").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _result(capability: str) -> GatewayReadResult:
    payload = discard_provider_prose(_fixture(capability))
    assert isinstance(payload, dict)
    return GatewayReadResult(
        capability=capability,
        manifest_version=pins.PINNED_MANIFEST_VERSION,
        manifest_digest=pins.EXPECTED_MANIFEST_DIGEST,
        schema_digest=next(
            entry["schema_digest"]
            for entry in _manifest()["entries"]
            if entry["capability"] == capability
        ),
        result_digest=RESULT_DIGEST,
        observed_at=OBSERVED_AT,
        payload=payload,
        warnings=(),
    )


@pytest.mark.contract
@pytest.mark.parametrize("capability", CAPABILITIES)
def test_sanitized_part2_fixture_conforms_to_pinned_output_schema(capability: str) -> None:
    entry = next(entry for entry in _manifest()["entries"] if entry["capability"] == capability)
    validator = Draft202012Validator(entry["output_schema"], format_checker=FormatChecker())

    validator.validate(_fixture(capability))
    assert entry["disposition"] == "allowed"
    assert entry["mutates"] is False


@pytest.mark.contract
def test_projection_expansion_is_exactly_one_reviewed_read() -> None:
    expected = {
        "get_accounts",
        "get_equity_fundamentals",
        "get_equity_historicals",
        "get_equity_orders",
        "get_equity_positions",
        "get_equity_price_book",
        "get_equity_quotes",
        "get_equity_tradability",
        "get_financials",
        "get_portfolio",
    }

    assert {capability.value for capability in pins.ReadCapability} == expected
    assert expected <= pins.MANIFEST_READ_CAPABILITIES
    assert expected.isdisjoint(pins.APPROVED_NON_TRADING_MUTATIONS)
    assert expected.isdisjoint(pins.DENIED_TRADING_CAPABILITIES)


@pytest.mark.contract
def test_all_part2_models_preserve_evidence_and_exclude_provider_prose() -> None:
    mapped = (
        (
            "get_equity_price_book",
            map_equity_price_books(
                _result("get_equity_price_book"),
                received_at=RECEIVED_AT,
                expected_symbols=("AAPL", "MSFT"),
            ),
        ),
        (
            "get_equity_tradability",
            map_equity_tradability(
                _result("get_equity_tradability"),
                received_at=RECEIVED_AT,
                expected_symbols=("AAPL", "MSFT"),
            ),
        ),
        (
            "get_equity_historicals",
            map_equity_historicals(
                _result("get_equity_historicals"),
                received_at=RECEIVED_AT,
                expected_symbols=("AAPL", "MSFT"),
            ),
        ),
        (
            "get_equity_fundamentals",
            map_equity_fundamentals(
                _result("get_equity_fundamentals"),
                received_at=RECEIVED_AT,
                expected_symbols=("AAPL", "MSFT"),
            ),
        ),
        (
            "get_financials",
            map_financials(
                _result("get_financials"),
                received_at=RECEIVED_AT,
                expected_symbols=("AAPL", "MSFT"),
            ),
        ),
        (
            "get_equity_orders",
            map_closed_equity_orders(
                _result("get_equity_orders"),
                received_at=RECEIVED_AT,
                expected_symbol="AAPL",
            ),
        ),
    )

    entries = {entry["capability"]: entry for entry in _manifest()["entries"]}
    for capability, value in mapped:
        rendered = value.model_dump_json()
        assert value.evidence.manifest_digest == pins.EXPECTED_MANIFEST_DIGEST
        assert value.evidence.schema_digest == entries[capability]["schema_digest"]
        assert value.evidence.result_digest == RESULT_DIGEST
        assert "guide" not in rendered
        assert "Discard this provider-authored" not in rendered
        assert "proposal_id" not in rendered
        assert "order_hash" not in rendered
        assert "client_order_id" not in rendered
