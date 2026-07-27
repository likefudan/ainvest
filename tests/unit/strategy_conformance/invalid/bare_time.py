"""Bare ``from time import time; time()`` — fails no_future_data."""

from __future__ import annotations

from time import time
from typing import ClassVar

from pydantic import BaseModel

from ainvest.schemas.strategy import StrategyContext
from ainvest.strategies.definitions import StrategyDiagnostics, StrategyResult
from strategy_conformance.invalid._common import InvalidParams


class BareTimeStrategy:
    name: ClassVar[str] = "invalid_bare_time"
    version: ClassVar[str] = "1.0.0"
    params_model: ClassVar[type[BaseModel]] = InvalidParams

    def __init__(self, params: InvalidParams) -> None:
        self._params = params

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        del context, self._params
        _ = time()
        return StrategyResult(diagnostics=StrategyDiagnostics(notes=("bare-time",)))
