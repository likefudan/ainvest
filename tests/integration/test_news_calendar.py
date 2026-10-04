"""The existing Risk Engine consumes the real shared calendar port."""

from datetime import date, datetime

from risk.risk_fixtures import make_context

from ainvest.data.calendar import ExchangeCalendar
from ainvest.risk.rules.eligibility import SessionRule
from ainvest.schemas.risk import RiskOutcome


def test_risk_rejects_at_actual_early_close() -> None:
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
