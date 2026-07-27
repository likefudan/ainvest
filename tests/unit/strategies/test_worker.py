"""Unit tests for strategy worker isolation (P03-T4)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ainvest.strategies.definitions import StrategyDefinition
from ainvest.strategies.reference.moving_average.plugin import METADATA as MA_METADATA
from ainvest.strategies.reference.moving_average.strategy import MovingAverageStrategy
from ainvest.strategies.worker import (
    WorkerFailureCode,
    WorkerLimits,
    WorkerRunSpec,
    WorkerStatus,
    build_request,
    evaluate_in_worker,
    evaluate_many_in_workers,
    is_sensitive_env_key,
    run_worker,
    scrub_environ,
)
from ainvest.strategies.worker.digests import digest_json
from strategies.strategy_fixtures import make_context
from strategies.worker_probes import (
    HealthyProbeStrategy,
    InvalidOutputStrategy,
    NetworkAccessStrategy,
    OomStrategy,
    SecretAccessStrategy,
    TimeoutStrategy,
    definition_for,
)

_TESTS_UNIT = Path(__file__).resolve().parent.parent


def _ensure_probe_pythonpath() -> None:
    """Make probe modules importable inside the worker child process."""
    current = os.environ.get("PYTHONPATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    root = str(_TESTS_UNIT)
    if root not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([root, *parts])


@pytest.fixture(autouse=True)
def _probe_path() -> None:
    _ensure_probe_pythonpath()


def _ma_definition() -> StrategyDefinition:
    return StrategyDefinition.from_type(MovingAverageStrategy, metadata=MA_METADATA)


def _limits(**overrides: Any) -> WorkerLimits:
    return WorkerLimits(
        wall_timeout_seconds=float(overrides.get("wall_timeout_seconds", 5.0)),
        cpu_seconds=(
            None
            if overrides.get("cpu_seconds", 5.0) is None
            else float(overrides.get("cpu_seconds", 5.0))
        ),
        memory_limit_bytes=(
            None
            if overrides.get("memory_limit_bytes", 256 * 1024 * 1024) is None
            else int(overrides.get("memory_limit_bytes", 256 * 1024 * 1024))
        ),
        block_network=bool(overrides.get("block_network", True)),
        read_only_workdir=bool(overrides.get("read_only_workdir", True)),
    )


@pytest.mark.unit
def test_scrub_environ_removes_secrets() -> None:
    cleaned = scrub_environ(
        {
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-not-a-real-secret-value",
            "TELEGRAM_BOT_TOKEN": "123:ABC",
            "DATABASE_URL": "postgresql://user:pass@localhost/db",
            "AINVEST_WORKER_BLOCK_NETWORK": "1",
            "SAFE_FLAG": "ok",
        }
    )
    assert "PATH" in cleaned
    assert "AINVEST_WORKER_BLOCK_NETWORK" in cleaned
    assert "OPENAI_API_KEY" not in cleaned
    assert "TELEGRAM_BOT_TOKEN" not in cleaned
    assert "DATABASE_URL" not in cleaned
    assert is_sensitive_env_key("ROBINHOOD_TOKEN")
    assert not is_sensitive_env_key("PATH")


@pytest.mark.unit
def test_moving_average_succeeds_in_worker() -> None:
    record = evaluate_in_worker(
        _ma_definition(),
        params={"fast_window": 20, "slow_window": 50, "target_weight": "0.10"},
        context=make_context(),
        limits=_limits(),
        run_id="run_ma_success_001",
    )
    assert record.status is WorkerStatus.SUCCESS
    assert record.result is not None
    assert record.package_version
    assert record.source_commit == MA_METADATA.source_commit
    assert record.parameter_digest.startswith("sha256:")
    assert record.input_digest.startswith("sha256:")
    assert record.duration_ms >= 0
    assert record.failure_code is None


@pytest.mark.unit
def test_timeout_fails_closed() -> None:
    record = evaluate_in_worker(
        definition_for(TimeoutStrategy),
        params={},
        context=make_context(),
        limits=_limits(wall_timeout_seconds=0.4, memory_limit_bytes=512 * 1024 * 1024),
        run_id="run_timeout_001",
    )
    assert record.status is WorkerStatus.FAILED
    assert record.failure_code is WorkerFailureCode.TIMEOUT


@pytest.mark.unit
def test_oom_or_simulated_memory_limit_fails_closed() -> None:
    # Soft RSS watchdog / RLIMIT: keep limit above interpreter baseline but below
    # the probe's 200 MiB allocation.
    record = evaluate_in_worker(
        definition_for(OomStrategy),
        params={},
        context=make_context(),
        limits=_limits(memory_limit_bytes=80 * 1024 * 1024, wall_timeout_seconds=10.0),
        run_id="run_oom_001",
    )
    assert record.status is WorkerStatus.FAILED
    assert record.failure_code is WorkerFailureCode.OOM


@pytest.mark.unit
def test_secret_access_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-secret-value")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "000000:fake-token-for-test")
    record = evaluate_in_worker(
        definition_for(SecretAccessStrategy),
        params={},
        context=make_context(),
        limits=_limits(),
        run_id="run_secret_001",
    )
    assert record.status is WorkerStatus.FAILED
    assert record.failure_code is WorkerFailureCode.SECRET_ACCESS
    assert record.failure_message is not None
    assert "sk-not-a-real-secret-value" not in record.failure_message
    assert "fake-token-for-test" not in (record.failure_message or "")


@pytest.mark.unit
def test_network_access_fails_closed() -> None:
    record = evaluate_in_worker(
        definition_for(NetworkAccessStrategy),
        params={},
        context=make_context(),
        limits=_limits(block_network=True),
        run_id="run_network_001",
    )
    assert record.status is WorkerStatus.FAILED
    assert record.failure_code is WorkerFailureCode.NETWORK_DENIED


@pytest.mark.unit
def test_invalid_strategy_result_fails_closed() -> None:
    record = evaluate_in_worker(
        definition_for(InvalidOutputStrategy),
        params={},
        context=make_context(),
        limits=_limits(),
        run_id="run_invalid_001",
    )
    assert record.status is WorkerStatus.FAILED
    assert record.failure_code is WorkerFailureCode.EVALUATION_ERROR


@pytest.mark.unit
def test_invalid_stdout_json_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    request = build_request(
        definition=definition_for(HealthyProbeStrategy),
        params={},
        context=make_context(),
        limits=_limits(),
        run_id="run_bad_stdout_001",
    )

    class _FakeProc:
        returncode = 0

        def communicate(
            self, input: str | None = None, timeout: float | None = None
        ) -> tuple[str, str]:
            del input, timeout
            return "this-is-not-json\n", ""

        def kill(self) -> None:
            return None

    def _fake_popen(*args: Any, **kwargs: Any) -> _FakeProc:
        del args, kwargs
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    record = run_worker(request)
    assert record.status is WorkerStatus.FAILED
    assert record.failure_code is WorkerFailureCode.INVALID_OUTPUT


@pytest.mark.unit
def test_batch_continues_after_one_failure() -> None:
    specs = [
        WorkerRunSpec(
            definition=definition_for(TimeoutStrategy),
            params={},
            context=make_context(),
            run_id="run_batch_timeout",
            limits=_limits(wall_timeout_seconds=0.4),
        ),
        WorkerRunSpec(
            definition=definition_for(HealthyProbeStrategy),
            params={"note": "survived"},
            context=make_context(),
            run_id="run_batch_healthy",
            limits=_limits(),
        ),
        WorkerRunSpec(
            definition=definition_for(NetworkAccessStrategy),
            params={},
            context=make_context(),
            run_id="run_batch_network",
            limits=_limits(),
        ),
    ]
    records = evaluate_many_in_workers(specs)
    assert len(records) == 3
    assert records[0].failure_code is WorkerFailureCode.TIMEOUT
    assert records[1].status is WorkerStatus.SUCCESS
    assert records[1].result is not None
    assert records[1].result.diagnostics.notes == ("ok:survived",)
    assert records[2].failure_code is WorkerFailureCode.NETWORK_DENIED


@pytest.mark.unit
def test_request_digests_are_stable() -> None:
    definition = definition_for(HealthyProbeStrategy)
    context = make_context()
    req_a = build_request(
        definition=definition,
        params={"note": "stable"},
        context=context,
        run_id="run_digest_a",
        version="0.1.0",
    )
    req_b = build_request(
        definition=definition,
        params={"note": "stable"},
        context=context,
        run_id="run_digest_b",
        version="0.1.0",
    )
    assert req_a.parameter_digest == req_b.parameter_digest
    assert req_a.input_digest == req_b.input_digest
    assert req_a.parameter_digest == digest_json(req_a.strategy.params)


@pytest.mark.unit
def test_worker_request_is_json_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parent must send JSON text on stdin — never pickled objects."""
    captured: dict[str, Any] = {}

    class _FakeProc:
        returncode = 0
        pid = 12345

        def communicate(
            self, input: str | None = None, timeout: float | None = None
        ) -> tuple[str, str]:
            captured["stdin"] = input
            del timeout
            # Minimal valid failure response so the runner returns cleanly.
            payload = {
                "schema_version": "1.0",
                "run_id": "run_json_only_001",
                "status": "FAILED",
                "package_version": "0.1.0",
                "strategy_name": "probe_healthy",
                "strategy_version": "1.0.0",
                "plugin_version": "1.0.0",
                "source_commit": "local",
                "parameter_digest": "sha256:" + ("ab" * 32),
                "input_digest": "sha256:" + ("cd" * 32),
                "failure": {
                    "schema_version": "1.0",
                    "code": "WORKER_EVALUATION_ERROR",
                    "message": "stub",
                    "duration_ms": 1.0,
                },
            }
            return json.dumps(payload) + "\n", ""

        def kill(self) -> None:
            return None

    def _fake_popen(*args: Any, **kwargs: Any) -> _FakeProc:
        captured["env"] = kwargs.get("env")
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak-into-child")
    request = build_request(
        definition=definition_for(HealthyProbeStrategy),
        params={},
        context=make_context(),
        limits=_limits(),
        run_id="run_json_only_001",
    )
    run_worker(request)
    assert isinstance(captured["stdin"], str)
    parsed = json.loads(captured["stdin"])
    assert parsed["schema_version"] == "1.0"
    assert "OPENAI_API_KEY" not in (captured["env"] or {})
    assert "sk-should-not-leak-into-child" not in json.dumps(captured["env"] or {})
