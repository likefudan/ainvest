"""The existing Risk Engine consumes the real shared calendar port."""

import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from risk.risk_fixtures import make_context

from ainvest.data.calendar import ExchangeCalendar
from ainvest.risk.rules.eligibility import SessionRule
from ainvest.schemas.risk import RiskOutcome


def _check_risk() -> None:
    rule = SessionRule(
        ExchangeCalendar(valid_from=date(2026, 1, 1), valid_through=date(2026, 12, 31))
    )
    for timestamp, expected in (
        ("2026-11-27T17:59:59Z", RiskOutcome.APPROVED),
        ("2026-11-27T18:00:00Z", RiskOutcome.REJECTED),
        ("2026-11-26T16:00:00Z", RiskOutcome.REJECTED),
        ("2027-01-04T16:00:00Z", RiskOutcome.REJECTED),
    ):
        context = make_context(as_of=datetime.fromisoformat(timestamp))
        assert rule.evaluate(context).decision is expected


def test_risk_rejects_at_actual_early_close() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import runpy, sys
from pathlib import Path
path = Path(sys.argv[1])
sys.path.insert(0, str(path.parents[1] / 'unit'))
runpy.run_path(str(path))['_check_risk']()
""",
            str(Path(__file__).resolve()),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
