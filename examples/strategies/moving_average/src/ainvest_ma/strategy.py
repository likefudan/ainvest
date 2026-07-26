"""Re-export reference strategy symbols for the sample package."""

from ainvest.strategies.reference.moving_average.strategy import (
    MovingAverageParams,
    MovingAverageStrategy,
)

__all__ = ["MovingAverageParams", "MovingAverageStrategy"]
