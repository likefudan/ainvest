"""Contract fixture validation for exported domain schemas (P02-T5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ainvest.schemas.export import EXPORTED_MODELS

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

    invalid_paths = sorted(model_dir.glob("invalid_*.json"))
    assert len(invalid_paths) >= 2, f"{model_name} needs multiple invalid fixtures"
    for path in invalid_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        with pytest.raises(ValidationError):
            model.model_validate(payload)


@pytest.mark.contract
def test_domain_models_forbid_unknown_fields() -> None:
    """Unknown-field policy is fail-closed for every exported contract."""
    for model in EXPORTED_MODELS.values():
        assert model.model_config.get("extra") == "forbid"
