"""Broker import strategy — fails broker_imports (AST)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from ainvest.schemas.strategy import StrategyContext
from ainvest.strategies.definitions import StrategyDiagnostics, StrategyResult
from strategy_conformance.invalid._common import InvalidParams


class BrokerImportStrategy:
    name: ClassVar[str] = "invalid_broker"
    version: ClassVar[str] = "1.0.0"
    params_model: ClassVar[type[BaseModel]] = InvalidParams

    def __init__(self, params: InvalidParams) -> None:
        self._params = params

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        del context, self._params
        # Parsed by conformance AST scan; not executed by the static check.
        import ainvest.execution as execution

        _ = execution
        return StrategyResult(diagnostics=StrategyDiagnostics(notes=("broker",)))
