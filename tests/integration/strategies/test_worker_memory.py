"""A large data parent must not poison a healthy post-exec worker's RSS."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.live_safety
def test_large_parent_healthy_child_and_real_oom() -> None:
    unit = Path(__file__).resolve().parents[2] / "unit"
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(unit)}
    script = """
from ainvest.strategies.worker import (
    evaluate_in_worker, WorkerLimits, WorkerStatus, WorkerFailureCode,
)
from strategies.strategy_fixtures import make_context
from strategies.worker_probes import HealthyProbeStrategy, OomStrategy, definition_for
# Retain a touched 320 MiB resident allocation across both child launches.
ballast = bytearray(320 * 1024 * 1024)
ballast[::4096] = b'x' * len(ballast[::4096])
healthy = evaluate_in_worker(
    definition_for(HealthyProbeStrategy), params={'note': 'rss'}, context=make_context(),
    limits=WorkerLimits(wall_timeout_seconds=10), run_id='rss_healthy',
)
assert healthy.status is WorkerStatus.SUCCESS, healthy
oversized = evaluate_in_worker(
    definition_for(OomStrategy), params={}, context=make_context(),
    limits=WorkerLimits(memory_limit_bytes=80 * 1024 * 1024, wall_timeout_seconds=10),
    run_id='rss_oom_probe',
)
assert oversized.failure_code is WorkerFailureCode.OOM, oversized
assert ballast[0] == ord('x')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=45
    )
    assert result.returncode == 0, result.stderr + result.stdout
