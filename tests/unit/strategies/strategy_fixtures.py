"""Shared helpers for strategy unit tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from ainvest.schemas.examples import strategy_context_example
from ainvest.schemas.strategy import StrategyContext, parse_strategy_context
from ainvest.strategies import (
    PluginMetadata,
    StrategyDefinition,
    StrategyDiagnostics,
    StrategyParams,
    StrategyResult,
    hookimpl,
)


def make_metadata(**overrides: Any) -> PluginMetadata:
    base = {
        "plugin_id": "demo_plugin",
        "plugin_version": "1.0.0",
        "ainvest_strategy_api": ">=1.0.0,<2.0.0",
        "source_commit": "local",
        "owner": "ainvest",
        "repository": "tests/demo",
    }
    base.update(overrides)
    return PluginMetadata(**base)


class DemoParams(StrategyParams):
    alpha: int = Field(default=1, ge=1)


class DemoStrategy:
    name: ClassVar[str] = "demo_strategy"
    version: ClassVar[str] = "1.0.0"
    params_model: ClassVar[type[BaseModel]] = DemoParams

    def __init__(self, params: DemoParams) -> None:
        self.params = params

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        del context
        return StrategyResult(diagnostics=StrategyDiagnostics(notes=("ok",)))


class DemoPlugin:
    def __init__(self, metadata: PluginMetadata | None = None) -> None:
        self.metadata = metadata or make_metadata()

    @hookimpl
    def strategy_definitions(self) -> list[StrategyDefinition]:
        return [StrategyDefinition.from_type(DemoStrategy, metadata=self.metadata)]


def context_payload(*, sma_20: str = "211.30", sma_50: str = "204.80") -> dict[str, Any]:
    payload = deepcopy(strategy_context_example())
    payload["strategy_state"] = {
        "strategy": "moving_average",
        "strategy_version": "1.0.0",
        "updated_at": "2026-07-24T18:00:00Z",
        "entries": [
            {
                "key": "fast_above_slow",
                "kind": "BOOLEAN",
                "boolean_value": False,
            }
        ],
    }
    payload["research"]["technical"]["sma_20"] = sma_20
    payload["research"]["technical"]["sma_50"] = sma_50
    return payload


def make_context(**kwargs: Any) -> StrategyContext:
    return parse_strategy_context(context_payload(**kwargs))


__all__ = [
    "DemoParams",
    "DemoPlugin",
    "DemoStrategy",
    "context_payload",
    "make_context",
    "make_metadata",
]
