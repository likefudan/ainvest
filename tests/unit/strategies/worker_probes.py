"""Probe strategies used only by worker isolation tests.

These plugins intentionally violate worker boundaries so the host can assert
fail-closed classification. They must never be registered as production entry
points.
"""

from __future__ import annotations

import os
import socket
import time
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from ainvest.schemas.strategy import StrategyContext
from ainvest.strategies.definitions import (
    PluginMetadata,
    StrategyDefinition,
    StrategyDiagnostics,
    StrategyParams,
    StrategyResult,
)


class ProbeParams(StrategyParams):
    """Empty params model for probe strategies."""

    note: str = Field(default="probe", min_length=1, max_length=64)


def _metadata(plugin_id: str) -> PluginMetadata:
    return PluginMetadata(
        plugin_id=plugin_id,
        plugin_version="1.0.0",
        ainvest_strategy_api=">=1.0.0,<2.0.0",
        source_commit="local",
        owner="ainvest",
        repository="tests/worker_probes",
    )


class TimeoutStrategy:
    name: ClassVar[str] = "probe_timeout"
    version: ClassVar[str] = "1.0.0"
    params_model: ClassVar[type[BaseModel]] = ProbeParams

    def __init__(self, params: ProbeParams) -> None:
        self._params = params

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        del context, self._params
        time.sleep(30)
        return StrategyResult(diagnostics=StrategyDiagnostics(notes=("unreachable",)))


class OomStrategy:
    name: ClassVar[str] = "probe_oom"
    version: ClassVar[str] = "1.0.0"
    params_model: ClassVar[type[BaseModel]] = ProbeParams

    def __init__(self, params: ProbeParams) -> None:
        self._params = params

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        del context, self._params
        from ainvest.strategies.worker.isolation import enforce_memory_allocation

        size = 200 * 1024 * 1024
        # Portable simulated memory limit (RLIMIT_AS often cannot be lowered on macOS).
        enforce_memory_allocation(size)
        blob = bytearray(size)
        return StrategyResult(diagnostics=StrategyDiagnostics(notes=(f"size={len(blob)}",)))


class SecretAccessStrategy:
    name: ClassVar[str] = "probe_secret"
    version: ClassVar[str] = "1.0.0"
    params_model: ClassVar[type[BaseModel]] = ProbeParams

    def __init__(self, params: ProbeParams) -> None:
        self._params = params

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        del context, self._params
        # Attempt to read a credential that must never be present in the worker.
        value = os.environ["OPENAI_API_KEY"]
        return StrategyResult(diagnostics=StrategyDiagnostics(notes=(value[:4],)))


class NetworkAccessStrategy:
    name: ClassVar[str] = "probe_network"
    version: ClassVar[str] = "1.0.0"
    params_model: ClassVar[type[BaseModel]] = ProbeParams

    def __init__(self, params: ProbeParams) -> None:
        self._params = params

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        del context, self._params
        socket.create_connection(("127.0.0.1", 9), timeout=0.2)
        return StrategyResult(diagnostics=StrategyDiagnostics(notes=("connected",)))


class InvalidOutputStrategy:
    name: ClassVar[str] = "probe_invalid"
    version: ClassVar[str] = "1.0.0"
    params_model: ClassVar[type[BaseModel]] = ProbeParams

    def __init__(self, params: ProbeParams) -> None:
        self._params = params

    def evaluate(self, context: StrategyContext) -> object:
        del context, self._params
        # Not a StrategyResult and not validatable as one.
        return {"not": "a-strategy-result", "signals": "bogus"}


class HealthyProbeStrategy:
    name: ClassVar[str] = "probe_healthy"
    version: ClassVar[str] = "1.0.0"
    params_model: ClassVar[type[BaseModel]] = ProbeParams

    def __init__(self, params: ProbeParams) -> None:
        self._params = params

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        del context
        return StrategyResult(
            diagnostics=StrategyDiagnostics(notes=(f"ok:{self._params.note}",)),
        )


def definition_for(strategy_type: type[Any], *, plugin_id: str | None = None) -> StrategyDefinition:
    """Build a StrategyDefinition for a probe class."""
    name = getattr(strategy_type, "name", None)
    if not isinstance(name, str):
        raise TypeError("probe strategy type must define a string name")
    return StrategyDefinition.from_type(
        strategy_type,
        metadata=_metadata(plugin_id or f"{name}_plugin"),
    )


__all__ = [
    "HealthyProbeStrategy",
    "InvalidOutputStrategy",
    "NetworkAccessStrategy",
    "OomStrategy",
    "ProbeParams",
    "SecretAccessStrategy",
    "TimeoutStrategy",
    "definition_for",
]
