"""Quality rejection cases."""

from datetime import timedelta

import pytest
from indicator_fixtures import sample

from ainvest.data.models import PriceAdjustment
from ainvest.data.quality import assess_bars


def test_clean_series() -> None:
    page, expected = sample()
    assert assess_bars(page, expected).issues == ()


@pytest.mark.parametrize(
    "damage,code",
    [
        ("duplicate", "DUPLICATE_BARS"),
        ("order", "UNORDERED_BARS"),
        ("gap", "BAR_GRID_MISMATCH"),
        ("adjustment", "ADJUSTMENT_MISMATCH"),
        ("currency", "INSTRUMENT_MISMATCH"),
        ("stale", "STALE_BARS"),
        ("future", "FUTURE_BARS"),
        ("partial", "INCOMPLETE_PAGE"),
    ],
)
def test_quality_failures(damage: str, code: str) -> None:
    page, expected = sample()
    if damage == "duplicate":
        page = page.model_copy(update={"items": (*page.items, page.items[-1])})
    elif damage == "order":
        page = page.model_copy(update={"items": tuple(reversed(page.items))})
    elif damage == "gap":
        page = page.model_copy(update={"items": page.items[:-1]})
    elif damage == "adjustment":
        expected = expected.model_copy(update={"adjustment": PriceAdjustment.SPLIT})
    elif damage == "currency":
        expected = expected.model_copy(
            update={"instrument": expected.instrument.model_copy(update={"currency": "EUR"})}
        )
    elif damage == "stale":
        expected = expected.model_copy(update={"as_of": expected.as_of + timedelta(days=1)})
    elif damage == "future":
        expected = expected.model_copy(update={"as_of": page.items[0].bar_start})
    elif damage == "partial":
        page = page.model_copy(update={"next_cursor": "more"})
    assert code in assess_bars(page, expected).issues
