"""Contract fixture validation for exported domain schemas (P02-T5)."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from ainvest.approval.order_hash import compute_order_hash, parse_order_proposal
from ainvest.schemas.examples import example_payload
from ainvest.schemas.export import EXPORTED_MODELS, render_model_json_schema

FIXTURE_ROOT = Path(__file__).parent / "fixtures"


@pytest.mark.contract
@pytest.mark.parametrize("model_name", sorted(EXPORTED_MODELS))
def test_each_schema_has_valid_and_invalid_fixtures(model_name: str) -> None:
    model = EXPORTED_MODELS[model_name]
    model_dir = FIXTURE_ROOT / model_name
    valid_path = model_dir / "valid.json"
    assert valid_path.is_file(), f"missing valid fixture for {model_name}"
    valid_payload = json.loads(valid_path.read_text(encoding="utf-8"))
    model.model_validate(valid_payload)
    assert valid_payload == example_payload(model_name)

    json_schema_validator = Draft202012Validator(
        render_model_json_schema(model),
        format_checker=FormatChecker(),
    )
    assert list(json_schema_validator.iter_errors(valid_payload)) == []

    invalid_paths = sorted(model_dir.glob("invalid_*.json"))
    assert len(invalid_paths) >= 2, f"{model_name} needs multiple invalid fixtures"
    # Prefer at least one semantic invalid beyond unknown-field / schema_version.
    semantic = [
        path
        for path in invalid_paths
        if path.name
        not in {
            "invalid_unknown_field.json",
            "invalid_schema_version.json",
        }
    ]
    assert semantic, f"{model_name} needs a semantic invalid fixture (float/timestamp/...)"
    for path in invalid_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        with pytest.raises(ValidationError):
            model.model_validate(payload)
        errors = list(json_schema_validator.iter_errors(payload))
        assert errors, f"exported JSON Schema accepted invalid fixture {model_name}/{path.name}"


@pytest.mark.contract
@pytest.mark.parametrize("model_name", sorted(EXPORTED_MODELS))
def test_v1_models_reject_unsupported_payload_versions(model_name: str) -> None:
    """A v1 validator must not silently accept a document claiming v2 semantics."""
    model = EXPORTED_MODELS[model_name]
    payload = example_payload(model_name)
    payload["schema_version"] = "2.0"
    with pytest.raises(ValidationError, match="schema_version"):
        model.model_validate(payload)

    schema = render_model_json_schema(model)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(payload))


@pytest.mark.contract
def test_domain_models_forbid_unknown_fields() -> None:
    """Unknown-field policy is fail-closed for every exported contract."""
    for model in EXPORTED_MODELS.values():
        assert model.model_config.get("extra") == "forbid"


@pytest.mark.contract
def test_order_proposal_fixture_hash_matches_canonical_digest() -> None:
    """Golden OrderProposal fixtures must stay approval-bound, not merely shaped."""
    payload = json.loads(
        (FIXTURE_ROOT / "OrderProposal" / "valid.json").read_text(encoding="utf-8")
    )
    assert payload["order_hash"] == compute_order_hash(payload)
    parse_order_proposal(payload)

    tampered = deepcopy(payload)
    tampered["order_hash"] = "sha256:" + ("0" * 64)
    with pytest.raises(ValueError, match="order_hash"):
        parse_order_proposal(tampered)
