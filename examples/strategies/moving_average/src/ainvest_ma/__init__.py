"""Sample packaging surface for the reference moving-average strategy."""

from ainvest.strategies.reference.moving_average.plugin import METADATA, plugin
from ainvest.strategies.reference.moving_average.strategy import (
    MovingAverageParams,
    MovingAverageStrategy,
)

__all__ = [
    "METADATA",
    "MovingAverageParams",
    "MovingAverageStrategy",
    "plugin",
]
