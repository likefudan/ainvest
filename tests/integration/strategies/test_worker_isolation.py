"""Integration tests for strategy worker process isolation (P03-T4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from strategies.strategy_fixtures import make_context
from strategies.worker_probes import (
    HealthyProbeStrategy,
    OomStrategy,
    SecretAccessStrategy,
    TimeoutStrategy,
    definition_for,
)

from ainvest.strategies.definitions import StrategyDefinition
from ainvest.strategies.reference.moving_average.plugin import METADATA as MA_METADATA
from ainvest.strategies.reference.moving_average.strategy import MovingAverageStrategy
from ainvest.strategies.worker import (
    WorkerFailureCode,
    WorkerLimits,
    WorkerRunSpec,
    WorkerStatus,
    evaluate_in_worker,
    evaluate_many_in_workers,
)

_TESTS_UNIT = Path(__file__).resolve().parents[2] / "unit"


@pytest.fixture(autouse=True)
def _probe_path() -> None:
    current = os.environ.get("PYTHONPATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    root = str(_TESTS_UNIT)
    if root not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([root, *parts])


@pytest.mark.integration
def test_reference_ma_and_probes_isolated_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-integration-must-not-leak")
    ma = StrategyDefinition.from_type(MovingAverageStrategy, metadata=MA_METADATA)
    specs = [
        WorkerRunSpec(
            definition=definition_for(TimeoutStrategy),
            params={},
            context=make_context(),
            run_id="integ_timeout",
            limits=WorkerLimits(wall_timeout_seconds=0.5, memory_limit_bytes=512 * 1024 * 1024),
        ),
        WorkerRunSpec(
            definition=ma,
            params={"fast_window": 20, "slow_window": 50, "target_weight": "0.10"},
            context=make_context(),
            run_id="integ_ma",
            limits=WorkerLimits(wall_timeout_seconds=10.0),
        ),
        WorkerRunSpec(
            definition=definition_for(SecretAccessStrategy),
            params={},
            context=make_context(),
            run_id="integ_secret",
            limits=WorkerLimits(),
        ),
        WorkerRunSpec(
            definition=definition_for(OomStrategy),
            params={},
            context=make_context(),
            run_id="integ_oom",
            limits=WorkerLimits(memory_limit_bytes=80 * 1024 * 1024, wall_timeout_seconds=15.0),
        ),
        WorkerRunSpec(
            definition=definition_for(HealthyProbeStrategy),
            params={"note": "batch-ok"},
            context=make_context(),
            run_id="integ_healthy",
            limits=WorkerLimits(),
        ),
    ]
    records = evaluate_many_in_workers(specs)
    by_id = {r.run_id: r for r in records}
    assert by_id["integ_timeout"].failure_code is WorkerFailureCode.TIMEOUT
    assert by_id["integ_ma"].status is WorkerStatus.SUCCESS
    assert by_id["integ_secret"].failure_code is WorkerFailureCode.SECRET_ACCESS
    assert by_id["integ_oom"].failure_code is WorkerFailureCode.OOM
    assert by_id["integ_healthy"].status is WorkerStatus.SUCCESS
    # Secret material must not appear in host-side records.
    blob = " ".join(
        str(getattr(r, field))
        for r in records
        for field in ("failure_message", "parameter_digest", "input_digest", "package_version")
    )
    assert "sk-integration-must-not-leak" not in blob


@pytest.mark.integration
def test_single_worker_records_audit_metadata() -> None:
    record = evaluate_in_worker(
        StrategyDefinition.from_type(MovingAverageStrategy, metadata=MA_METADATA),
        params={"fast_window": 20, "slow_window": 50},
        context=make_context(),
        limits=WorkerLimits(wall_timeout_seconds=10.0),
        run_id="integ_audit_meta",
    )
    assert record.status is WorkerStatus.SUCCESS
    assert record.package_version
    assert record.plugin_version == MA_METADATA.plugin_version
    assert record.source_commit == MA_METADATA.source_commit
    assert record.parameter_digest.startswith("sha256:")
    assert record.input_digest.startswith("sha256:")
    assert record.duration_ms >= 0
