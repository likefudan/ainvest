"""Ensure committed fixture builders stay aligned with live models."""

from __future__ import annotations

import pytest

from ainvest.schemas.examples import EXAMPLE_BUILDERS, example_payload
from ainvest.schemas.export import EXPORTED_MODELS


@pytest.mark.unit
def test_every_exported_model_has_a_valid_example_builder() -> None:
    assert set(EXAMPLE_BUILDERS) == set(EXPORTED_MODELS)
    for name, model in EXPORTED_MODELS.items():
        model.model_validate(example_payload(name))
