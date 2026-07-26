#!/usr/bin/env python3
"""Write or check committed JSON Schema snapshots and contract fixtures."""

from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from pathlib import Path

from ainvest.schemas.examples import EXAMPLE_BUILDERS, example_payload
from ainvest.schemas.export import (
    EXPORTED_MODELS,
    check_json_schemas,
    dump_schema_document,
    export_json_schemas,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "contract" / "fixtures"


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
