"""Nondeterministic strategy — fails determinism."""

from __future__ import annotations

import time
from typing import ClassVar

from pydantic import BaseModel

from ainvest.schemas.strategy import StrategyContext
from ainvest.strategies.definitions import StrategyDiagnostics, StrategyResult
from strategy_conformance.invalid._common import InvalidParams


class NondeterministicStrategy:
    name: ClassVar[str] = "invalid_nondeterministic"
    version: ClassVar[str] = "1.0.0"
    params_model: ClassVar[type[BaseModel]] = InvalidParams

    def __init__(self, params: InvalidParams) -> None:
        self._params = params

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        del context, self._params
        return StrategyResult(
            diagnostics=StrategyDiagnostics(notes=(f"t={time.time_ns()}",)),
        )
