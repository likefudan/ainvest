"""Market data, news, and fundamentals adapters.

Read-only gateways produce versioned schemas for research and strategy.
Must not import consumer packages (``agents``, ``strategies``, ``risk``,
``approval``, or ``execution``).
"""

from ainvest.data.calendar_port import FakeMarketCalendar, MarketCalendar, SessionStatus

__all__ = [
    "FakeMarketCalendar",
    "MarketCalendar",
    "SessionStatus",
]
