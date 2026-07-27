"""Unit tests for Strategy API definitions and protocol (P03-T0)."""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from strategies.strategy_fixtures import DemoStrategy, make_metadata

from ainvest.strategies import (
    PluginMetadata,
    StrategyDefinition,
    StrategyError,
    StrategyParams,
    StrategyResult,
)


@pytest.mark.unit
def test_definition_from_type_does_not_evaluate() -> None:
    calls: list[str] = []

    class TrackingStrategy(DemoStrategy):
        def evaluate(self, context):  # type: ignore[no-untyped-def]
            calls.append("evaluate")
            return super().evaluate(context)

    definition = StrategyDefinition.from_type(TrackingStrategy, metadata=make_metadata())
    assert definition.name == "demo_strategy"
    assert calls == []
    strategy = definition.create({"alpha": 2})
    assert isinstance(strategy, TrackingStrategy)
    assert strategy.params.alpha == 2
    assert calls == []


@pytest.mark.unit
def test_rejects_missing_metadata_fields() -> None:
    with pytest.raises(StrategyError, match="missing plugin_id"):
        PluginMetadata(
            plugin_id="",
            plugin_version="1.0.0",
            ainvest_strategy_api=">=1.0.0,<2.0.0",
            source_commit="local",
            owner="ainvest",
            repository="tests/demo",
        ).validate()


@pytest.mark.unit
def test_rejects_incompatible_api_range() -> None:
    with pytest.raises(StrategyError, match="incompatible"):
        make_metadata(ainvest_strategy_api=">=2.0.0,<3.0.0").validate()


@pytest.mark.unit
def test_rejects_params_model_without_forbid() -> None:
    class LooseParams(BaseModel):
        model_config = ConfigDict(extra="allow")
        value: int = 1

    class LooseStrategy:
        name: ClassVar[str] = "loose_strategy"
        version: ClassVar[str] = "1.0.0"
        params_model: ClassVar[type[BaseModel]] = LooseParams

        def evaluate(self, context):  # type: ignore[no-untyped-def]
            del context
            return StrategyResult()

    with pytest.raises(StrategyError, match="extra='forbid'"):
        StrategyDefinition.from_type(LooseStrategy, metadata=make_metadata())


@pytest.mark.unit
def test_rejects_unknown_parameters() -> None:
    definition = StrategyDefinition.from_type(DemoStrategy, metadata=make_metadata())
    with pytest.raises(StrategyError, match="invalid parameters"):
        definition.validate_params({"alpha": 1, "unknown": 99})


@pytest.mark.unit
def test_strategy_params_base_forbids_extras() -> None:
    class Sample(StrategyParams):
        window: int = Field(default=5, ge=1)

    with pytest.raises(ValidationError):
        Sample.model_validate({"window": 5, "extra": True})
