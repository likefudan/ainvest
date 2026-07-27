"""Deterministic Paper Broker and fill simulator (P03-T14).

No real money. Fills occur only from **injected** market events. Clocks and
randomness are caller-injected (never ``datetime.now`` / global ``random``).
Fees, half-spread, and slippage are explicit cost-model inputs — never assumed
zero by omission.

Implements :class:`~ainvest.execution.broker.BrokerReadPort` and
:class:`~ainvest.execution.broker.BrokerWritePort` for ``account_scope=paper``.
"""

from ainvest.execution.paper.broker import PaperBroker, as_read_port, as_write_port
from ainvest.execution.paper.types import (
    PaperClock,
    PaperCostModel,
    PaperMarketEvent,
    PaperRejectReason,
)

__all__ = [
    "PaperBroker",
    "PaperClock",
    "PaperCostModel",
    "PaperMarketEvent",
    "PaperRejectReason",
    "as_read_port",
    "as_write_port",
]
