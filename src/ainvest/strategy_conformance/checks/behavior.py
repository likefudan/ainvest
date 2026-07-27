"""Determinism, future-data, timeout, exception, and Paper example checks."""

from __future__ import annotations

import ast
from pathlib import Path

from ainvest.strategies.definitions import StrategyDefinition
from ainvest.strategies.worker import WorkerFailureCode, WorkerLimits, WorkerStatus
from ainvest.strategies.worker.digests import digest_json
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
        "time.localtime",
        "time.gmtime",
        "time.monotonic",
        "time.perf_counter",
        "arrow.now",
        "arrow.utcnow",
        "pendulum.now",
    }
)


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
            return failed(
                "paper_example",
                code=ConformanceCode.PAPER_EXAMPLE,
                message=early.message,
                details=early.details,
            )
        assert record.result is not None
        return passed(
            "paper_example",
            message="Paper Trading example context evaluated successfully",
            details={"signal_count": str(len(record.result.signals))},
        )

    return timed(_run)


def _find_clock_calls(tree: ast.AST) -> tuple[str, ...]:
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        label = _call_label(node.func)
        if label is not None and label in _FORBIDDEN_CLOCK_CALLS:
            found.append(label)
    return tuple(sorted(set(found)))


def _call_label(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_label(node.value)
        if base is None:
            return node.attr
        return f"{base}.{node.attr}"
    return None


__all__ = [
    "check_determinism",
    "check_exception_handling",
    "check_no_future_data",
    "check_paper_example",
    "check_timeout_behavior",
]
