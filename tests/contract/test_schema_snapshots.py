"""Contract tests for committed JSON Schema snapshots (P02-T5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ainvest.schemas.export import (
    EXPORTED_MODELS,
    SCHEMA_JSON_V1,
    check_json_schemas,
    dump_schema_document,
    render_model_json_schema,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures"


@pytest.mark.contract
def test_exported_json_schemas_match_live_models() -> None:
    """CI detects unintended breaking or additive schema drift."""
    problems = check_json_schemas()
    assert problems == [], "\n".join(problems)


@pytest.mark.contract
@pytest.mark.parametrize("model_name", sorted(EXPORTED_MODELS))
def test_each_exported_schema_file_is_canonical(model_name: str) -> None:
    path = SCHEMA_JSON_V1 / f"{model_name}.json"
    assert path.is_file(), f"missing {path}"
    expected = dump_schema_document(render_model_json_schema(EXPORTED_MODELS[model_name]))
    assert path.read_text(encoding="utf-8") == expected


@pytest.mark.contract
def test_manifest_lists_every_exported_model() -> None:
    manifest = json.loads((SCHEMA_JSON_V1 / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["major"] == 1
    assert manifest["models"] == sorted(EXPORTED_MODELS)


@pytest.mark.contract
def test_approval_challenge_minor_artifacts_preserve_v1_0_exactly() -> None:
    old = json.loads((SCHEMA_JSON_V1 / "ApprovalChallenge.json").read_text(encoding="utf-8"))
    latest = json.loads((SCHEMA_JSON_V1 / "ApprovalChallengeV1_1.json").read_text(encoding="utf-8"))

    assert old["properties"]["schema_version"]["const"] == "1.0"
    assert old["$defs"]["ApprovalChallengeStatus"]["enum"] == [
        "PENDING",
        "CONSUMED",
        "EXPIRED",
        "CANCELLED",
    ]
    assert latest["properties"]["schema_version"]["const"] == "1.1"
    assert set(latest["$defs"]["ApprovalChallengeStatusV1_1"]["enum"]) == {
        "PENDING",
        "APPROVED",
        "REJECTED",
        "CONSUMED",
        "EXPIRED",
        "CANCELLED",
    }
