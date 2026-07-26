"""Unit tests for StrategyRegistry plugin loading (P03-T1)."""

from __future__ import annotations

from typing import ClassVar

import pytest
from strategy_fixtures import DemoPlugin, DemoStrategy, make_metadata

from ainvest.config import TradingMode
from ainvest.strategies import (
    RegistryLoadConfig,
    StrategyDefinition,
    StrategyError,
    StrategyRegistry,
    hookimpl,
)


class SecondStrategy(DemoStrategy):
    name: ClassVar[str] = "second_strategy"


class DuplicateNamePlugin:
    metadata = make_metadata(plugin_id="other_plugin")

    @hookimpl
    def strategy_definitions(self) -> list[StrategyDefinition]:
        return [StrategyDefinition.from_type(DemoStrategy, metadata=self.metadata)]


@pytest.mark.unit
def test_registry_loads_multiple_plugins() -> None:
    first = DemoPlugin(make_metadata(plugin_id="plugin_a"))
    second_meta = make_metadata(plugin_id="plugin_b")

    class PluginB:
        metadata = second_meta

        @hookimpl
        def strategy_definitions(self) -> list[StrategyDefinition]:
            return [StrategyDefinition.from_type(SecondStrategy, metadata=second_meta)]

    registry = StrategyRegistry.load(
        plugins=[first, PluginB()],
        load_entry_points=False,
    )
    names = [item.name for item in registry.list()]
    assert names == ["demo_strategy", "second_strategy"]
    assert registry.get("demo_strategy").metadata.plugin_id == "plugin_a"
    view = registry.as_mapping()
    with pytest.raises(TypeError):
        view["x"] = registry.get("demo_strategy")  # type: ignore[index]


@pytest.mark.unit
def test_duplicate_plugin_id_fails() -> None:
    with pytest.raises(StrategyError, match="duplicate plugin_id"):
        StrategyRegistry.load(
            plugins=[DemoPlugin(), DemoPlugin()],
            load_entry_points=False,
        )


@pytest.mark.unit
def test_duplicate_strategy_name_fails() -> None:
    with pytest.raises(StrategyError, match="duplicate strategy name"):
        StrategyRegistry.load(
            plugins=[DemoPlugin(), DuplicateNamePlugin()],
            load_entry_points=False,
        )


@pytest.mark.unit
def test_incompatible_api_rejected() -> None:
    bad = DemoPlugin(make_metadata(ainvest_strategy_api=">=9.0.0,<10.0.0"))
    with pytest.raises(StrategyError, match="incompatible"):
        StrategyRegistry.load(plugins=[bad], load_entry_points=False)


@pytest.mark.unit
def test_disabled_plugin_skipped() -> None:
    registry = StrategyRegistry.load(
        RegistryLoadConfig(disabled_plugins=frozenset({"demo_plugin"})),
        plugins=[DemoPlugin()],
        load_entry_points=False,
    )
    assert registry.list() == ()


@pytest.mark.unit
def test_allowlist_skips_entry_point_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-allowlisted entry points must not be imported (ep.load never called)."""
    loads: list[str] = []

    class FakeEp:
        def __init__(self, name: str) -> None:
            self.name = name
            self.group = "ainvest.strategies"

        def load(self) -> object:
            loads.append(self.name)
            raise AssertionError(f"unexpected load of {self.name}")

    class FakeDist:
        def __init__(self) -> None:
            self.metadata = {"Name": "fake"}
            self.version = "0.0.1"
            self.entry_points = [FakeEp("other_plugin"), FakeEp("keep_me")]

    import importlib.metadata

    monkeypatch.setattr(
        importlib.metadata,
        "distributions",
        lambda: [FakeDist()],
    )

    keep = DemoPlugin(make_metadata(plugin_id="keep_me", plugin_version="1.2.3"))

    class KeepStrategy(DemoStrategy):
        name: ClassVar[str] = "keep_strategy"

    class KeepPlugin:
        metadata = keep.metadata

        @hookimpl
        def strategy_definitions(self) -> list[StrategyDefinition]:
            return [StrategyDefinition.from_type(KeepStrategy, metadata=keep.metadata)]

    def load_keep(self: FakeEp) -> object:
        loads.append(self.name)
        if self.name != "keep_me":
            raise AssertionError(f"non-allowlisted entry point loaded: {self.name}")
        return KeepPlugin()

    monkeypatch.setattr(FakeEp, "load", load_keep)

    registry = StrategyRegistry.load(
        RegistryLoadConfig(allowlist={"keep_me": "1.2.3"}),
        load_entry_points=True,
    )
    assert loads == ["keep_me"]
    assert [item.name for item in registry.list()] == ["keep_strategy"]


@pytest.mark.unit
def test_allowlist_filters_and_pins() -> None:
    keep = DemoPlugin(make_metadata(plugin_id="keep_me", plugin_version="1.2.3"))
    drop = DemoPlugin(make_metadata(plugin_id="drop_me", plugin_version="1.0.0"))

    class KeepStrategy(DemoStrategy):
        name: ClassVar[str] = "keep_strategy"

    class KeepPlugin:
        metadata = keep.metadata

        @hookimpl
        def strategy_definitions(self) -> list[StrategyDefinition]:
            return [StrategyDefinition.from_type(KeepStrategy, metadata=keep.metadata)]

    registry = StrategyRegistry.load(
        RegistryLoadConfig(allowlist={"keep_me": "1.2.3"}),
        plugins=[KeepPlugin(), drop],
        load_entry_points=False,
    )
    assert [item.name for item in registry.list()] == ["keep_strategy"]


@pytest.mark.unit
def test_allowlist_version_mismatch_fails() -> None:
    plugin = DemoPlugin(make_metadata(plugin_version="1.0.0"))
    with pytest.raises(StrategyError, match="pinned allowlist version"):
        StrategyRegistry.load(
            RegistryLoadConfig(allowlist={"demo_plugin": "9.9.9"}),
            plugins=[plugin],
            load_entry_points=False,
        )


@pytest.mark.unit
def test_live_requires_allowlist() -> None:
    with pytest.raises(StrategyError, match="live mode requires"):
        StrategyRegistry.load(
            RegistryLoadConfig(trading_mode=TradingMode.LIVE),
            plugins=[DemoPlugin()],
            load_entry_points=False,
        )


@pytest.mark.unit
def test_live_allowlist_missing_plugin_fails() -> None:
    with pytest.raises(StrategyError, match="allowlisted plugins not discovered"):
        StrategyRegistry.load(
            RegistryLoadConfig(
                trading_mode=TradingMode.LIVE,
                allowlist={"missing_plugin": "1.0.0"},
            ),
            plugins=[DemoPlugin()],
            load_entry_points=False,
        )


@pytest.mark.unit
def test_unknown_strategy_fails_closed() -> None:
    registry = StrategyRegistry.load(plugins=[DemoPlugin()], load_entry_points=False)
    with pytest.raises(StrategyError, match="unknown strategy"):
        registry.get("nope")


@pytest.mark.unit
def test_missing_plugin_metadata_fails() -> None:
    class Bare:
        @hookimpl
        def strategy_definitions(self) -> list[StrategyDefinition]:
            return []

    with pytest.raises(StrategyError, match="missing metadata"):
        StrategyRegistry.load(plugins=[Bare()], load_entry_points=False)
