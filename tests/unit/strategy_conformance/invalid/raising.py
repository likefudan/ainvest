"""Raising strategy — fails exceptions."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from ainvest.schemas.strategy import StrategyContext
from ainvest.strategies.definitions import StrategyResult
from strategy_conformance.invalid._common import InvalidParams


class RaisingStrategy:
    name: ClassVar[str] = "invalid_raising"
    version: ClassVar[str] = "1.0.0"
    params_model: ClassVar[type[BaseModel]] = InvalidParams

    def __init__(self, params: InvalidParams) -> None:
        self._params = params

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        del context, self._params
        raise RuntimeError("deliberate evaluate failure")
