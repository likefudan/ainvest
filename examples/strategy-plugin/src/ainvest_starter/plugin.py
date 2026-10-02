"""Declare the plugin; do not evaluate or access external resources on import."""

from ainvest.strategies import PluginMetadata, StrategyDefinition, hookimpl
from ainvest_starter.strategy import StarterHold

METADATA = PluginMetadata(
    plugin_id="starter_hold",
    plugin_version="0.1.0",
    ainvest_strategy_api=">=1.0.0,<2.0.0",
    source_commit="local",
    owner="example_team",
    repository="example.invalid/strategy-starter",
)


class StarterPlugin:
    """Declarations only; review and replace example provenance before release."""

    metadata = METADATA

    @hookimpl
    def strategy_definitions(self) -> list[StrategyDefinition]:
        return [StrategyDefinition.from_type(StarterHold, metadata=METADATA)]


plugin = StarterPlugin()
