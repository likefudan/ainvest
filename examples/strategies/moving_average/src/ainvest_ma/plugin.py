"""Sample third-party entry-point package wrapping the reference MA strategy.

Installed ainvest already registers this strategy. This package documents how
external teams publish under the ``ainvest.strategies`` entry-point group.
"""

from ainvest.strategies.reference.moving_average.plugin import METADATA, plugin

__all__ = ["METADATA", "plugin"]
