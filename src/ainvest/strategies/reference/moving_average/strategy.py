"""Deterministic moving-average crossover strategy (design.md §5.3.1).

Uses only ``context.as_of`` and values supplied on ``StrategyContext`` (research
technical indicators and optional prior state). Never reads the system clock,
opens network connections, or submits orders.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import Decimal
from typing import ClassVar, Self

from pydantic import Field, model_validator

from ainvest.schemas.common import Weight
from ainvest.schemas.strategy import (
    SignalIntent,
    StrategyContext,
    StrategyState,
    StrategyStateItem,
    StrategyStateValueKind,
    TradeSignal,
)
from ainvest.strategies import StrategyDiagnostics, StrategyParams, StrategyResult

_STATE_KEY_FAST_ABOVE: str = "fast_above_slow"
_DEFAULT_SIGNAL_TTL: timedelta = timedelta(minutes=30)

# Map configured windows onto ResearchPacket technical indicator fields.
_SMA_FIELDS: dict[int, str] = {
    20: "sma_20",
    50: "sma_50",
}


class MovingAverageParams(StrategyParams):
    """Parameters for the reference SMA crossover strategy."""

    fast_window: int = Field(default=20, ge=2)
    slow_window: int = Field(default=50, ge=3)
    # Domain Weight so scientific notation / extreme exponents fail at bind time.
    target_weight: Weight = Field(default=Decimal("0.10"), gt=0)

    @model_validator(mode="after")
    def _fast_slower_than_slow(self) -> Self:
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be < slow_window")
        return self


class MovingAverageStrategy:
    """BUY/SELL/HOLD from fast/slow SMA relationship; intents only."""

    name: ClassVar[str] = "moving_average"
    version: ClassVar[str] = "1.0.0"
    params_model: ClassVar[type[MovingAverageParams]] = MovingAverageParams

    def __init__(self, params: MovingAverageParams) -> None:
        self._params = params

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        fast = self._sma_value(context, self._params.fast_window)
        slow = self._sma_value(context, self._params.slow_window)
        if fast is None or slow is None:
            return StrategyResult(
                signals=(
                    self._signal(
                        context,
                        intent=SignalIntent.HOLD,
                        strength=Decimal("0"),
                        reason_codes=("INSUFFICIENT_DATA",),
                        target_weight=None,
                    ),
                ),
                next_state=self._next_state(context, fast_above=None),
                diagnostics=StrategyDiagnostics(
                    reason_codes=("INSUFFICIENT_DATA",),
                    notes=("missing technical SMA for configured windows",),
                    metrics={
                        "fast_window": str(self._params.fast_window),
                        "slow_window": str(self._params.slow_window),
                    },
                ),
            )

        fast_above = fast > slow
        prev_above = self._previous_fast_above(context)
        if prev_above is None:
            intent = SignalIntent.HOLD
            reason = "SMA_RELATIONSHIP_INITIALIZED"
            strength = Decimal("0")
            target_weight = None
        elif fast_above and not prev_above:
            intent = SignalIntent.BUY
            reason = "SMA_FAST_CROSSED_ABOVE_SLOW"
            strength = self._strength(fast, slow)
            target_weight = self._params.target_weight
        elif (not fast_above) and prev_above:
            intent = SignalIntent.SELL
            reason = "SMA_FAST_CROSSED_BELOW_SLOW"
            strength = -self._strength(fast, slow)
            target_weight = None
        else:
            intent = SignalIntent.HOLD
            reason = "SMA_NO_CROSS"
            strength = Decimal("0")
            target_weight = None

        metrics = {
            "fast_sma": format(fast, "f"),
            "slow_sma": format(slow, "f"),
            "fast_window": str(self._params.fast_window),
            "slow_window": str(self._params.slow_window),
            "fast_above_slow": "true" if fast_above else "false",
        }
        return StrategyResult(
            signals=(
                self._signal(
                    context,
                    intent=intent,
                    strength=strength,
                    reason_codes=(reason,),
                    target_weight=target_weight,
                ),
            ),
            next_state=self._next_state(context, fast_above=fast_above),
            diagnostics=StrategyDiagnostics(reason_codes=(reason,), metrics=metrics),
        )

    def _sma_value(self, context: StrategyContext, window: int) -> Decimal | None:
        technical = context.research.technical
        if technical is None:
            return None
        field_name = _SMA_FIELDS.get(window)
        if field_name is None:
            return None
        value = getattr(technical, field_name, None)
        if value is None:
            return None
        return Decimal(value)

    def _previous_fast_above(self, context: StrategyContext) -> bool | None:
        state = context.strategy_state
        if state is None:
            return None
        for item in state.entries:
            if item.key == _STATE_KEY_FAST_ABOVE and item.kind is StrategyStateValueKind.BOOLEAN:
                return bool(item.boolean_value)
        return None

    def _next_state(
        self,
        context: StrategyContext,
        *,
        fast_above: bool | None,
    ) -> StrategyState | None:
        if fast_above is None:
            return context.strategy_state
        return StrategyState(
            strategy=self.name,
            strategy_version=self.version,
            updated_at=context.as_of,
            entries=(
                StrategyStateItem(
                    key=_STATE_KEY_FAST_ABOVE,
                    kind=StrategyStateValueKind.BOOLEAN,
                    boolean_value=fast_above,
                ),
            ),
        )

    def _signal(
        self,
        context: StrategyContext,
        *,
        intent: SignalIntent,
        strength: Decimal,
        reason_codes: tuple[str, ...],
        target_weight: Decimal | None,
    ) -> TradeSignal:
        generated_at = context.as_of
        expires_at = generated_at + _DEFAULT_SIGNAL_TTL
        return TradeSignal(
            signal_id=self._signal_id(context, intent=intent, reason_codes=reason_codes),
            research_id=context.research.research_id,
            strategy=self.name,
            strategy_version=self.version,
            symbol=context.symbol,
            intent=intent,
            strength=strength,
            target_weight=target_weight,
            generated_at=generated_at,
            expires_at=expires_at,
            reason_codes=reason_codes,
        )

    def _signal_id(
        self,
        context: StrategyContext,
        *,
        intent: SignalIntent,
        reason_codes: tuple[str, ...],
    ) -> str:
        payload = "|".join(
            [
                self.name,
                self.version,
                context.symbol,
                context.as_of.isoformat(),
                intent.value,
                ",".join(reason_codes),
                str(context.research.research_id),
            ]
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        return f"sig_{digest}"

    @staticmethod
    def _strength(fast: Decimal, slow: Decimal) -> Decimal:
        if slow == 0:
            return Decimal("0.5")
        gap = abs(fast - slow) / abs(slow)
        # Clamp to (0, 1]; deterministic and independent of wall clock.
        if gap > 1:
            return Decimal("1")
        if gap == 0:
            return Decimal("0.01")
        quantized = gap.quantize(Decimal("0.0001"))
        return quantized if quantized > 0 else Decimal("0.0001")
