"""Market calendar port for session eligibility (P03-T10).

P04-T3 later provides a pandas-market-calendars implementation. Risk depends
only on this protocol and may use :class:`FakeMarketCalendar` in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import StrEnum
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from ainvest.schemas.common import ensure_utc

_ET = ZoneInfo("America/New_York")


class SessionStatus(StrEnum):
    """Regular-session classification for a moment in time."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    HOLIDAY = "HOLIDAY"
    EARLY_CLOSED = "EARLY_CLOSED"
    UNKNOWN = "UNKNOWN"


@runtime_checkable
class MarketCalendar(Protocol):
    """Minimal calendar port for first-release regular-session gating."""

    def session_status(
        self,
        moment: datetime,
        *,
        exchange: str = "XNYS",
    ) -> SessionStatus:
        """Classify ``moment`` for ``exchange`` (MIC or venue code)."""
        ...

    def is_regular_session_open(
        self,
        moment: datetime,
        *,
        exchange: str = "XNYS",
    ) -> bool:
        """True only when the regular session is unambiguously open."""
        ...


@dataclass(frozen=True)
class FakeMarketCalendar:
    """Deterministic calendar for unit tests (no network, no pandas).

    Regular session defaults to 09:30-16:00 America/New_York on weekdays that
    are not listed as holidays. Early-close dates end at ``early_close_time``.
    Unknown exchanges fail closed as :attr:`SessionStatus.UNKNOWN`.
    """

    holidays: frozenset[date] = field(default_factory=frozenset)
    early_close_dates: frozenset[date] = field(default_factory=frozenset)
    regular_open: time = time(9, 30)
    regular_close: time = time(16, 0)
    early_close_time: time = time(13, 0)
    supported_exchanges: frozenset[str] = field(
        default_factory=lambda: frozenset({"XNYS", "XNAS", "ARCX"})
    )

    def session_status(
        self,
        moment: datetime,
        *,
        exchange: str = "XNYS",
    ) -> SessionStatus:
        if exchange not in self.supported_exchanges:
            return SessionStatus.UNKNOWN
        local = ensure_utc(moment).astimezone(_ET)
        day = local.date()
        if day.weekday() >= 5:
            return SessionStatus.CLOSED
        if day in self.holidays:
            return SessionStatus.HOLIDAY
        open_t = self.regular_open
        if day in self.early_close_dates:
            close_t = self.early_close_time
            if local.timetz().replace(tzinfo=None) >= close_t:
                return SessionStatus.EARLY_CLOSED
        else:
            close_t = self.regular_close
        clock = local.timetz().replace(tzinfo=None)
        if open_t <= clock < close_t:
            return SessionStatus.OPEN
        return SessionStatus.CLOSED

    def is_regular_session_open(
        self,
        moment: datetime,
        *,
        exchange: str = "XNYS",
    ) -> bool:
        return self.session_status(moment, exchange=exchange) is SessionStatus.OPEN


__all__ = [
    "FakeMarketCalendar",
    "MarketCalendar",
    "SessionStatus",
]
