"""Discover, validate, and expose strategy plugins via pluggy (P03-T1)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import pluggy

from ainvest.config import TradingMode
from ainvest.strategies.definitions import (
    PluginMetadata,
    StrategyDefinition,
    StrategyError,
)
from ainvest.strategies.hooks import ENTRY_POINT_GROUP, HOOK_NAMESPACE, StrategyHookSpec


@dataclass(frozen=True, slots=True)
class RegistryLoadConfig:
    """Controls which installed plugins may be loaded.

    Live mode requires a non-empty allowlist of ``plugin_id -> pinned version``.
    Paper/research may omit the allowlist; when present it still restricts load.

    Entry-point names must match ``plugin_id`` values used in ``allowlist`` /
    ``disabled_plugins`` so non-selected packages are never imported.
    """

    trading_mode: TradingMode = TradingMode.PAPER
    allowlist: Mapping[str, str] | None = None
    disabled_plugins: frozenset[str] = field(default_factory=frozenset)
    entry_point_group: str = ENTRY_POINT_GROUP


class StrategyRegistry:
    """Facade over pluggy discovery with fail-closed conflict and API checks."""

    def __init__(self) -> None:
        self._definitions_by_name: dict[str, StrategyDefinition] = {}
        self._plugins_by_id: dict[str, PluginMetadata] = {}
        self._entry_points: dict[str, str] = {}
        self._frozen: bool = False

    @classmethod
    def load(
        cls,
        config: RegistryLoadConfig | None = None,
        *,
        plugins: Sequence[Any] | None = None,
        load_entry_points: bool = True,
    ) -> StrategyRegistry:
        """Discover plugins, validate definitions, and return an immutable registry."""
        cfg = config or RegistryLoadConfig()
        cls._validate_load_config(cfg)

        registry = cls()
        manager = pluggy.PluginManager(HOOK_NAMESPACE)
        manager.add_hookspecs(StrategyHookSpec)

        if load_entry_points:
            registry._load_entry_points(manager, cfg)
        if plugins:
            for plugin in plugins:
                registry._register_plugin_object(manager, plugin, entry_point_name=None, config=cfg)

        results = manager.hook.strategy_definitions()
        for definitions in results:
            if definitions is None:
                continue
            if not isinstance(definitions, list):
                raise StrategyError(
                    "strategy_definitions() must return a list",
                    code="STRATEGY_HOOK_INVALID",
                )
            for definition in definitions:
                registry._add_definition(definition, config=cfg)

        registry._assert_allowlist_satisfied(cfg)
        registry._frozen = True
        return registry

    def list(self) -> tuple[StrategyDefinition, ...]:
        """Return immutable validated definitions sorted by strategy name."""
        return tuple(self._definitions_by_name[name] for name in sorted(self._definitions_by_name))

    def get(self, name: str) -> StrategyDefinition:
        """Return one validated definition or fail closed."""
        try:
            return self._definitions_by_name[name]
        except KeyError as exc:
            raise StrategyError(
                f"unknown strategy: {name!r}",
                code="STRATEGY_UNKNOWN",
            ) from exc

    def get_plugin(self, plugin_id: str) -> PluginMetadata:
        """Return validated plugin metadata or fail closed."""
        try:
            return self._plugins_by_id[plugin_id]
        except KeyError as exc:
            raise StrategyError(
                f"unknown plugin: {plugin_id!r}",
                code="STRATEGY_UNKNOWN_PLUGIN",
            ) from exc

    def as_mapping(self) -> Mapping[str, StrategyDefinition]:
        """Read-only view of definitions keyed by strategy name."""
        return MappingProxyType(self._definitions_by_name)

    def plugin_ids(self) -> frozenset[str]:
        return frozenset(self._plugins_by_id)

    def _assert_allowlist_satisfied(self, config: RegistryLoadConfig) -> None:
        if config.allowlist is None:
            return
        missing = [
            plugin_id
            for plugin_id in config.allowlist
            if plugin_id not in self._plugins_by_id and plugin_id not in config.disabled_plugins
        ]
        if missing:
            raise StrategyError(
                f"allowlisted plugins not discovered: {sorted(missing)!r}",
                code="STRATEGY_ALLOWLIST_MISSING",
            )

    @staticmethod
    def _validate_load_config(config: RegistryLoadConfig) -> None:
        if config.trading_mode is TradingMode.LIVE:
            if not config.allowlist:
                raise StrategyError(
                    "live mode requires a non-empty plugin allowlist with pinned versions",
                    code="STRATEGY_LIVE_ALLOWLIST_REQUIRED",
                )
            for plugin_id, version in config.allowlist.items():
                if not plugin_id or not str(plugin_id).strip():
                    raise StrategyError(
                        "live allowlist contains an empty plugin_id",
                        code="STRATEGY_LIVE_ALLOWLIST_INVALID",
                    )
                if not version or not str(version).strip():
                    raise StrategyError(
                        f"live allowlist requires a pinned version for {plugin_id!r}",
                        code="STRATEGY_LIVE_ALLOWLIST_UNPINNED",
                    )

    def _load_entry_points(self, manager: pluggy.PluginManager, config: RegistryLoadConfig) -> None:
        import importlib.metadata

        for dist in importlib.metadata.distributions():
            for ep in dist.entry_points:
                if ep.group != config.entry_point_group:
                    continue
                # Entry-point name must match plugin_id for allowlist/disabled
                # pre-filters so non-selected packages are never imported.
                if ep.name in config.disabled_plugins:
                    continue
                if config.allowlist is not None and ep.name not in config.allowlist:
                    continue
                if ep.name in self._entry_points:
                    raise StrategyError(
                        f"duplicate strategy entry point: {ep.name!r}",
                        code="STRATEGY_ENTRY_POINT_CONFLICT",
                    )
                self._entry_points[ep.name] = f"{dist.metadata['Name']}=={dist.version}"
                if manager.get_plugin(ep.name) is not None or manager.is_blocked(ep.name):
                    raise StrategyError(
                        f"duplicate strategy entry point registration: {ep.name!r}",
                        code="STRATEGY_ENTRY_POINT_CONFLICT",
                    )
                plugin = ep.load()
                self._register_plugin_object(
                    manager,
                    plugin,
                    entry_point_name=ep.name,
                    config=config,
                )

    def _register_plugin_object(
        self,
        manager: pluggy.PluginManager,
        plugin: Any,
        *,
        entry_point_name: str | None,
        config: RegistryLoadConfig,
    ) -> None:
        metadata = self._extract_metadata(plugin)
        if metadata.plugin_id in config.disabled_plugins:
            return
        if config.allowlist is not None:
            pinned = config.allowlist.get(metadata.plugin_id)
            if pinned is None:
                return
            if pinned != metadata.plugin_version:
                raise StrategyError(
                    (
                        f"plugin {metadata.plugin_id!r} version {metadata.plugin_version!r} "
                        f"does not match pinned allowlist version {pinned!r}"
                    ),
                    code="STRATEGY_VERSION_MISMATCH",
                )
        elif config.trading_mode is TradingMode.LIVE:
            raise StrategyError(
                "live mode requires a non-empty plugin allowlist with pinned versions",
                code="STRATEGY_LIVE_ALLOWLIST_REQUIRED",
            )

        if metadata.plugin_id in self._plugins_by_id:
            raise StrategyError(
                f"duplicate plugin_id: {metadata.plugin_id!r}",
                code="STRATEGY_PLUGIN_CONFLICT",
            )
        metadata.validate()
        self._plugins_by_id[metadata.plugin_id] = metadata

        name = entry_point_name or metadata.plugin_id
        if manager.get_plugin(name) is not None:
            raise StrategyError(
                f"duplicate plugin registration name: {name!r}",
                code="STRATEGY_ENTRY_POINT_CONFLICT",
            )
        manager.register(plugin, name=name)

    def _add_definition(
        self,
        definition: StrategyDefinition,
        *,
        config: RegistryLoadConfig,
    ) -> None:
        if not isinstance(definition, StrategyDefinition):
            raise StrategyError(
                f"invalid StrategyDefinition type: {type(definition)!r}",
                code="STRATEGY_HOOK_INVALID",
            )
        definition.metadata.validate()
        if definition.metadata.plugin_id in config.disabled_plugins:
            return
        if definition.metadata.plugin_id not in self._plugins_by_id:
            raise StrategyError(
                (
                    f"strategy {definition.name!r} references unregistered plugin "
                    f"{definition.metadata.plugin_id!r}"
                ),
                code="STRATEGY_PLUGIN_UNREGISTERED",
            )
        registered = self._plugins_by_id[definition.metadata.plugin_id]
        if definition.metadata != registered:
            raise StrategyError(
                f"strategy {definition.name!r} metadata does not match registered plugin",
                code="STRATEGY_METADATA_MISMATCH",
            )
        if definition.name in self._definitions_by_name:
            raise StrategyError(
                f"duplicate strategy name: {definition.name!r}",
                code="STRATEGY_NAME_CONFLICT",
            )
        if self._frozen:
            raise StrategyError(
                "cannot mutate a frozen StrategyRegistry",
                code="STRATEGY_REGISTRY_FROZEN",
            )
        self._definitions_by_name[definition.name] = definition

    @staticmethod
    def _extract_metadata(plugin: Any) -> PluginMetadata:
        raw = getattr(plugin, "metadata", None)
        if raw is None:
            raise StrategyError(
                f"plugin {type(plugin).__name__!r} missing metadata",
                code="STRATEGY_METADATA_MISSING",
            )
        if isinstance(raw, PluginMetadata):
            return raw
        if isinstance(raw, Mapping):
            try:
                return PluginMetadata(**dict(raw))
            except TypeError as exc:
                raise StrategyError(
                    f"plugin {type(plugin).__name__!r} has invalid metadata mapping",
                    code="STRATEGY_METADATA_INVALID",
                ) from exc
        raise StrategyError(
            f"plugin {type(plugin).__name__!r} metadata must be PluginMetadata",
            code="STRATEGY_METADATA_INVALID",
        )


def load_strategy_registry(
    config: RegistryLoadConfig | None = None,
    *,
    plugins: Iterable[Any] | None = None,
    load_entry_points: bool = True,
) -> StrategyRegistry:
    """Convenience wrapper around :meth:`StrategyRegistry.load`."""
    return StrategyRegistry.load(
        config,
        plugins=tuple(plugins) if plugins is not None else None,
        load_entry_points=load_entry_points,
    )


__all__ = [
    "RegistryLoadConfig",
    "StrategyRegistry",
    "load_strategy_registry",
]
