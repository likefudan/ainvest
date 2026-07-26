"""Strategy protocol, pluggy hooks, registry, and worker isolation.

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

__all__ = [
    "STRATEGY_API_VERSION",
    "StrategyApiRange",
    "assert_strategy_api_compatible",
    "parse_strategy_api_range",
    "strategy_api_range_contains",
]
