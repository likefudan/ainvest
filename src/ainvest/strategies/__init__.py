"""Strategy protocol, pluggy hooks, registry, and worker isolation.

Strategies may produce ``TradeSignal`` values only. They must not import
``ainvest.execution`` or ``ainvest.approval``, hold broker credentials, or
call a broker.
"""

__all__: list[str] = []
