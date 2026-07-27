"""Determinism, future-data, timeout, exception, and Paper example checks."""

from __future__ import annotations

import ast
from pathlib import Path

from ainvest.strategies.definitions import StrategyDefinition
from ainvest.strategies.worker import (
    WorkerFailureCode,
    WorkerLimits,
    WorkerStatus,
    digest_json,
)
from ainvest.strategy_conformance.checks._util import (
    DEFAULT_WORKER_LIMITS,
    failed,
    passed,
    require_worker_success,
    run_in_worker,
    strategy_source_path,
    timed,
)
from ainvest.strategy_conformance.codes import ConformanceCode
from ainvest.strategy_conformance.fixtures import make_paper_context
from ainvest.strategy_conformance.models import CheckResult

# Fully-qualified wall-clock call labels (after import alias resolution).
_FORBIDDEN_CLOCK_CALLS: frozenset[str] = frozenset(
    {
        "datetime.now",
        "datetime.utcnow",
        "datetime.today",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.today",
        "datetime.date.today",
        "date.today",
        "time.time",
        "time.time_ns",
        "time.localtime",
        "time.gmtime",
        "time.monotonic",
        "time.perf_counter",
        "arrow.now",
        "arrow.utcnow",
        "pendulum.now",
    }
)

# Names imported from wall-clock modules that are forbidden when called bare.
_FORBIDDEN_TIME_IMPORTS: frozenset[str] = frozenset(
    {
        "time",
        "time_ns",
        "localtime",
        "gmtime",
        "monotonic",
        "perf_counter",
    }
)
_FORBIDDEN_DATETIME_IMPORTS: frozenset[str] = frozenset({"now", "utcnow", "today"})


def check_determinism(definition: StrategyDefinition) -> CheckResult:
    """Repeat isolated evaluations with fixed inputs; digests must match."""

    def _run() -> CheckResult:
        context = make_paper_context(
            strategy_name=definition.name,
            strategy_version=definition.version,
        )
        digests: list[str] = []
        for index in range(2):
            record = run_in_worker(
                definition,
                params={},
                context=context,
                run_id=f"conformance-determinism-{index}",
            )
            early = require_worker_success(record, check_id="determinism")
            if early is not None:
                return early
            assert record.result is not None
            digests.append(digest_json(record.result.model_dump(mode="json")))
        if digests[0] != digests[1]:
            return failed(
                "determinism",
                code=ConformanceCode.NONDETERMINISTIC,
                message="repeated evaluations produced different StrategyResult digests",
                details={"digest_a": digests[0], "digest_b": digests[1]},
            )
        return passed(
            "determinism",
            message="repeated fixed-input evaluations are byte-stable",
            details={"result_digest": digests[0]},
        )

    return timed(_run)


def check_no_future_data(definition: StrategyDefinition) -> CheckResult:
    """Fail closed when strategy source uses wall-clock APIs."""

    def _run() -> CheckResult:
        path = strategy_source_path(definition)
        if path is None:
            return failed(
                "no_future_data",
                code=ConformanceCode.FUTURE_DATA,
                message="unable to locate strategy source for clock API scan",
            )
        try:
            source = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, ValueError) as exc:
            return failed(
                "no_future_data",
                code=ConformanceCode.FUTURE_DATA,
                message=f"unable to parse strategy source: {exc}",
            )
        offenders = _find_clock_calls(tree)
        if offenders:
            return failed(
                "no_future_data",
                code=ConformanceCode.FUTURE_DATA,
                message="strategy source uses wall-clock APIs; use context.as_of only",
                details={"offenders": ",".join(offenders[:8])},
            )
        return passed(
            "no_future_data",
            message="no wall-clock APIs found in strategy source",
            details={"source": path},
        )

    return timed(_run)


def check_timeout_behavior(definition: StrategyDefinition) -> CheckResult:
    """Strategy must finish within worker wall timeout (not hang)."""

    def _run() -> CheckResult:
        limits = WorkerLimits(
            wall_timeout_seconds=5.0,
            cpu_seconds=5.0,
            memory_limit_bytes=DEFAULT_WORKER_LIMITS.memory_limit_bytes,
            block_network=True,
            read_only_workdir=True,
        )
        context = make_paper_context(
            strategy_name=definition.name,
            strategy_version=definition.version,
        )
        record = run_in_worker(
            definition,
            params={},
            context=context,
            limits=limits,
            run_id="conformance-timeout",
        )
        if (
            record.status is WorkerStatus.FAILED
            and record.failure_code is WorkerFailureCode.TIMEOUT
        ):
            return failed(
                "timeout",
                code=ConformanceCode.TIMEOUT,
                message=record.failure_message or "strategy exceeded worker wall timeout",
            )
        early = require_worker_success(record, check_id="timeout")
        if early is not None:
            return early
        return passed(
            "timeout",
            message="strategy completed within the conformance wall timeout",
            details={"wall_timeout_seconds": "5.0"},
        )

    return timed(_run)


def check_exception_handling(definition: StrategyDefinition) -> CheckResult:
    """Evaluation must not crash the worker or raise unhandled evaluation errors."""

    def _run() -> CheckResult:
        context = make_paper_context(
            strategy_name=definition.name,
            strategy_version=definition.version,
        )
        record = run_in_worker(
            definition,
            params={},
            context=context,
            run_id="conformance-exceptions",
        )
        if record.status is WorkerStatus.FAILED and record.failure_code in {
            WorkerFailureCode.CRASH,
            WorkerFailureCode.EVALUATION_ERROR,
        }:
            return failed(
                "exceptions",
                code=ConformanceCode.EXCEPTION,
                message=record.failure_message or "strategy raised during evaluation",
                details={"worker_code": str(record.failure_code)},
            )
        early = require_worker_success(record, check_id="exceptions")
        if early is not None:
            return early
        return passed("exceptions", message="strategy evaluation completed without exceptions")

    return timed(_run)


def check_paper_example(definition: StrategyDefinition) -> CheckResult:
    """Paper Trading fixture context must produce a valid StrategyResult."""

    def _run() -> CheckResult:
        context = make_paper_context(
            strategy_name=definition.name,
            strategy_version=definition.version,
        )
        record = run_in_worker(
            definition,
            params={},
            context=context,
            run_id="conformance-paper",
        )
        early = require_worker_success(record, check_id="paper_example")
        if early is not None:
            # Preserve root-cause worker/conformance code (do not overwrite).
            return early
        assert record.result is not None
        return passed(
            "paper_example",
            message="Paper Trading example context evaluated successfully",
            details={"signal_count": str(len(record.result.signals))},
        )

    return timed(_run)


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    """Map local names to fully-qualified wall-clock call labels when applicable."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                if alias.name == "time" or alias.name.startswith("time."):
                    aliases[local] = "time"
                elif alias.name in {"datetime", "arrow", "pendulum"} or alias.name.startswith(
                    "datetime."
                ):
                    aliases[local] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                if module == "time" and alias.name in _FORBIDDEN_TIME_IMPORTS:
                    aliases[local] = f"time.{alias.name}"
                elif (
                    module in {"datetime", "datetime.datetime"}
                    and alias.name in _FORBIDDEN_DATETIME_IMPORTS
                ):
                    aliases[local] = f"datetime.{alias.name}"
                elif module == "datetime.date" and alias.name == "today":
                    aliases[local] = "datetime.date.today"
                elif module == "datetime" and alias.name == "date":
                    aliases[local] = "datetime.date"
                elif module == "datetime" and alias.name == "datetime":
                    aliases[local] = "datetime.datetime"
    return aliases


def _find_clock_calls(tree: ast.AST) -> tuple[str, ...]:
    aliases = _import_aliases(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        label = _resolved_call_label(node.func, aliases)
        if label is not None and label in _FORBIDDEN_CLOCK_CALLS:
            found.append(label)
    return tuple(sorted(set(found)))


def _resolved_call_label(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        # Bare call: `time()` after `from time import time`.
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name):
            base = aliases.get(node.value.id, node.value.id)
            return f"{base}.{node.attr}"
        base_label = _resolved_call_label(node.value, aliases)
        if base_label is None:
            return node.attr
        return f"{base_label}.{node.attr}"
    return None


__all__ = [
    "check_determinism",
    "check_exception_handling",
    "check_no_future_data",
    "check_paper_example",
    "check_timeout_behavior",
]
