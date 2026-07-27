"""Unit tests for strategy instance YAML configuration (P03-T2)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
import yaml

from ainvest.config import TradingMode
from ainvest.strategies import (
    StrategyError,
    StrategyRegistry,
    auditable_instance_dict,
    bind_strategy_instances,
    format_duration,
    load_and_bind_strategy_instances,
    load_strategy_instances_document,
    parse_duration,
)
from ainvest.strategies.reference.moving_average.strategy import MovingAverageParams
from strategies.strategy_fixtures import DemoPlugin, make_metadata

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_YAML = REPO_ROOT / "config" / "strategies.example.yaml"


@pytest.mark.unit
def test_parse_and_format_duration() -> None:
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("1h") == timedelta(hours=1)
    assert format_duration(timedelta(minutes=30)) == "30m"
    with pytest.raises(StrategyError, match="invalid duration"):
        parse_duration("30minutes")


@pytest.mark.unit
def test_design_example_yaml_loads(tmp_path: Path) -> None:
    # Example references moving_average; bind with a matching demo-shaped plugin.
    document = load_strategy_instances_document(EXAMPLE_YAML)
    assert document.schema_version == "1"
    assert len(document.strategies) == 1
    instance = document.strategies[0]
    assert instance.id == "aapl_sma_daily"
    assert instance.enabled is False
    assert instance.universe.symbols == ("AAPL", "MSFT")
    assert instance.constraints.research_max_age == timedelta(minutes=30)
    assert instance.constraints.signal_ttl == timedelta(minutes=30)


@pytest.mark.unit
def test_bind_validates_params_via_definition(tmp_path: Path) -> None:
    yaml_path = tmp_path / "strategies.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "strategies": [
                    {
                        "id": "demo_one",
                        "plugin": "demo_plugin",
                        "enabled": True,
                        "universe": {"symbols": ["AAPL"], "timeframe": "1d"},
                        "parameters": {"alpha": 3},
                        "schedule": {"run_at": "market_close_minus_15m"},
                        "constraints": {
                            "research_max_age": "30m",
                            "signal_ttl": "15m",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = StrategyRegistry.load(plugins=[DemoPlugin()], load_entry_points=False)
    bound = load_and_bind_strategy_instances(yaml_path, registry)
    assert len(bound) == 1
    assert bound[0].params.model_dump() == {"alpha": 3}
    audit = auditable_instance_dict(bound[0])
    assert audit["parameters"] == {"alpha": 3}
    assert "secret" not in str(audit).lower()


@pytest.mark.unit
def test_rejects_unknown_parameters(tmp_path: Path) -> None:
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "strategies": [
                    {
                        "id": "demo_one",
                        "plugin": "demo_plugin",
                        "universe": {"symbols": ["AAPL"], "timeframe": "1d"},
                        "parameters": {"alpha": 1, "nope": True},
                        "schedule": {"run_at": "market_close_minus_15m"},
                        "constraints": {
                            "research_max_age": "30m",
                            "signal_ttl": "30m",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = StrategyRegistry.load(plugins=[DemoPlugin()], load_entry_points=False)
    with pytest.raises(StrategyError, match="invalid parameters"):
        load_and_bind_strategy_instances(yaml_path, registry)


@pytest.mark.unit
def test_rejects_duplicate_instance_ids(tmp_path: Path) -> None:
    yaml_path = tmp_path / "dup.yaml"
    entry = {
        "id": "demo_one",
        "plugin": "demo_plugin",
        "universe": {"symbols": ["AAPL"], "timeframe": "1d"},
        "parameters": {"alpha": 1},
        "schedule": {"run_at": "market_close_minus_15m"},
        "constraints": {"research_max_age": "30m", "signal_ttl": "30m"},
    }
    yaml_path.write_text(
        yaml.safe_dump({"schema_version": "1", "strategies": [entry, dict(entry)]}),
        encoding="utf-8",
    )
    with pytest.raises(StrategyError, match="invalid strategy instance"):
        load_strategy_instances_document(yaml_path)


@pytest.mark.unit
def test_rejects_executable_yaml(tmp_path: Path) -> None:
    yaml_path = tmp_path / "exec.yaml"
    yaml_path.write_text(
        "schema_version: '1'\nstrategies:\n  - id: demo_one\n"
        "    plugin: demo_plugin\n    universe:\n      symbols: [AAPL]\n"
        "      timeframe: 1d\n    parameters:\n      alpha: eval('2')\n"
        "    schedule:\n      run_at: market_close_minus_15m\n"
        "    constraints:\n      research_max_age: 30m\n      signal_ttl: 30m\n",
        encoding="utf-8",
    )
    with pytest.raises(StrategyError, match=r"Executable expression|CONFIG_YAML"):
        load_strategy_instances_document(yaml_path)


@pytest.mark.unit
def test_live_requires_pinned_plugin_version(tmp_path: Path) -> None:
    yaml_path = tmp_path / "live.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "strategies": [
                    {
                        "id": "demo_one",
                        "plugin": "demo_plugin",
                        "universe": {"symbols": ["AAPL"], "timeframe": "1d"},
                        "parameters": {"alpha": 1},
                        "schedule": {"run_at": "market_close_minus_15m"},
                        "constraints": {
                            "research_max_age": "30m",
                            "signal_ttl": "30m",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = StrategyRegistry.load(plugins=[DemoPlugin()], load_entry_points=False)
    document = load_strategy_instances_document(yaml_path)
    with pytest.raises(StrategyError, match="pinned plugin_version"):
        bind_strategy_instances(document, registry, trading_mode=TradingMode.LIVE)


@pytest.mark.unit
def test_live_pin_mismatch_fails(tmp_path: Path) -> None:
    yaml_path = tmp_path / "live.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "strategies": [
                    {
                        "id": "demo_one",
                        "plugin": "demo_plugin",
                        "plugin_version": "9.0.0",
                        "universe": {"symbols": ["AAPL"], "timeframe": "1d"},
                        "parameters": {"alpha": 1},
                        "schedule": {"run_at": "market_close_minus_15m"},
                        "constraints": {
                            "research_max_age": "30m",
                            "signal_ttl": "30m",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = StrategyRegistry.load(
        plugins=[DemoPlugin(make_metadata(plugin_version="1.0.0"))],
        load_entry_points=False,
    )
    with pytest.raises(StrategyError, match="does not match discovered"):
        load_and_bind_strategy_instances(yaml_path, registry, trading_mode=TradingMode.LIVE)


@pytest.mark.unit
def test_bind_injects_constraint_signal_ttl_into_ma_params(tmp_path: Path) -> None:
    yaml_path = tmp_path / "ma.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "strategies": [
                    {
                        "id": "aapl_sma_daily",
                        "plugin": "moving_average",
                        "strategy": "moving_average",
                        "enabled": False,
                        "universe": {"symbols": ["AAPL"], "timeframe": "1d"},
                        "parameters": {
                            "fast_window": 20,
                            "slow_window": 50,
                            "target_weight": "0.10",
                        },
                        "schedule": {"run_at": "market_close_minus_15m"},
                        "constraints": {
                            "research_max_age": "30m",
                            "signal_ttl": "15m",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = StrategyRegistry.load(load_entry_points=True)
    bound = load_and_bind_strategy_instances(yaml_path, registry)
    params = cast(MovingAverageParams, bound[0].params)
    assert params.signal_ttl == timedelta(minutes=15)


@pytest.mark.unit
def test_bind_rejects_signal_ttl_mismatch(tmp_path: Path) -> None:
    yaml_path = tmp_path / "ma.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "strategies": [
                    {
                        "id": "aapl_sma_daily",
                        "plugin": "moving_average",
                        "strategy": "moving_average",
                        "universe": {"symbols": ["AAPL"], "timeframe": "1d"},
                        "parameters": {
                            "fast_window": 20,
                            "slow_window": 50,
                            "target_weight": "0.10",
                            "signal_ttl": "45m",
                        },
                        "schedule": {"run_at": "market_close_minus_15m"},
                        "constraints": {
                            "research_max_age": "30m",
                            "signal_ttl": "15m",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = StrategyRegistry.load(load_entry_points=True)
    with pytest.raises(StrategyError, match="must match"):
        load_and_bind_strategy_instances(yaml_path, registry)
