"""Unit tests for FakeMarketCalendar (P03-T10)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ainvest.data.calendar_port import FakeMarketCalendar, SessionStatus


@pytest.mark.unit
def test_fake_calendar_regular_open_and_closed() -> None:
    cal = FakeMarketCalendar()
    # Wednesday 2026-07-22 15:00 UTC = 11:00 ET → open
    open_moment = datetime(2026, 7, 22, 15, 0, tzinfo=UTC)
    assert cal.session_status(open_moment) is SessionStatus.OPEN
    assert cal.is_regular_session_open(open_moment) is True
    # 20:00 UTC = 16:00 ET → closed at boundary
    closed = datetime(2026, 7, 22, 20, 0, tzinfo=UTC)
    assert cal.session_status(closed) is SessionStatus.CLOSED


@pytest.mark.unit
def test_fake_calendar_holiday_and_early_close() -> None:
    cal = FakeMarketCalendar(
        holidays=frozenset({date(2026, 7, 3)}),
        early_close_dates=frozenset({date(2026, 7, 2)}),
    )
    holiday = datetime(2026, 7, 3, 15, 0, tzinfo=UTC)
    assert cal.session_status(holiday) is SessionStatus.HOLIDAY
    # 18:00 UTC = 14:00 ET on early-close day (closes 13:00 ET)
    after_early = datetime(2026, 7, 2, 18, 0, tzinfo=UTC)
    assert cal.session_status(after_early) is SessionStatus.EARLY_CLOSED
    before_early = datetime(2026, 7, 2, 15, 0, tzinfo=UTC)  # 11:00 ET
    assert cal.session_status(before_early) is SessionStatus.OPEN


@pytest.mark.unit
def test_fake_calendar_unknown_exchange_fails_closed() -> None:
    cal = FakeMarketCalendar()
    moment = datetime(2026, 7, 22, 15, 0, tzinfo=UTC)
    assert cal.session_status(moment, exchange="XXXX") is SessionStatus.UNKNOWN
    assert cal.is_regular_session_open(moment, exchange="XXXX") is False
