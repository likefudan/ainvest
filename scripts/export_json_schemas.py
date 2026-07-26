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


def _write_extra_invalids(model_dir: Path, valid: dict[str, Any]) -> None:
    decimal_path = _find_key_path(valid, _DECIMAL_KEYS)
    if decimal_path is not None:
        floaty = deepcopy(valid)
        _set_path(floaty, decimal_path, 1.25)
        (model_dir / "invalid_binary_float.json").write_text(
            dump_schema_document(floaty),
            encoding="utf-8",
        )

    timestamp_path = _find_key_path(valid, _TIMESTAMP_KEYS)
    if timestamp_path is not None:
        naive = deepcopy(valid)
        _set_path(naive, timestamp_path, "2026-07-24T18:30:00")
        (model_dir / "invalid_naive_timestamp.json").write_text(
            dump_schema_document(naive),
            encoding="utf-8",
        )


def _write_fixtures() -> None:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    for name, model in EXPORTED_MODELS.items():
        if name not in EXAMPLE_BUILDERS:
            raise SystemExit(f"missing example builder for exported model {name}")
        model_dir = FIXTURE_ROOT / name
        model_dir.mkdir(parents=True, exist_ok=True)
        valid = example_payload(name)
        # Confirm the golden payload validates before writing.
        model.model_validate(valid)
        (model_dir / "valid.json").write_text(
            dump_schema_document(valid),
            encoding="utf-8",
        )

        unknown = deepcopy(valid)
        unknown["unexpected_extension_field"] = "boom"
        (model_dir / "invalid_unknown_field.json").write_text(
            dump_schema_document(unknown),
            encoding="utf-8",
        )

        bad_version = deepcopy(valid)
        bad_version["schema_version"] = "not-a-version"
        (model_dir / "invalid_schema_version.json").write_text(
            dump_schema_document(bad_version),
            encoding="utf-8",
        )

        _write_extra_invalids(model_dir, valid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if committed schemas drift from live Pydantic models",
    )
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="also regenerate contract fixture JSON under tests/contract/fixtures",
    )
    args = parser.parse_args(argv)

    if args.check:
        problems = check_json_schemas()
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
