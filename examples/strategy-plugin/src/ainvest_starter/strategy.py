"""A deterministic HOLD-only teaching strategy with explicit immutable state."""

import hashlib
from datetime import timedelta
from decimal import Decimal
from typing import ClassVar

from pydantic import Field

from ainvest.schemas.strategy import (
    SignalIntent,
    StrategyContext,
    StrategyState,
    StrategyStateItem,
    StrategyStateValueKind,
    TradeSignal,
)
from ainvest.strategies import StrategyDiagnostics, StrategyParams, StrategyResult


class StarterParams(StrategyParams):
    """Unknown keys and coerced/invalid TTLs are rejected before evaluation."""

    ttl_seconds: int = Field(default=1800, strict=True, ge=1, le=3600)


class StarterHold:
    """No order intent, quantity, network, credentials or hidden mutable state."""

    name: ClassVar[str] = "starter_hold"
    version: ClassVar[str] = "0.1.0"
    params_model: ClassVar[type[StarterParams]] = StarterParams

    def __init__(self, params: StarterParams) -> None:
        self._params = params

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        previous = context.strategy_state
        if previous is not None and (
            previous.strategy != self.name or previous.strategy_version != self.version
        ):
            raise ValueError("state identity/version mismatch; explicit migration required")
        initialized = False
        if previous is not None:
            for item in previous.entries:
                if item.key == "initialized":
                    if item.kind is not StrategyStateValueKind.BOOLEAN:
                        raise ValueError("initialized state must be BOOLEAN")
                    initialized = item.boolean_value is True
        reason = "STARTER_ALREADY_INITIALIZED" if initialized else "STARTER_INITIALIZED"
        # Equal complete inputs/parameters yield the same identifier and output.
        # Do not use random UUIDs, Python hash(), or wall-clock time here.
        identity = "|".join(
            (self.name, self.version, context.model_dump_json(), self._params.model_dump_json())
        )
        signal = TradeSignal(
            signal_id="sig_" + hashlib.sha256(identity.encode()).hexdigest()[:32],
            research_id=context.research.research_id,
            strategy=self.name,
            strategy_version=self.version,
            symbol=context.symbol,
            intent=SignalIntent.HOLD,
            strength=Decimal("0"),
            target_weight=None,
            generated_at=context.as_of,
            expires_at=context.as_of + timedelta(seconds=self._params.ttl_seconds),
            reason_codes=(reason,),
        )
        return StrategyResult(
            signals=(signal,),
            next_state=StrategyState(
                strategy=self.name,
                strategy_version=self.version,
                updated_at=context.as_of,
                entries=(
                    StrategyStateItem(
                        key="initialized", kind=StrategyStateValueKind.BOOLEAN, boolean_value=True
                    ),
                ),
            ),
            diagnostics=StrategyDiagnostics(reason_codes=(reason,)),
        )
