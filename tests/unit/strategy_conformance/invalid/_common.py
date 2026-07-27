"""Shared helpers for invalid conformance probe strategies."""

from __future__ import annotations

from pydantic import Field

from ainvest.strategies.definitions import PluginMetadata, StrategyDefinition, StrategyParams


class InvalidParams(StrategyParams):
    note: str = Field(default="invalid", min_length=1, max_length=64)


def metadata(plugin_id: str) -> PluginMetadata:
    return PluginMetadata(
        plugin_id=plugin_id,
        plugin_version="1.0.0",
        ainvest_strategy_api=">=1.0.0,<2.0.0",
        source_commit="local",
        owner="ainvest",
        repository="tests/strategy_conformance/invalid",
    )


def definition_for(
    strategy_type: type[object], *, plugin_id: str | None = None
) -> StrategyDefinition:
    name = getattr(strategy_type, "name", None)
    if not isinstance(name, str):
        raise TypeError("invalid strategy type must define a string name")
    return StrategyDefinition.from_type(
        strategy_type,
        metadata=metadata(plugin_id or f"{name}_plugin"),
    )
