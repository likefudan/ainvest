"""Reference moving-average crossover strategy for ainvest (P03-T3).

This package proves the Strategy API: parameters, metadata, entry points, and
deterministic evaluation with no network, broker, or system-clock access.
"""

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
