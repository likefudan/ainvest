"""Reference moving-average crossover strategy for ainvest (P03-T3).

This package proves the Strategy API: parameters, metadata, entry points, and
deterministic evaluation with no network, broker, or system-clock access.
"""

from ainvest_ma.plugin import METADATA, plugin
from ainvest_ma.strategy import MovingAverageParams, MovingAverageStrategy

__all__ = [
    "METADATA",
    "MovingAverageParams",
    "MovingAverageStrategy",
    "plugin",
]
