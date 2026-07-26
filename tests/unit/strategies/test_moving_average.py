"""Unit tests for the reference moving-average strategy (P03-T3)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from strategy_fixtures import context_payload, make_context

from ainvest.schemas.strategy import SignalIntent, parse_strategy_context
from ainvest.strategies.reference.moving_average.strategy import (
    MovingAverageParams,
    MovingAverageStrategy,
)


@pytest.mark.unit
def test_params_require_fast_lt_slow() -> None:
    with pytest.raises(ValidationError):
        MovingAverageParams(fast_window=50, slow_window=20)


@pytest.mark.unit
def test_params_reject_scientific_target_weight() -> None:
    with pytest.raises(ValidationError):
        MovingAverageParams.model_validate({"target_weight": "1E-50"})


@pytest.mark.unit
def test_signal_ttl_from_params_controls_expiry() -> None:
    strategy = MovingAverageStrategy(MovingAverageParams.model_validate({"signal_ttl": "15m"}))
    context = make_context(sma_20="210.00", sma_50="200.00")
    signal = strategy.evaluate(context).signals[0]
    assert signal.expires_at - signal.generated_at == timedelta(minutes=15)


@pytest.mark.unit
def test_buy_on_cross_above() -> None:
    strategy = MovingAverageStrategy(MovingAverageParams())
    result = strategy.evaluate(make_context(sma_20="210.00", sma_50="200.00"))
    assert len(result.signals) == 1
    signal = result.signals[0]
    assert signal.intent is SignalIntent.BUY
    assert signal.reason_codes == ("SMA_FAST_CROSSED_ABOVE_SLOW",)
    assert signal.target_weight == Decimal("0.10")
    assert signal.generated_at == make_context().as_of
    assert result.next_state is not None
    assert result.next_state.entries[0].boolean_value is True


@pytest.mark.unit
def test_sell_on_cross_below() -> None:
    payload = context_payload(sma_20="190.00", sma_50="200.00")
    payload["strategy_state"]["entries"][0]["boolean_value"] = True
    strategy = MovingAverageStrategy(MovingAverageParams())
    result = strategy.evaluate(parse_strategy_context(payload))
    signal = result.signals[0]
    assert signal.intent is SignalIntent.SELL
    assert signal.reason_codes == ("SMA_FAST_CROSSED_BELOW_SLOW",)
    assert signal.target_weight is None


@pytest.mark.unit
def test_hold_when_no_cross() -> None:
    payload = context_payload(sma_20="210.00", sma_50="200.00")
    payload["strategy_state"]["entries"][0]["boolean_value"] = True
    strategy = MovingAverageStrategy(MovingAverageParams())
    result = strategy.evaluate(parse_strategy_context(payload))
    assert result.signals[0].intent is SignalIntent.HOLD
    assert result.signals[0].reason_codes == ("SMA_NO_CROSS",)


@pytest.mark.unit
def test_hold_on_insufficient_data() -> None:
    payload = context_payload()
    payload["research"]["technical"] = None
    strategy = MovingAverageStrategy(MovingAverageParams())
    result = strategy.evaluate(parse_strategy_context(payload))
    assert result.signals[0].intent is SignalIntent.HOLD
    assert result.signals[0].reason_codes == ("INSUFFICIENT_DATA",)


@pytest.mark.unit
def test_deterministic_byte_identical_output() -> None:
    strategy = MovingAverageStrategy(MovingAverageParams())
    context = make_context(sma_20="210.00", sma_50="200.00")
    first = strategy.evaluate(context)
    second = strategy.evaluate(context)
    assert first.model_dump_json() == second.model_dump_json()


@pytest.mark.unit
def test_no_lookahead_uses_context_as_of_only() -> None:
    strategy = MovingAverageStrategy(MovingAverageParams())
    context = make_context(sma_20="210.00", sma_50="200.00")
    result = strategy.evaluate(context)
    signal = result.signals[0]
    assert signal.generated_at == context.as_of
    assert signal.expires_at > context.as_of
    assert result.next_state is not None
    assert result.next_state.updated_at == context.as_of


@pytest.mark.unit
def test_initialize_without_prior_state_holds() -> None:
    payload = context_payload(sma_20="210.00", sma_50="200.00")
    payload["strategy_state"] = None
    strategy = MovingAverageStrategy(MovingAverageParams())
    result = strategy.evaluate(parse_strategy_context(payload))
    assert result.signals[0].intent is SignalIntent.HOLD
    assert result.signals[0].reason_codes == ("SMA_RELATIONSHIP_INITIALIZED",)


@pytest.mark.unit
def test_source_code_avoids_system_clock_and_network() -> None:
    from pathlib import Path

    source = Path(__file__).resolve().parents[3] / (
        "src/ainvest/strategies/reference/moving_average/strategy.py"
    )
    text = source.read_text(encoding="utf-8")
    assert "datetime.now(" not in text
    assert "date.today(" not in text
    assert "urllib" not in text
    assert "requests" not in text
    assert "import socket" not in text
