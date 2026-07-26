"""pluggy plugin entry for the reference moving-average strategy."""

from __future__ import annotations

from ainvest.strategies import PluginMetadata, StrategyDefinition, hookimpl
from ainvest.strategies.reference.moving_average.strategy import MovingAverageStrategy

METADATA = PluginMetadata(
    plugin_id="moving_average",
    plugin_version="1.0.0",
    ainvest_strategy_api=">=1.0.0,<2.0.0",
    source_commit="local",
    owner="ainvest",
    repository="src/ainvest/strategies/reference/moving_average",
)


class MovingAveragePlugin:
    """Declares strategy definitions only; never evaluates on import."""

    metadata = METADATA

    @hookimpl
    def strategy_definitions(self) -> list[StrategyDefinition]:
        return [StrategyDefinition.from_type(MovingAverageStrategy, metadata=METADATA)]


plugin = MovingAveragePlugin()
