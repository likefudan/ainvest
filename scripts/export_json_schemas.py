#!/usr/bin/env python3
"""Write or check committed JSON Schema snapshots and contract fixtures."""

from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from ainvest.schemas.examples import EXAMPLE_BUILDERS, example_payload
from ainvest.schemas.export import (
    EXPORTED_MODELS,
    check_json_schemas,
    dump_schema_document,
    export_json_schemas,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "contract" / "fixtures"

_DECIMAL_KEYS = frozenset(
    {
        "quantity",
        "limit_price",
        "price",
        "last_price",
        "bid",
        "ask",
        "cash",
        "buying_power",
        "equity",
        "fees",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "sma_20",
        "sma_50",
        "rsi_14",
        "atr_14",
        "strength",
        "target_weight",
        "market_value",
        "portfolio_weight",
        "average_cost",
        "unrealized_pnl",
        "decimal_value",
        "numeric_value",
        "maximum_notional",
        "quantity_increment",
        "price_increment",
        "gross_market_value",
        "net_market_value",
        "largest_position_weight",
    }
)
_TIMESTAMP_KEYS = frozenset(
    {
        "as_of",
        "created_at",
        "expires_at",
        "decided_at",
        "observed_at",
        "received_at",
        "generated_at",
        "filled_at",
        "submitted_at",
        "updated_at",
        "requested_at",
        "approved_at",
        "occurred_at",
        "bar_start",
        "bar_end",
        "identity_as_of",
        "checked_at",
        "completed_at",
    }
)


def _find_key_path(payload: Any, keys: frozenset[str]) -> list[str] | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, str):
                return [key]
            nested = _find_key_path(value, keys)
            if nested is not None:
                return [key, *nested]
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            nested = _find_key_path(item, keys)
            if nested is not None:
                return [str(index), *nested]
    return None


def _set_path(payload: dict[str, Any], path: list[str], value: Any) -> None:
    cursor: Any = payload
    for part in path[:-1]:
        cursor = cursor[int(part)] if part.isdigit() else cursor[part]
    last = path[-1]
    if last.isdigit():
        cursor[int(last)] = value
    else:
        cursor[last] = value


def _fixture_documents(name: str) -> dict[str, dict[str, Any]]:
    """Build the complete deterministic fixture set for one exported model."""
    model = EXPORTED_MODELS[name]
    if name not in EXAMPLE_BUILDERS:
        raise SystemExit(f"missing example builder for exported model {name}")
    valid = example_payload(name)
    model.model_validate(valid)

    unknown = deepcopy(valid)
    unknown["unexpected_extension_field"] = "boom"

    unsupported_version = deepcopy(valid)
    unsupported_version["schema_version"] = "2.0"

    documents = {
        "valid.json": valid,
        "invalid_unknown_field.json": unknown,
        "invalid_schema_version.json": unsupported_version,
    }

    decimal_path = _find_key_path(valid, _DECIMAL_KEYS)
    if decimal_path is not None:
        floaty = deepcopy(valid)
        _set_path(floaty, decimal_path, 1.25)
        documents["invalid_binary_float.json"] = floaty

    timestamp_path = _find_key_path(valid, _TIMESTAMP_KEYS)
    if timestamp_path is not None:
        naive = deepcopy(valid)
        _set_path(naive, timestamp_path, "2026-07-24T18:30:00")
        documents["invalid_naive_timestamp.json"] = naive

    return documents


def _fixture_drift_problems() -> list[str]:
    """Return drift messages for committed deterministic contract fixtures."""
    if not FIXTURE_ROOT.is_dir():
        return [f"missing fixture root: {FIXTURE_ROOT}"]
    problems: list[str] = []
    expected_model_names = set(EXPORTED_MODELS)
    on_disk_model_names = {path.name for path in FIXTURE_ROOT.iterdir() if path.is_dir()}
    for name in sorted(expected_model_names - on_disk_model_names):
        problems.append(f"missing fixture directory: {name}")
    for name in sorted(on_disk_model_names - expected_model_names):
        problems.append(f"unexpected fixture directory: {name}")

    for name in sorted(EXPORTED_MODELS):
        model_dir = FIXTURE_ROOT / name
        if not model_dir.is_dir():
            continue
        expected = {
            filename: dump_schema_document(document)
            for filename, document in _fixture_documents(name).items()
        }
        on_disk = {path.name for path in model_dir.glob("*.json")}
        for filename in sorted(set(expected) - on_disk):
            problems.append(f"missing fixture: {name}/{filename}")
        for filename in sorted(on_disk - set(expected)):
            problems.append(f"unexpected fixture: {name}/{filename}")
        for filename, contents in expected.items():
            path = model_dir / filename
            if path.is_file() and path.read_text(encoding="utf-8") != contents:
                problems.append(
                    f"fixture drift for {name}/{filename}: "
                    "run ./scripts/dev export-schemas --fixtures"
                )
    return problems


def _write_fixtures() -> None:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    for name in EXPORTED_MODELS:
        model_dir = FIXTURE_ROOT / name
        model_dir.mkdir(parents=True, exist_ok=True)
        documents = _fixture_documents(name)
        for path in model_dir.glob("*.json"):
            if path.name not in documents:
                path.unlink()
        for filename, document in documents.items():
            (model_dir / filename).write_text(
                dump_schema_document(document),
                encoding="utf-8",
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if committed schemas or fixtures drift from their generators",
    )
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="also regenerate contract fixture JSON under tests/contract/fixtures",
    )
    args = parser.parse_args(argv)

    if args.check:
        problems = [*check_json_schemas(), *_fixture_drift_problems()]
        if problems:
            for item in problems:
                print(item, file=sys.stderr)
            return 1
        print("JSON Schema snapshots are up to date.")
        return 0

    written = export_json_schemas()
    print(f"wrote {len(written)} schema files under schemas/json/v1/")
    if args.fixtures:
        _write_fixtures()
        print(f"wrote fixtures under {FIXTURE_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
