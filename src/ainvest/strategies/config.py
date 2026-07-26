"""Strategy instance YAML configuration (P03-T2).

Separates strategy *code* definitions (plugins) from runtime *instances*
(universe, parameters, schedule, constraints). YAML must not contain executable
expressions; use :func:`ainvest.config.load_yaml_mapping` for safe loading.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from ainvest.config import ConfigError, TradingMode, load_yaml_mapping
from ainvest.schemas.common import Symbol
from ainvest.strategies.definitions import StrategyDefinition, StrategyError
from ainvest.strategies.registry import StrategyRegistry

_DURATION_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<value>[1-9][0-9]*)(?P<unit>[smhd])$")

InstanceId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$", min_length=2, max_length=64),
]
PluginId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$", min_length=2, max_length=64),
]
Timeframe = Annotated[
    str,
    StringConstraints(pattern=r"^[1-9][0-9]?[mhdw]$", min_length=2, max_length=8),
]
ScheduleName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$", min_length=2, max_length=64),
]


def parse_duration(value: str | timedelta) -> timedelta:
    """Parse a compact duration such as ``30m``, ``1h``, or ``1d``."""
    if isinstance(value, timedelta):
        if value <= timedelta(0):
            raise StrategyError(
                f"duration must be positive, got {value!r}",
                code="STRATEGY_DURATION_INVALID",
            )
        return value
    if not isinstance(value, str):
        raise StrategyError(
            f"duration must be a string like '30m', got {type(value).__name__}",
            code="STRATEGY_DURATION_INVALID",
        )
    match = _DURATION_RE.fullmatch(value.strip())
    if match is None:
        raise StrategyError(
            f"invalid duration: {value!r}",
            code="STRATEGY_DURATION_INVALID",
        )
    amount = int(match.group("value"))
    unit = match.group("unit")
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def format_duration(value: timedelta) -> str:
    """Format a timedelta as a compact auditable duration string."""
    total_seconds = int(value.total_seconds())
    if total_seconds <= 0 or value != timedelta(seconds=total_seconds):
        raise StrategyError(
            f"cannot format non-integral or non-positive duration: {value!r}",
            code="STRATEGY_DURATION_INVALID",
        )
    if total_seconds % 86400 == 0:
        return f"{total_seconds // 86400}d"
    if total_seconds % 3600 == 0:
        return f"{total_seconds // 3600}h"
    if total_seconds % 60 == 0:
        return f"{total_seconds // 60}m"
    return f"{total_seconds}s"


class UniverseConfig(BaseModel):
    """Symbols and bar timeframe for one strategy instance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbols: tuple[Symbol, ...] = Field(min_length=1)
    timeframe: Timeframe

    @field_validator("symbols", mode="before")
    @classmethod
    def _coerce_symbols(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _unique_symbols(self) -> UniverseConfig:
        if len(self.symbols) != len(set(self.symbols)):
            raise ValueError("universe.symbols must be unique")
        return self


class ScheduleConfig(BaseModel):
    """Named schedule trigger for the instance (resolved by the scheduler later)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_at: ScheduleName


class ConstraintsConfig(BaseModel):
    """Runtime constraints applied around strategy evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    research_max_age: timedelta
    signal_ttl: timedelta

    @field_validator("research_max_age", "signal_ttl", mode="before")
    @classmethod
    def _parse_durations(cls, value: object) -> object:
        if isinstance(value, (str, timedelta)):
            return parse_duration(value)
        return value

    @model_validator(mode="after")
    def _positive_constraints(self) -> ConstraintsConfig:
        if self.research_max_age <= timedelta(0):
            raise ValueError("research_max_age must be positive")
        if self.signal_ttl <= timedelta(0):
            raise ValueError("signal_ttl must be positive")
        return self


class StrategyInstanceConfig(BaseModel):
    """One runtime strategy instance from YAML."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: InstanceId
    plugin: PluginId
    enabled: bool = False
    universe: UniverseConfig
    parameters: dict[str, Any] = Field(default_factory=dict)
    schedule: ScheduleConfig
    constraints: ConstraintsConfig
    # Optional strategy name within the plugin; defaults resolved during binding.
    strategy: str | None = None
    # Optional pinned plugin version required in live mode.
    plugin_version: str | None = None

    @field_validator("parameters", mode="before")
    @classmethod
    def _require_mapping(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("parameters must be a mapping")
        return dict(value)


class StrategyInstancesDocument(BaseModel):
    """Root document for ``strategies`` YAML files."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1"
    strategies: tuple[StrategyInstanceConfig, ...] = ()

    @field_validator("strategies", mode="before")
    @classmethod
    def _coerce_strategies(cls, value: object) -> object:
        if value is None:
            return ()
        return value

    @model_validator(mode="after")
    def _unique_instance_ids(self) -> StrategyInstancesDocument:
        ids = [item.id for item in self.strategies]
        if len(ids) != len(set(ids)):
            raise ValueError("strategy instance ids must be unique")
        return self


@dataclass(frozen=True, slots=True)
class BoundStrategyInstance:
    """Instance config validated against a registered strategy definition."""

    config: StrategyInstanceConfig
    definition: StrategyDefinition
    params: BaseModel

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def enabled(self) -> bool:
        return self.config.enabled


def load_strategy_instances_document(path: Path | str) -> StrategyInstancesDocument:
    """Load and structurally validate a strategies YAML file."""
    try:
        raw = load_yaml_mapping(path)
    except ConfigError as exc:
        raise StrategyError(str(exc), code=getattr(exc, "code", "CONFIG_INVALID")) from exc
    try:
        return StrategyInstancesDocument.model_validate(raw)
    except ValidationError as exc:
        raise StrategyError(
            f"invalid strategy instance configuration: {exc.error_count()} error(s)",
            code="STRATEGY_CONFIG_INVALID",
        ) from exc


def bind_strategy_instances(
    document: StrategyInstancesDocument,
    registry: StrategyRegistry,
    *,
    trading_mode: TradingMode = TradingMode.PAPER,
) -> tuple[BoundStrategyInstance, ...]:
    """Bind instance configs to registry definitions and validate parameters.

    Live mode requires each instance to pin ``plugin_version`` and that pin must
    match the discovered plugin metadata version.
    """
    plugin_strategies: dict[str, list[StrategyDefinition]] = {}
    for registered in registry.list():
        plugin_strategies.setdefault(registered.metadata.plugin_id, []).append(registered)

    bound: list[BoundStrategyInstance] = []
    for instance in document.strategies:
        definitions = plugin_strategies.get(instance.plugin)
        if not definitions:
            raise StrategyError(
                f"instance {instance.id!r} references unknown plugin {instance.plugin!r}",
                code="STRATEGY_UNKNOWN_PLUGIN",
            )

        definition: StrategyDefinition
        if instance.strategy is not None:
            matched = next((d for d in definitions if d.name == instance.strategy), None)
            if matched is None:
                raise StrategyError(
                    (
                        f"instance {instance.id!r} references unknown strategy "
                        f"{instance.strategy!r} in plugin {instance.plugin!r}"
                    ),
                    code="STRATEGY_UNKNOWN",
                )
            definition = matched
        elif len(definitions) == 1:
            definition = definitions[0]
        else:
            raise StrategyError(
                (
                    f"instance {instance.id!r} must set strategy= because plugin "
                    f"{instance.plugin!r} provides multiple strategies"
                ),
                code="STRATEGY_AMBIGUOUS",
            )

        if trading_mode is TradingMode.LIVE:
            if not instance.plugin_version:
                raise StrategyError(
                    f"live mode requires pinned plugin_version for instance {instance.id!r}",
                    code="STRATEGY_LIVE_UNPINNED",
                )
            if instance.plugin_version != definition.metadata.plugin_version:
                raise StrategyError(
                    (
                        f"instance {instance.id!r} plugin_version "
                        f"{instance.plugin_version!r} does not match discovered "
                        f"{definition.metadata.plugin_version!r}"
                    ),
                    code="STRATEGY_VERSION_MISMATCH",
                )
        elif instance.plugin_version is not None:
            if instance.plugin_version != definition.metadata.plugin_version:
                raise StrategyError(
                    (
                        f"instance {instance.id!r} plugin_version "
                        f"{instance.plugin_version!r} does not match discovered "
                        f"{definition.metadata.plugin_version!r}"
                    ),
                    code="STRATEGY_VERSION_MISMATCH",
                )

        params = definition.validate_params(instance.parameters)
        bound.append(BoundStrategyInstance(config=instance, definition=definition, params=params))
    return tuple(bound)


def load_and_bind_strategy_instances(
    path: Path | str,
    registry: StrategyRegistry,
    *,
    trading_mode: TradingMode = TradingMode.PAPER,
) -> tuple[BoundStrategyInstance, ...]:
    """Load YAML instances and bind them to a registry in one step."""
    document = load_strategy_instances_document(path)
    return bind_strategy_instances(document, registry, trading_mode=trading_mode)


def auditable_instance_dict(bound: BoundStrategyInstance) -> dict[str, Any]:
    """Return a secret-free normalized mapping suitable for audit logs."""
    cfg = bound.config
    return {
        "id": cfg.id,
        "plugin": cfg.plugin,
        "plugin_version": bound.definition.metadata.plugin_version,
        "strategy": bound.definition.name,
        "strategy_version": bound.definition.version,
        "enabled": cfg.enabled,
        "universe": {
            "symbols": list(cfg.universe.symbols),
            "timeframe": cfg.universe.timeframe,
        },
        "parameters": bound.params.model_dump(mode="json"),
        "schedule": {"run_at": cfg.schedule.run_at},
        "constraints": {
            "research_max_age": format_duration(cfg.constraints.research_max_age),
            "signal_ttl": format_duration(cfg.constraints.signal_ttl),
        },
        "ainvest_strategy_api": bound.definition.metadata.ainvest_strategy_api,
        "source_commit": bound.definition.metadata.source_commit,
    }


__all__ = [
    "BoundStrategyInstance",
    "ConstraintsConfig",
    "ScheduleConfig",
    "StrategyInstanceConfig",
    "StrategyInstancesDocument",
    "UniverseConfig",
    "auditable_instance_dict",
    "bind_strategy_instances",
    "format_duration",
    "load_and_bind_strategy_instances",
    "load_strategy_instances_document",
    "parse_duration",
]
