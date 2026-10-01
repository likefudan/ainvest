"""Worker crash/timeout faults cannot escape into money-moving effects."""

from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import ClassVar

import pytest
from strategies.strategy_fixtures import make_context
from strategies.worker_probes import ProbeParams, TimeoutStrategy, definition_for

from ainvest.schemas.strategy import StrategyContext
from ainvest.strategies.definitions import StrategyResult
from ainvest.strategies.worker import (
    WorkerFailureCode,
    WorkerLimits,
    WorkerStatus,
    evaluate_in_worker,
)

from .conftest import FaultEvidence, assert_fault_evidence

_TESTS_UNIT = Path(__file__).resolve().parents[1] / "unit"


class CrashStrategy:
    name: ClassVar[str] = "fault_process_crash"
    version: ClassVar[str] = "1.0.0"
    params_model: ClassVar[type[ProbeParams]] = ProbeParams

    def __init__(self, params: ProbeParams) -> None:
        del params

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        del context
        os.kill(os.getpid(), signal.SIGABRT)
        raise AssertionError("unreachable")


@pytest.fixture(autouse=True)
def _worker_probe_import_path() -> None:
    current = os.environ.get("PYTHONPATH", "")
    parts = [part for part in current.split(os.pathsep) if part]
    roots = (str(_TESTS_UNIT.parent), str(_TESTS_UNIT))
    os.environ["PYTHONPATH"] = os.pathsep.join([*roots, *[p for p in parts if p not in roots]])


@pytest.mark.integration
def test_worker_timeout_is_terminal_for_the_run_and_emits_no_signal() -> None:
    record = evaluate_in_worker(
        definition_for(TimeoutStrategy),
        params={},
        context=make_context(),
        limits=WorkerLimits(wall_timeout_seconds=0.5, memory_limit_bytes=512 * 1024 * 1024),
        run_id="fault_worker_timeout",
    )
    evidence = FaultEvidence(
        final_state=record.status.value,
        audit_or_result=(record.failure_code.value if record.failure_code else "missing",),
        external_calls=1,
        funds_effect="unchanged",
    )

    assert record.status is WorkerStatus.FAILED
    assert record.failure_code is WorkerFailureCode.TIMEOUT
    assert record.result is None
    assert_fault_evidence(evidence, state="FAILED", calls=1, funds="unchanged")


@pytest.mark.integration
def test_worker_process_crash_is_classified_without_a_funds_effect() -> None:
    record = evaluate_in_worker(
        definition_for(CrashStrategy),
        params={},
        context=make_context(),
        limits=WorkerLimits(wall_timeout_seconds=5.0),
        run_id="fault_worker_crash",
    )
    evidence = FaultEvidence(
        final_state=record.status.value,
        audit_or_result=(record.failure_code.value if record.failure_code else "missing",),
        external_calls=1,
        funds_effect="unchanged",
    )

    assert record.failure_code is WorkerFailureCode.CRASH
    assert record.exit_code in {-signal.SIGABRT, 128 + signal.SIGABRT}
    assert record.result is None
    assert_fault_evidence(evidence, state="FAILED", calls=1, funds="unchanged")
