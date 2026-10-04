"""Actual exchange schedules, not synthetic holiday arithmetic."""

import subprocess
import sys
from datetime import date, datetime
from types import SimpleNamespace
from typing import ClassVar

import pytest

from ainvest.data import calendar as module
from ainvest.data.calendar import ExchangeCalendar
from ainvest.data.calendar_port import SessionStatus


@pytest.mark.parametrize(
    ("moment", "status"),
    [
        ("2026-03-06T14:30:00Z", "OPEN"),
        ("2026-03-09T13:29:59Z", "CLOSED"),
        ("2026-03-09T13:30:00Z", "OPEN"),
        ("2026-03-09T20:00:00Z", "CLOSED"),
        ("2026-11-02T14:30:00Z", "OPEN"),
        ("2026-11-26T16:00:00Z", "HOLIDAY"),
        ("2026-11-27T17:59:59Z", "OPEN"),
        ("2026-11-27T18:00:00Z", "EARLY_CLOSED"),
        ("2026-07-04T15:00:00Z", "CLOSED"),
        ("2026-07-03T15:00:00Z", "HOLIDAY"),
        ("2026-03-09T09:30:00-04:00", "OPEN"),
        ("2027-01-04T15:00:00Z", "UNKNOWN"),
        ("2026-03-09T09:30:00", "UNKNOWN"),
    ],
)
def test_calendar(moment: str, status: str) -> None:
    # Keep pandas out of the pytest parent: Linux child peak RSS can inherit
    # the parent's pre-exec high water and trip unrelated strategy watchdogs.
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from datetime import date, datetime
from ainvest.data.calendar import ExchangeCalendar
from ainvest.data.calendar_port import MarketCalendar
calendar = ExchangeCalendar(valid_from=date(2026, 1, 1), valid_through=date(2026, 12, 31))
assert isinstance(calendar, MarketCalendar)
moment, status = sys.argv[1:]
for venue in ('XNYS', 'XNAS'):
    assert calendar.session_status(datetime.fromisoformat(moment), exchange=venue).value == status
assert calendar.is_regular_session_open(datetime.fromisoformat(moment)) == (status == 'OPEN')
""",
            moment,
            status,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_unknown_venue_and_bad_bounds() -> None:
    calendar = ExchangeCalendar(valid_from=date(2026, 1, 1), valid_through=date(2026, 12, 31))
    assert (
        calendar.session_status(datetime.fromisoformat("2026-03-09T15:00:00Z"), exchange="OTC")
        is SessionStatus.UNKNOWN
    )
    with pytest.raises(ValueError):
        ExchangeCalendar(valid_from=date(2026, 2, 1), valid_through=date(2026, 1, 1))


def test_dependency_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(venue: str, day: date) -> None:
        raise RuntimeError("bad schedule")

    monkeypatch.setattr(module, "_session", broken)
    calendar = ExchangeCalendar(valid_from=date(2026, 1, 1), valid_through=date(2026, 12, 31))
    assert (
        calendar.session_status(datetime.fromisoformat("2026-03-09T15:00:00Z"))
        is SessionStatus.UNKNOWN
    )


@pytest.mark.parametrize("damage", ["rows", "interrupt", "reversed", "day", "extended"])
def test_malformed_schedule_fails_closed(monkeypatch: pytest.MonkeyPatch, damage: str) -> None:
    opening, closing = "2026-03-09T13:30:00Z", "2026-03-09T20:00:00Z"
    if damage == "reversed":
        opening, closing = closing, opening
    elif damage == "day":
        closing = "2026-03-10T20:00:00Z"
    elif damage == "extended":
        closing = "2026-03-09T21:00:00Z"

    class Schedule:
        empty = False
        columns = ["interruption_start_1"] if damage == "interrupt" else []
        iloc: ClassVar[dict[int, dict[str, SimpleNamespace]]] = {
            0: {
                "market_open": SimpleNamespace(
                    to_pydatetime=lambda: datetime.fromisoformat(opening)
                ),
                "market_close": SimpleNamespace(
                    to_pydatetime=lambda: datetime.fromisoformat(closing)
                ),
            }
        }

        def __len__(self) -> int:
            return 2 if damage == "rows" else 1

        def __getitem__(self, key: str) -> SimpleNamespace:
            return SimpleNamespace(notna=lambda: SimpleNamespace(any=lambda: True))

    frame = Schedule()
    fake = SimpleNamespace(get_calendar=lambda _: SimpleNamespace(schedule=lambda **_: frame))
    monkeypatch.setattr(module, "import_module", lambda _: fake)
    module._session.cache_clear()
    calendar = ExchangeCalendar(valid_from=date(2026, 1, 1), valid_through=date(2026, 12, 31))
    assert not calendar.is_regular_session_open(datetime.fromisoformat("2026-03-09T15:00:00Z"))
    module._session.cache_clear()
