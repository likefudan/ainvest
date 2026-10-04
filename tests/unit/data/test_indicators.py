"""Independent fixed references and explicit warm-up, never fabricated zeros."""

from decimal import Decimal
from importlib import import_module

import pytest
from indicator_fixtures import sample

from ainvest.data.indicators import compute_indicators
from ainvest.data.ports import DataConflictError, DataIncompleteError, DataSchemaError


def test_fixed_linear_reference() -> None:
    page, expected = sample()
    run = compute_indicators(page, expected)
    assert run.technical.sma_20 == Decimal("150.5")
    assert run.technical.sma_50 == Decimal("135.5")
    assert run.technical.rsi_14 == Decimal("100")
    assert run.technical.atr_14 == Decimal("2")
    assert compute_indicators(page, expected) == run
    assert run.library_version and run.input_digest.startswith("sha256:")


@pytest.mark.parametrize("count", [1, 14, 15, 19, 20, 49, 50])
def test_warmup(count: int) -> None:
    page, expected = sample(count)
    values = compute_indicators(page, expected).technical
    assert (values.sma_20 is not None) == (count >= 20)
    assert (values.sma_50 is not None) == (count >= 50)
    assert (values.rsi_14 is not None) == (count >= 15)
    assert (values.atr_14 is not None) == (count >= 15)


def test_gapped_input_not_repaired() -> None:
    page, expected = sample()
    with pytest.raises(DataIncompleteError):
        compute_indicators(page.model_copy(update={"items": page.items[:-1]}), expected)


def test_nonfinite_calculation_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    talib = import_module("talib")
    monkeypatch.setattr(talib, "SMA", lambda *args, **kwargs: [float("nan")])
    page, expected = sample()
    with pytest.raises(DataSchemaError):
        compute_indicators(page, expected)


def test_global_settings_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    talib = import_module("talib")
    monkeypatch.setattr(talib, "get_compatibility", lambda: 1)
    page, expected = sample()
    with pytest.raises(DataConflictError):
        compute_indicators(page, expected)
