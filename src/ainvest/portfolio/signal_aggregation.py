"""Multi-strategy TradeSignal aggregation (P03-T7 / ADR-020 / DEC-020).

Converts a batch of strategy signals into at most one selected signal per
symbol. Conflicts fail closed to ``NEEDS_REVIEW`` with no selection. This
module never sizes quantities, never submits orders, and never treats
``strength`` as a probability or merge weight.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import field_validator, model_validator

from ainvest.schemas.common import (
    DomainModel,
    MachineCode,
    Symbol,
    UtcDateTime,
    ensure_utc,
)
from ainvest.schemas.strategy import SignalIntent, TradeSignal

# Group key axes required by P03-T7: signal as_of (generated_at), expiry,
# and strategy_version. Symbol is the outer partition.
_GroupKey = tuple[datetime, datetime, str]


class AggregationOutcome(StrEnum):
    """Per-symbol aggregation result class."""

    SELECTED = "SELECTED"
    NO_TRADE = "NO_TRADE"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class AggregationReasonCode(StrEnum):
    """Stable machine-readable aggregation outcomes (ADR-020)."""

    SINGLE_SIGNAL = "SINGLE_SIGNAL"
    DUPLICATE_SIGNALS_COLLAPSED = "DUPLICATE_SIGNALS_COLLAPSED"
    NO_ACTIONABLE_SIGNALS = "NO_ACTIONABLE_SIGNALS"
    ALL_HOLD = "ALL_HOLD"
    ALL_EXPIRED_OR_INACTIVE = "ALL_EXPIRED_OR_INACTIVE"
    INTENT_CONFLICT = "INTENT_CONFLICT"
    TARGET_WEIGHT_CONFLICT = "TARGET_WEIGHT_CONFLICT"
    AS_OF_MISMATCH = "AS_OF_MISMATCH"
    EXPIRY_MISMATCH = "EXPIRY_MISMATCH"
    STRATEGY_VERSION_MISMATCH = "STRATEGY_VERSION_MISMATCH"
    MULTI_STRATEGY_CONFLICT = "MULTI_STRATEGY_CONFLICT"
    STRENGTH_DISAGREEMENT = "STRENGTH_DISAGREEMENT"


class SignalAggregationResult(DomainModel):
    """Auditable aggregation outcome for one symbol.

    ``input_signals`` preserves every signal supplied for the symbol, including
    HOLD / expired / inactive ones. ``selected_signal`` is set only when
    ``outcome`` is ``SELECTED``.
    """

    symbol: Symbol
    outcome: AggregationOutcome
    reason_code: MachineCode
    selected_signal: TradeSignal | None = None
    input_signals: tuple[TradeSignal, ...]
    as_of: UtcDateTime

    @field_validator("input_signals", mode="before")
    @classmethod
    def _coerce_input_signals(cls, value: object) -> object:
        if value is None:
            return ()
        return value

    @model_validator(mode="after")
    def _outcome_consistency(self) -> SignalAggregationResult:
        for signal in self.input_signals:
            if signal.symbol != self.symbol:
                raise ValueError("input_signals must all match result.symbol")
        if self.outcome is AggregationOutcome.SELECTED:
            if self.selected_signal is None:
                raise ValueError("SELECTED requires selected_signal")
            if self.selected_signal.symbol != self.symbol:
                raise ValueError("selected_signal.symbol must match result.symbol")
            if self.reason_code not in {
                AggregationReasonCode.SINGLE_SIGNAL,
                AggregationReasonCode.DUPLICATE_SIGNALS_COLLAPSED,
            }:
                raise ValueError("SELECTED requires a selection reason_code")
        else:
            if self.selected_signal is not None:
                raise ValueError("non-SELECTED outcomes cannot carry selected_signal")
            if self.outcome is AggregationOutcome.NEEDS_REVIEW and self.reason_code in {
                AggregationReasonCode.SINGLE_SIGNAL,
                AggregationReasonCode.DUPLICATE_SIGNALS_COLLAPSED,
                AggregationReasonCode.NO_ACTIONABLE_SIGNALS,
                AggregationReasonCode.ALL_HOLD,
                AggregationReasonCode.ALL_EXPIRED_OR_INACTIVE,
            }:
                raise ValueError("NEEDS_REVIEW requires a conflict reason_code")
        return self


def aggregate_signals(
    signals: Sequence[TradeSignal],
    *,
    as_of: datetime,
) -> tuple[SignalAggregationResult, ...]:
    """Aggregate signals into at most one selection per symbol.

    Deterministic: symbols and inputs are ordered by ``symbol`` then
    ``signal_id``. Never returns opposing selected intents for one symbol —
    each symbol yields exactly one :class:`SignalAggregationResult`.

    Strength is compared for exact agreement only; it is never used as a
    probability or weighting factor (ADR-020 / DEC-020).
    """
    clock = ensure_utc(as_of)
    by_symbol: dict[str, list[TradeSignal]] = defaultdict(list)
    for signal in signals:
        by_symbol[signal.symbol].append(signal)

    results: list[SignalAggregationResult] = []
    for symbol in sorted(by_symbol):
        ordered = tuple(sorted(by_symbol[symbol], key=lambda item: item.signal_id))
        results.append(_aggregate_symbol(symbol=symbol, signals=ordered, as_of=clock))
    return tuple(results)


def selected_signals(
    results: Sequence[SignalAggregationResult],
) -> tuple[TradeSignal, ...]:
    """Return selected signals in result order (symbols already sorted)."""
    return tuple(result.selected_signal for result in results if result.selected_signal is not None)


def _aggregate_symbol(
    *,
    symbol: str,
    signals: tuple[TradeSignal, ...],
    as_of: datetime,
) -> SignalAggregationResult:
    actionable: list[TradeSignal] = []
    hold_count = 0
    expired_or_inactive = 0
    for signal in signals:
        if as_of < signal.generated_at or signal.is_expired(as_of):
            expired_or_inactive += 1
            continue
        if signal.intent is SignalIntent.HOLD:
            hold_count += 1
            continue
        actionable.append(signal)

    if not actionable:
        reason = _no_trade_reason(hold_count=hold_count, expired_or_inactive=expired_or_inactive)
        return SignalAggregationResult(
            symbol=symbol,
            outcome=AggregationOutcome.NO_TRADE,
            reason_code=reason,
            selected_signal=None,
            input_signals=signals,
            as_of=as_of,
        )

    conflict = _conflict_reason(actionable)
    if conflict is not None:
        return SignalAggregationResult(
            symbol=symbol,
            outcome=AggregationOutcome.NEEDS_REVIEW,
            reason_code=conflict,
            selected_signal=None,
            input_signals=signals,
            as_of=as_of,
        )

    selected = min(actionable, key=lambda item: item.signal_id)
    reason = (
        AggregationReasonCode.SINGLE_SIGNAL
        if len(actionable) == 1
        else AggregationReasonCode.DUPLICATE_SIGNALS_COLLAPSED
    )
    return SignalAggregationResult(
        symbol=symbol,
        outcome=AggregationOutcome.SELECTED,
        reason_code=reason,
        selected_signal=selected,
        input_signals=signals,
        as_of=as_of,
    )


def _no_trade_reason(*, hold_count: int, expired_or_inactive: int) -> AggregationReasonCode:
    if hold_count > 0 and expired_or_inactive == 0:
        return AggregationReasonCode.ALL_HOLD
    if expired_or_inactive > 0 and hold_count == 0:
        return AggregationReasonCode.ALL_EXPIRED_OR_INACTIVE
    return AggregationReasonCode.NO_ACTIONABLE_SIGNALS


def _conflict_reason(actionable: list[TradeSignal]) -> AggregationReasonCode | None:
    """Return the first applicable conflict code, or None when consensus holds.

    Priority matches ADR-020: opposing intent first, then group-key mismatches
    (as_of / expiry / strategy_version), then strategy identity and field drift.
    """
    intents = {signal.intent for signal in actionable}
    if SignalIntent.BUY in intents and SignalIntent.SELL in intents:
        return AggregationReasonCode.INTENT_CONFLICT

    buckets: dict[_GroupKey, list[TradeSignal]] = defaultdict(list)
    for signal in actionable:
        key: _GroupKey = (
            signal.generated_at,
            signal.expires_at,
            signal.strategy_version,
        )
        buckets[key].append(signal)

    if len(buckets) > 1:
        generated_ats = {key[0] for key in buckets}
        expiries = {key[1] for key in buckets}
        if len(generated_ats) > 1:
            return AggregationReasonCode.AS_OF_MISMATCH
        if len(expiries) > 1:
            return AggregationReasonCode.EXPIRY_MISMATCH
        # Group key is (generated_at, expires_at, strategy_version); if buckets
        # differ and the first two axes match, the version axis must differ.
        return AggregationReasonCode.STRATEGY_VERSION_MISMATCH

    strategies = {(signal.strategy, signal.strategy_version) for signal in actionable}
    if len(strategies) > 1:
        return AggregationReasonCode.MULTI_STRATEGY_CONFLICT

    weights = {signal.target_weight for signal in actionable}
    if len(weights) > 1:
        return AggregationReasonCode.TARGET_WEIGHT_CONFLICT

    strengths = {signal.strength for signal in actionable}
    if len(strengths) > 1:
        # Disagreement is preserved as a review reason; strength is not a weight.
        return AggregationReasonCode.STRENGTH_DISAGREEMENT

    return None


__all__ = [
    "AggregationOutcome",
    "AggregationReasonCode",
    "SignalAggregationResult",
    "aggregate_signals",
    "selected_signals",
]
