"""Unit tests for multi-strategy signal aggregation (P03-T7 / ADR-020)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from ainvest.portfolio.signal_aggregation import (
    AggregationOutcome,
    AggregationReasonCode,
    SignalAggregationResult,
    aggregate_signals,
    selected_signals,
)
from ainvest.schemas.strategy import SignalIntent, TradeSignal, trade_signal_example

AS_OF = datetime(2026, 7, 24, 18, 30, 10, tzinfo=UTC)


def _signal(**overrides: Any) -> TradeSignal:
    payload = trade_signal_example()
    payload.update(overrides)
    return TradeSignal.model_validate(payload)


@pytest.mark.unit
def test_single_buy_is_selected() -> None:
    signal = _signal(signal_id="sig_01HZYAGG00000001")
    results = aggregate_signals([signal], as_of=AS_OF)
    assert len(results) == 1
    result = results[0]
    assert result.outcome is AggregationOutcome.SELECTED
    assert result.reason_code == AggregationReasonCode.SINGLE_SIGNAL
    assert result.selected_signal == signal
    assert result.input_signals == (signal,)


@pytest.mark.unit
def test_buy_sell_conflict_needs_review_no_selection() -> None:
    buy = _signal(signal_id="sig_01HZYAGGBUY00001", intent="BUY", target_weight="0.10")
    sell = _signal(
        signal_id="sig_01HZYAGGSELL0001",
        intent="SELL",
        target_weight="0.00",
        strategy="mean_reversion",
        strategy_version="1.0.0",
        strength="-0.40",
        reason_codes=["MEAN_REVERSION_EXIT"],
    )
    results = aggregate_signals([buy, sell], as_of=AS_OF)
    assert len(results) == 1
    result = results[0]
    assert result.outcome is AggregationOutcome.NEEDS_REVIEW
    assert result.reason_code == AggregationReasonCode.INTENT_CONFLICT
    assert result.selected_signal is None
    assert result.input_signals == (buy, sell)
    assert selected_signals(results) == ()


@pytest.mark.unit
def test_duplicate_identical_signals_collapse_deterministically() -> None:
    first = _signal(signal_id="sig_01HZYAGGDUPE0002")
    second = _signal(signal_id="sig_01HZYAGGDUPE0001")
    # Same trade fields; only signal_id differs (duplicate delivery).
    results = aggregate_signals([first, second], as_of=AS_OF)
    result = results[0]
    assert result.outcome is AggregationOutcome.SELECTED
    assert result.reason_code == AggregationReasonCode.DUPLICATE_SIGNALS_COLLAPSED
    assert result.selected_signal is not None
    assert result.selected_signal.signal_id == "sig_01HZYAGGDUPE0001"
    assert result.input_signals == (second, first)


@pytest.mark.unit
def test_differing_generated_at_is_as_of_mismatch() -> None:
    early = _signal(
        signal_id="sig_01HZYAGGASOF0001",
        generated_at="2026-07-24T18:30:00Z",
        expires_at="2026-07-24T19:00:00Z",
    )
    late = _signal(
        signal_id="sig_01HZYAGGASOF0002",
        generated_at="2026-07-24T18:30:10Z",
        expires_at="2026-07-24T19:00:10Z",
    )
    results = aggregate_signals([early, late], as_of=AS_OF)
    result = results[0]
    assert result.outcome is AggregationOutcome.NEEDS_REVIEW
    assert result.reason_code == AggregationReasonCode.AS_OF_MISMATCH
    assert result.selected_signal is None


@pytest.mark.unit
def test_mixed_expiry_needs_review() -> None:
    short = _signal(
        signal_id="sig_01HZYAGGEXP00001",
        expires_at="2026-07-24T18:45:00Z",
    )
    long = _signal(
        signal_id="sig_01HZYAGGEXP00002",
        expires_at="2026-07-24T19:30:00Z",
    )
    results = aggregate_signals([short, long], as_of=AS_OF)
    result = results[0]
    assert result.outcome is AggregationOutcome.NEEDS_REVIEW
    assert result.reason_code == AggregationReasonCode.EXPIRY_MISMATCH
    assert result.selected_signal is None


@pytest.mark.unit
def test_strategy_version_mismatch_needs_review() -> None:
    v1 = _signal(signal_id="sig_01HZYAGGVER00001", strategy_version="1.2.0")
    v2 = _signal(signal_id="sig_01HZYAGGVER00002", strategy_version="1.3.0")
    results = aggregate_signals([v1, v2], as_of=AS_OF)
    result = results[0]
    assert result.outcome is AggregationOutcome.NEEDS_REVIEW
    assert result.reason_code == AggregationReasonCode.STRATEGY_VERSION_MISMATCH


@pytest.mark.unit
def test_multi_strategy_same_intent_needs_review() -> None:
    sma = _signal(signal_id="sig_01HZYAGGMULTI001", strategy="sma_crossover")
    mom = _signal(
        signal_id="sig_01HZYAGGMULTI002",
        strategy="momentum",
        strategy_version="1.2.0",
        reason_codes=["MOMENTUM_UP"],
    )
    results = aggregate_signals([sma, mom], as_of=AS_OF)
    result = results[0]
    assert result.outcome is AggregationOutcome.NEEDS_REVIEW
    assert result.reason_code == AggregationReasonCode.MULTI_STRATEGY_CONFLICT
    assert result.selected_signal is None


@pytest.mark.unit
def test_target_weight_conflict_needs_review() -> None:
    light = _signal(signal_id="sig_01HZYAGGWT000001", target_weight="0.05")
    heavy = _signal(signal_id="sig_01HZYAGGWT000002", target_weight="0.15")
    results = aggregate_signals([light, heavy], as_of=AS_OF)
    result = results[0]
    assert result.outcome is AggregationOutcome.NEEDS_REVIEW
    assert result.reason_code == AggregationReasonCode.TARGET_WEIGHT_CONFLICT


@pytest.mark.unit
def test_strength_disagreement_is_not_probability_merge() -> None:
    weak = _signal(signal_id="sig_01HZYAGGSTR00001", strength="0.20")
    strong = _signal(signal_id="sig_01HZYAGGSTR00002", strength="0.90")
    results = aggregate_signals([weak, strong], as_of=AS_OF)
    result = results[0]
    assert result.outcome is AggregationOutcome.NEEDS_REVIEW
    assert result.reason_code == AggregationReasonCode.STRENGTH_DISAGREEMENT
    assert result.selected_signal is None


@pytest.mark.unit
def test_hold_and_expired_preserved_but_not_selected() -> None:
    hold = _signal(
        signal_id="sig_01HZYAGGHOLD0001",
        intent="HOLD",
        target_weight=None,
        reason_codes=["NO_CROSS"],
    )
    expired = _signal(
        signal_id="sig_01HZYAGGEXPIRD01",
        generated_at="2026-07-24T17:00:00Z",
        expires_at="2026-07-24T17:30:00Z",
    )
    results = aggregate_signals([hold, expired], as_of=AS_OF)
    result = results[0]
    assert result.outcome is AggregationOutcome.NO_TRADE
    assert result.reason_code == AggregationReasonCode.NO_ACTIONABLE_SIGNALS
    assert result.selected_signal is None
    assert result.input_signals == (expired, hold)


@pytest.mark.unit
def test_all_hold_reason() -> None:
    hold = _signal(
        signal_id="sig_01HZYAGGHOLD0002",
        intent="HOLD",
        target_weight=None,
        reason_codes=["NO_CROSS"],
    )
    results = aggregate_signals([hold], as_of=AS_OF)
    assert results[0].reason_code == AggregationReasonCode.ALL_HOLD


@pytest.mark.unit
def test_all_expired_or_inactive_reason() -> None:
    future = _signal(
        signal_id="sig_01HZYAGGFUTURE01",
        generated_at="2026-07-24T19:00:00Z",
        expires_at="2026-07-24T19:30:00Z",
    )
    results = aggregate_signals([future], as_of=AS_OF)
    assert results[0].reason_code == AggregationReasonCode.ALL_EXPIRED_OR_INACTIVE


@pytest.mark.unit
def test_hold_does_not_block_actionable_buy() -> None:
    hold = _signal(
        signal_id="sig_01HZYAGGHOLD0003",
        intent="HOLD",
        target_weight=None,
        reason_codes=["NO_CROSS"],
    )
    buy = _signal(signal_id="sig_01HZYAGGBUY00002")
    results = aggregate_signals([hold, buy], as_of=AS_OF)
    result = results[0]
    assert result.outcome is AggregationOutcome.SELECTED
    assert result.selected_signal == buy
    assert hold in result.input_signals


@pytest.mark.unit
def test_per_symbol_isolation_never_emits_opposing_orders() -> None:
    aapl_buy = _signal(signal_id="sig_01HZYAGGAAPL0001", symbol="AAPL", intent="BUY")
    aapl_sell = _signal(
        signal_id="sig_01HZYAGGAAPL0002",
        symbol="AAPL",
        intent="SELL",
        target_weight="0.00",
        strategy="exit_rule",
        strategy_version="1.0.0",
        strength="-0.50",
        reason_codes=["EXIT"],
    )
    msft_buy = _signal(
        signal_id="sig_01HZYAGGMSFT0001",
        symbol="MSFT",
        intent="BUY",
        reason_codes=["MSFT_SETUP"],
    )
    results = aggregate_signals([msft_buy, aapl_sell, aapl_buy], as_of=AS_OF)
    assert [item.symbol for item in results] == ["AAPL", "MSFT"]
    aapl, msft = results
    assert aapl.outcome is AggregationOutcome.NEEDS_REVIEW
    assert aapl.reason_code == AggregationReasonCode.INTENT_CONFLICT
    assert aapl.selected_signal is None
    assert msft.outcome is AggregationOutcome.SELECTED
    assert msft.selected_signal == msft_buy
    # Never more than one selected signal per symbol; AAPL has none.
    selected = selected_signals(results)
    assert len(selected) == 1
    assert selected[0].symbol == "MSFT"
    assert selected[0].intent is SignalIntent.BUY


@pytest.mark.unit
def test_empty_input_returns_empty_tuple() -> None:
    assert aggregate_signals([], as_of=AS_OF) == ()


@pytest.mark.unit
def test_intent_conflict_outranks_as_of_mismatch() -> None:
    buy = _signal(
        signal_id="sig_01HZYAGGRANK0001",
        intent="BUY",
        generated_at="2026-07-24T18:30:00Z",
        expires_at="2026-07-24T19:00:00Z",
    )
    sell = _signal(
        signal_id="sig_01HZYAGGRANK0002",
        intent="SELL",
        target_weight="0.00",
        generated_at="2026-07-24T18:30:10Z",
        expires_at="2026-07-24T19:00:10Z",
        strategy="other",
        strategy_version="9.9.9",
        strength="-0.10",
        reason_codes=["OTHER_EXIT"],
    )
    result = aggregate_signals([buy, sell], as_of=AS_OF)[0]
    assert result.reason_code == AggregationReasonCode.INTENT_CONFLICT


@pytest.mark.unit
def test_result_model_rejects_selected_without_signal() -> None:
    with pytest.raises(ValidationError):
        SignalAggregationResult.model_validate(
            {
                "symbol": "AAPL",
                "outcome": "SELECTED",
                "reason_code": "SINGLE_SIGNAL",
                "selected_signal": None,
                "input_signals": [],
                "as_of": AS_OF,
            }
        )


@pytest.mark.unit
def test_naive_as_of_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        aggregate_signals([_signal()], as_of=datetime(2026, 7, 24, 18, 30, 10))


@pytest.mark.unit
def test_aggregation_is_order_independent_for_conflicts() -> None:
    buy = _signal(signal_id="sig_01HZYAGGORD00001", intent="BUY")
    sell = _signal(
        signal_id="sig_01HZYAGGORD00002",
        intent="SELL",
        target_weight="0.00",
        strategy="exit_rule",
        strategy_version="1.0.0",
        strength="-0.20",
        reason_codes=["EXIT"],
    )
    forward = aggregate_signals([buy, sell], as_of=AS_OF)
    reverse = aggregate_signals([sell, buy], as_of=AS_OF)
    assert forward == reverse


@pytest.mark.unit
def test_as_of_exactly_at_expiry_is_inactive() -> None:
    signal = _signal(
        generated_at="2026-07-24T18:00:00Z",
        expires_at="2026-07-24T18:30:10Z",
    )
    # TradeSignal.is_expired: as_of >= expires_at
    result = aggregate_signals([signal], as_of=AS_OF)[0]
    assert result.outcome is AggregationOutcome.NO_TRADE
    assert result.reason_code == AggregationReasonCode.ALL_EXPIRED_OR_INACTIVE


@pytest.mark.unit
def test_duplicate_collapse_ignores_research_id_difference() -> None:
    """Trade-identical duplicates still collapse; research_id is not a merge key."""
    a = _signal(signal_id="sig_01HZYAGGRES00002", research_id="res_01HZYAGGRES0001")
    b = _signal(signal_id="sig_01HZYAGGRES00001", research_id="res_01HZYAGGRES0002")
    result = aggregate_signals([a, b], as_of=AS_OF)[0]
    assert result.outcome is AggregationOutcome.SELECTED
    assert result.reason_code == AggregationReasonCode.DUPLICATE_SIGNALS_COLLAPSED
    assert result.selected_signal is not None
    assert result.selected_signal.signal_id == "sig_01HZYAGGRES00001"


@pytest.mark.unit
def test_clock_skew_tolerance_not_invented_for_future_signal() -> None:
    """Signals generated after as_of are inactive; no look-ahead selection."""
    signal = _signal(
        signal_id="sig_01HZYAGGLOOK0001",
        generated_at=(AS_OF + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
        expires_at=(AS_OF + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
    )
    result = aggregate_signals([signal], as_of=AS_OF)[0]
    assert result.selected_signal is None
    assert result.reason_code == AggregationReasonCode.ALL_EXPIRED_OR_INACTIVE
