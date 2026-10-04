"""Bounded regular-session calendar; no broker permission or halt detection."""

from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache
from importlib import import_module
from zoneinfo import ZoneInfo

from ainvest.data.calendar_port import SessionStatus
from ainvest.schemas.common import ensure_utc

_ET = ZoneInfo("America/New_York")
_VENUES = {"XNYS": "NYSE", "XNAS": "NASDAQ"}


@lru_cache(maxsize=512)
def _session(venue: str, day: date) -> tuple[datetime, datetime] | None:
    library = import_module("pandas_market_calendars")
    schedule = library.get_calendar(venue).schedule(
        start_date=day, end_date=day, interruptions=True
    )
    if schedule.empty:
        return None
    if len(schedule) != 1:
        raise ValueError("ambiguous schedule")
    for column in schedule.columns:
        if str(column).startswith("interruption") and schedule[column].notna().any():
            raise ValueError("interrupted session")
    opening = ensure_utc(schedule.iloc[0]["market_open"].to_pydatetime())
    closing = ensure_utc(schedule.iloc[0]["market_close"].to_pydatetime())
    if not opening < closing:
        raise ValueError("invalid session")
    if any(value.astimezone(_ET).date() != day for value in (opening, closing)):
        raise ValueError("invalid session day")
    if opening.astimezone(_ET).time() < time(9, 30) or closing.astimezone(_ET).time() > time(16):
        raise ValueError("extended hours not supported")
    return opening, closing


@dataclass(frozen=True)
class ExchangeCalendar:
    """Use an explicitly reviewed date horizon; unknown venue/date fails closed.

    The locked library includes known holidays, not real-time exchange halts or
    subsequently announced closures. Runtime composition must review the horizon.
    """

    valid_from: date
    valid_through: date

    def __post_init__(self) -> None:
        if self.valid_from > self.valid_through:
            raise ValueError("invalid calendar horizon")

    def session_status(self, moment: datetime, *, exchange: str = "XNYS") -> SessionStatus:
        try:
            instant = ensure_utc(moment)
            day = instant.astimezone(_ET).date()
            if exchange not in _VENUES or not self.valid_from <= day <= self.valid_through:
                return SessionStatus.UNKNOWN
            session = _session(_VENUES[exchange], day)
            if session is None:
                return SessionStatus.CLOSED if day.weekday() >= 5 else SessionStatus.HOLIDAY
            opening, closing = session
            if opening <= instant < closing:
                return SessionStatus.OPEN
            if instant >= closing and closing.astimezone(_ET).time() < time(16):
                return SessionStatus.EARLY_CLOSED
            return SessionStatus.CLOSED
        except (
            ImportError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            AttributeError,
            RuntimeError,
            OSError,
        ):
            return SessionStatus.UNKNOWN

    def is_regular_session_open(self, moment: datetime, *, exchange: str = "XNYS") -> bool:
        return self.session_status(moment, exchange=exchange) is SessionStatus.OPEN
