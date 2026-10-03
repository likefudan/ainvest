"""Published integration artifacts must match code and validate without ainvest."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "docs/api/generated"
pytestmark = pytest.mark.contract


def test_reference_artifacts_match_code() -> None:
    exporter = runpy.run_path(str(ROOT / "scripts/export_api_reference.py"))
    expected = exporter["render_artifacts"]()
    assert {path.name for path in GENERATED.iterdir()} == set(expected)
    for name, content in expected.items():
        assert (GENERATED / name).read_text() == content, f"regenerate {name}"


def test_export_check_detects_missing_changed_and_extra_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = runpy.run_path(str(ROOT / "scripts/export_api_reference.py"))
    main = exporter["main"]
    monkeypatch.setitem(main.__globals__, "TARGET", tmp_path)
    monkeypatch.setattr(sys, "argv", ["export_api_reference", "--check"])
    assert main() == 1
    monkeypatch.setattr(sys, "argv", ["export_api_reference"])
    assert main() == 0
    monkeypatch.setattr(sys, "argv", ["export_api_reference", "--check"])
    assert main() == 0
    (tmp_path / "examples.json").write_text("{}\n")
    assert main() == 1
    monkeypatch.setattr(sys, "argv", ["export_api_reference"])
    assert main() == 0
    (tmp_path / "unexpected.json").write_text("{}\n")
    monkeypatch.setattr(sys, "argv", ["export_api_reference", "--check"])
    assert main() == 1


@pytest.mark.parametrize("kind", ["WorkflowCommand", "WorkflowEvent"])
def test_published_example_validates_without_domain_model(kind: str) -> None:
    schema = json.loads((GENERATED / f"{kind}.json").read_text())
    Draft202012Validator.check_schema(schema)
    examples = json.loads((GENERATED / "examples.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(examples[kind])


@pytest.mark.parametrize("kind", ["WorkflowCommand", "WorkflowEvent"])
@pytest.mark.parametrize("change", ["version", "extra", "type", "naive", "bad_date", "id"])
def test_published_contract_rejects_invalid_payload(kind: str, change: str) -> None:
    schema = json.loads((GENERATED / f"{kind}.json").read_text())
    payload = json.loads((GENERATED / "examples.json").read_text())[kind]
    timestamp = "issued_at" if kind == "WorkflowCommand" else "occurred_at"
    if change == "version":
        payload["schema_version"] = "99.0"
    elif change == "extra":
        payload["extra_field"] = True
    elif change == "type":
        payload["command_type" if kind == "WorkflowCommand" else "event_type"] = "UNKNOWN"
    elif change == "naive":
        payload[timestamp] = "2026-10-01T12:00:00"
    elif change == "bad_date":
        payload[timestamp] = "2026-13-99T12:00:00Z"
    else:
        payload["idempotency_id"] = "invalid"
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(payload))


def test_reference_preserves_unknown_write_recovery() -> None:
    catalog = json.loads((GENERATED / "catalog.json").read_text())
    for name in ("EXECUTE_ORDER", "CANCEL_ORDER"):
        assert catalog["commands"][name]["retry"] == "BROKER_WRITE"
    assert catalog["order"]["recovery_only"] == {"SUBMIT_UNKNOWN": ["RECONCILING"]}
    assert catalog["cancel"]["recovery_only"] == {"CANCEL_UNKNOWN": ["CANCEL_RECONCILING"]}
    assert "UNKNOWN_OUTCOME" in catalog["codes"]["broker"]
    assert "INVALID_APPROVAL_TOKEN" in catalog["codes"]["approval_service"]
