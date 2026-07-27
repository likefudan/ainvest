"""Shared helpers for conformance check implementations."""

from __future__ import annotations

import ast
import inspect
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ainvest.strategies.definitions import StrategyDefinition
from ainvest.strategies.worker import WorkerLimits, WorkerStatus, evaluate_in_worker
from ainvest.strategy_conformance.codes import ConformanceCode, ConformanceStatus
from ainvest.strategy_conformance.models import CheckResult

CheckFn = Callable[[StrategyDefinition], CheckResult]

DEFAULT_WORKER_LIMITS = WorkerLimits(
    wall_timeout_seconds=10.0,
    cpu_seconds=10.0,
    memory_limit_bytes=512 * 1024 * 1024,
    block_network=True,
    read_only_workdir=True,
)

FORBIDDEN_BROKER_MODULES: frozenset[str] = frozenset(
    {
        "ainvest.execution",
        "ainvest.approval",
        "robin_stocks",
        "robinhood",
    }
)

FORBIDDEN_NETWORK_MODULES: frozenset[str] = frozenset(
    {
        "socket",
        "httpx",
        "requests",
        "urllib",
        "urllib3",
        "aiohttp",
        "http.client",
    }
)


def passed(
    check_id: str,
    *,
    message: str,
    duration_ms: float = 0.0,
    details: Mapping[str, str] | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        status=ConformanceStatus.PASSED,
        code=ConformanceCode.OK,
        message=message,
        duration_ms=duration_ms,
        details=dict(details or {}),
    )


def failed(
    check_id: str,
    *,
    code: ConformanceCode,
    message: str,
    duration_ms: float = 0.0,
    details: Mapping[str, str] | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        status=ConformanceStatus.FAILED,
        code=code,
        message=message[:1024],
        duration_ms=duration_ms,
        details=dict(details or {}),
    )


def timed(fn: Callable[[], CheckResult]) -> CheckResult:
    started = time.perf_counter()
    result = fn()
    elapsed = (time.perf_counter() - started) * 1000.0
    return result.model_copy(update={"duration_ms": elapsed})


def run_in_worker(
    definition: StrategyDefinition,
    *,
    params: Mapping[str, Any] | None,
    context: Any,
    limits: WorkerLimits | None = None,
    run_id: str,
) -> Any:
    return evaluate_in_worker(
        definition,
        params=params or {},
        context=context,
        limits=limits or DEFAULT_WORKER_LIMITS,
        run_id=run_id,
    )


def require_worker_success(record: Any, *, check_id: str) -> CheckResult | None:
    if record.status is WorkerStatus.SUCCESS and record.result is not None:
        return None
    details: dict[str, str] = {}
    if record.failure_code is not None:
        details["worker_code"] = str(record.failure_code)
    message = record.failure_message or f"worker status={record.status}"
    return failed(
        check_id,
        code=ConformanceCode.WORKER_FAILURE,
        message=message,
        details=details,
    )


def strategy_source_path(definition: StrategyDefinition) -> str | None:
    try:
        path = inspect.getsourcefile(definition.strategy_type)
    except TypeError:
        return None
    return path


def module_imports_forbidden(path: str, forbidden: frozenset[str]) -> tuple[str, ...]:
    """Return forbidden module prefixes imported by ``path`` (AST only)."""
    try:
        source = Path(path).read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, ValueError):
        return ()
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _matches_forbidden(alias.name, forbidden):
                    found.append(alias.name)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and _matches_forbidden(node.module, forbidden)
        ):
            found.append(node.module)
    return tuple(sorted(set(found)))


def _matches_forbidden(module: str, forbidden: frozenset[str]) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden)


__all__ = [
    "DEFAULT_WORKER_LIMITS",
    "FORBIDDEN_BROKER_MODULES",
    "FORBIDDEN_NETWORK_MODULES",
    "CheckFn",
    "failed",
    "module_imports_forbidden",
    "passed",
    "require_worker_success",
    "run_in_worker",
    "strategy_source_path",
    "timed",
]
