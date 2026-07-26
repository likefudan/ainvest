"""Strategy protocol, pluggy hooks, registry, and instance configuration.

Strategies may produce ``TradeSignal`` values only. They must not import
``ainvest.execution`` or ``ainvest.approval``, hold broker credentials, or
call a broker.
"""

from ainvest.strategies.api import (
    STRATEGY_API_VERSION,
    StrategyApiRange,
    assert_strategy_api_compatible,
    parse_strategy_api_range,
    strategy_api_range_contains,
)
from ainvest.strategies.config import (
    BoundStrategyInstance,
    ConstraintsConfig,
    ScheduleConfig,
    StrategyInstanceConfig,
    StrategyInstancesDocument,
    UniverseConfig,
    auditable_instance_dict,
    bind_strategy_instances,
    format_duration,
    load_and_bind_strategy_instances,
    load_strategy_instances_document,
    parse_duration,
)
from ainvest.strategies.definitions import (
    PluginMetadata,
    Strategy,
    StrategyDefinition,
    StrategyDiagnostics,
    StrategyError,
    StrategyParams,
    StrategyResult,
)
from ainvest.strategies.hooks import ENTRY_POINT_GROUP, HOOK_NAMESPACE, hookimpl, hookspec
from ainvest.strategies.registry import (
    RegistryLoadConfig,
    StrategyRegistry,
    load_strategy_registry,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "HOOK_NAMESPACE",
    "STRATEGY_API_VERSION",
    "BoundStrategyInstance",
    "ConstraintsConfig",
    "PluginMetadata",
    "RegistryLoadConfig",
    "ScheduleConfig",
    "Strategy",
    "StrategyApiRange",
    "StrategyDefinition",
    "StrategyDiagnostics",
    "StrategyError",
    "StrategyInstanceConfig",
    "StrategyInstancesDocument",
    "StrategyParams",
    "StrategyRegistry",
    "StrategyResult",
    "UniverseConfig",
    "assert_strategy_api_compatible",
    "auditable_instance_dict",
    "bind_strategy_instances",
    "format_duration",
    "hookimpl",
    "hookspec",
    "load_and_bind_strategy_instances",
    "load_strategy_instances_document",
    "load_strategy_registry",
    "parse_duration",
    "parse_strategy_api_range",
    "strategy_api_range_contains",
]
