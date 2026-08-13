"""The P06-T1 part 1 fixtures conform to the pinned rh-mcp v0.3.0 schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from jsonschema import Draft202012Validator

ROOT: Final = Path(__file__).resolve().parents[2] / "fixtures" / "rh_mcp" / "v0.3.0"
MANIFEST: Final = ROOT / "read-manifest.json"
FIXTURES: Final = ROOT / "p06-t1-part1"
CAPABILITIES: Final = (
    "get_accounts",
    "get_portfolio",
    "get_equity_positions",
    "get_equity_quotes",
    "get_equity_orders",
)


def _document(path: Path) -> dict[str, Any]:
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.mark.contract
@pytest.mark.parametrize("capability", CAPABILITIES)
def test_sanitized_mapping_fixture_matches_pinned_output_schema(capability: str) -> None:
    manifest = _document(MANIFEST)
    entry = next(item for item in manifest["entries"] if item.get("capability") == capability)
    fixture = _document(FIXTURES / f"{capability}.json")

    Draft202012Validator(entry["output_schema"]).validate(fixture)
    assert set(fixture) == {"data", "guide"}
    assert isinstance(fixture["guide"], str)


@pytest.mark.contract
def test_contract_covers_exactly_the_claimed_part1_capabilities() -> None:
    fixture_names = {path.stem for path in FIXTURES.glob("*.json")}
    assert fixture_names == set(CAPABILITIES)
