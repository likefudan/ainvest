"""Integration: registry discovers the installed reference MA plugin (P03-T3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ainvest.config import TradingMode
from ainvest.strategies import (
    RegistryLoadConfig,
    StrategyRegistry,
    load_and_bind_strategy_instances,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_YAML = REPO_ROOT / "config" / "strategies.example.yaml"


@pytest.mark.integration
def test_registry_discovers_moving_average_entry_point() -> None:
    registry = StrategyRegistry.load(load_entry_points=True)
    definition = registry.get("moving_average")
    assert definition.metadata.plugin_id == "moving_average"
    assert definition.metadata.plugin_version == "1.0.0"
    assert definition.version == "1.0.0"
    strategy = definition.create()
    assert strategy.name == "moving_average"


@pytest.mark.integration
def test_example_yaml_binds_to_installed_plugin() -> None:
    registry = StrategyRegistry.load(
        RegistryLoadConfig(
            trading_mode=TradingMode.PAPER,
            allowlist={"moving_average": "1.0.0"},
        ),
        load_entry_points=True,
    )
    bound = load_and_bind_strategy_instances(EXAMPLE_YAML, registry)
    assert len(bound) == 1
    assert bound[0].definition.name == "moving_average"
    assert bound[0].enabled is False
    assert bound[0].params.model_dump()["fast_window"] == 20
