"""Regression tests for PR #63 review fixes on strategy workers."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from ainvest.strategies.worker.child import _classify_exception
from ainvest.strategies.worker.codes import WorkerFailureCode, WorkerStatus
from ainvest.strategies.worker.env import (
    SecretEnvironmentAccessError,
    bind_worker_paths,
    install_secret_env_guard,
    scrub_environ,
    uninstall_secret_env_guard,
)
from ainvest.strategies.worker.isolation import (
    cleanup_worker_workdir,
    create_worker_workdir,
    prepare_workdir,
)
from ainvest.strategies.worker.protocol import WorkerLimits
from ainvest.strategies.worker.runner import (
    _classify_exit,
    _reap_after_timeout,
    build_request,
    evaluate_in_worker,
    run_worker,
)
from strategies.strategy_fixtures import make_context
from strategies.worker_probes import (
    BenignKeyErrorStrategy,
    HealthyProbeStrategy,
    HomePathStrategy,
    definition_for,
)

_TESTS_UNIT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _probe_path() -> None:
    current = os.environ.get("PYTHONPATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    root = str(_TESTS_UNIT)
    if root not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([root, *parts])


@pytest.fixture(autouse=True)
def _no_lingering_secret_guard() -> Generator[None, None, None]:
    uninstall_secret_env_guard()
    yield
    uninstall_secret_env_guard()


@pytest.mark.unit
def test_scrub_drops_host_home_and_tmp() -> None:
    cleaned = scrub_environ(
        {
            "PATH": "/usr/bin",
            "HOME": "/Users/host/.secret-home",
            "PWD": "/Users/host/project",
            "TMPDIR": "/Users/host/tmp",
            "TMP": "/Users/host/tmp",
            "TEMP": "/Users/host/tmp",
        }
    )
    assert "HOME" not in cleaned
    assert "PWD" not in cleaned
    assert "TMPDIR" not in cleaned
    assert "PATH" in cleaned


@pytest.mark.unit
def test_bind_worker_paths_points_home_at_workdir(tmp_path: Path) -> None:
    workdir = tmp_path / "ainvest-strategy-worker-test"
    workdir.mkdir()
    environ: dict[str, str] = {"PATH": "/usr/bin"}
    bind_worker_paths(environ, workdir)
    assert environ["HOME"] == str(workdir / "home")
    assert environ["PWD"] == str(workdir)
    assert environ["TMPDIR"] == str(workdir / "tmp")
    assert (workdir / "home").is_dir()
    assert (workdir / "tmp").is_dir()


@pytest.mark.unit
def test_worker_home_isolated_from_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    host_home = tmp_path / "host-home"
    host_home.mkdir()
    (host_home / ".env").write_text("OPENAI_API_KEY=sk-should-not-be-visible\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(host_home))
    record = evaluate_in_worker(
        definition_for(HomePathStrategy),
        params={},
        context=make_context(),
        run_id="run_home_iso_001",
    )
    assert record.status is WorkerStatus.SUCCESS
    assert record.result is not None
    notes = record.result.diagnostics.notes
    assert any(n.startswith("home=") for n in notes)
    home_note = next(n for n in notes if n.startswith("home="))
    assert str(host_home) not in home_note
    assert "ainvest-strategy-worker-" in home_note
    assert "env_exists=False" in notes


@pytest.mark.unit
def test_cleanup_removes_readonly_workdir() -> None:
    workdir = create_worker_workdir()
    prepare_workdir(workdir, read_only=True)
    assert workdir.exists()
    cleanup_worker_workdir(workdir)
    assert not workdir.exists()


@pytest.mark.unit
def test_run_worker_cleans_workdir_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[Path] = []
    real_create = create_worker_workdir

    def _tracking_create() -> Path:
        # Create under tmp_path for easier assertions.
        path = Path(tempfile.mkdtemp(prefix="ainvest-strategy-worker-", dir=tmp_path))
        (path / "home").mkdir()
        (path / "tmp").mkdir()
        created.append(path)
        return path

    monkeypatch.setattr(
        "ainvest.strategies.worker.runner.create_worker_workdir",
        _tracking_create,
    )
    del real_create
    record = evaluate_in_worker(
        definition_for(HealthyProbeStrategy),
        params={"note": "cleanup"},
        context=make_context(),
        run_id="run_cleanup_001",
    )
    assert record.status is WorkerStatus.SUCCESS
    assert created
    assert not created[0].exists()


@pytest.mark.unit
def test_classify_exit_crash_signals() -> None:
    classified = _classify_exit(
        returncode=-signal.SIGSEGV,
        timed_out=False,
        memory_limit=None,
    )
    assert classified is not None
    code, message = classified
    assert code is WorkerFailureCode.CRASH
    assert "signal" in message

    classified = _classify_exit(
        returncode=128 + signal.SIGABRT,
        timed_out=False,
        memory_limit=None,
    )
    assert classified is not None
    code, _message = classified
    assert code is WorkerFailureCode.CRASH

    # Timed-out SIGKILL stays TIMEOUT.
    classified = _classify_exit(
        returncode=-signal.SIGKILL,
        timed_out=True,
        memory_limit=1024,
    )
    assert classified is not None
    code, _message = classified
    assert code is WorkerFailureCode.TIMEOUT


@pytest.mark.unit
def test_reap_after_timeout_never_raises() -> None:
    """``communicate`` and ``wait`` TimeoutExpired must not escape the reap helper."""
    calls = {"communicate": 0, "wait": 0}

    class _StickyProc:
        returncode: int | None = None
        pid = 4242

        def communicate(
            self, input: str | None = None, timeout: float | None = None
        ) -> tuple[str, str]:
            del input
            calls["communicate"] += 1
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout or 1)

        def poll(self) -> int | None:
            # Stay alive so the wait fallback is exercised.
            return None

        def wait(self, timeout: float | None = None) -> int:
            calls["wait"] += 1
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout or 1)

        def kill(self) -> None:
            return None

    proc = _StickyProc()
    stdout, _stderr, code = _reap_after_timeout(proc)  # type: ignore[arg-type]
    assert stdout == ""
    assert code is None  # never reaped; still fail-closed without raising
    assert calls["communicate"] >= 2
    assert calls["wait"] == 1


@pytest.mark.unit
def test_timeout_reap_returns_timeout_not_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nested TimeoutExpired during reap must yield TIMEOUT, not INTERNAL_ERROR."""

    class _HangProc:
        returncode: int | None = None
        pid = 9991

        def communicate(
            self, input: str | None = None, timeout: float | None = None
        ) -> tuple[str, str]:
            del input
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout or 1)

        def poll(self) -> int | None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.returncode = -signal.SIGKILL
            return -signal.SIGKILL

        def kill(self) -> None:
            self.returncode = -signal.SIGKILL

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _HangProc())
    monkeypatch.setattr(
        "ainvest.strategies.worker.runner._kill_process_group",
        lambda proc: setattr(proc, "returncode", -signal.SIGKILL),
    )

    def _reap(proc: Any) -> tuple[str, str, int | None]:
        proc.returncode = -signal.SIGKILL
        return "", "", proc.returncode

    monkeypatch.setattr("ainvest.strategies.worker.runner._reap_after_timeout", _reap)

    request = build_request(
        definition=definition_for(HealthyProbeStrategy),
        params={},
        context=make_context(),
        limits=WorkerLimits(wall_timeout_seconds=0.1),
        run_id="run_reap_timeout_001",
    )
    record = run_worker(request)
    assert record.status is WorkerStatus.FAILED
    assert record.failure_code is WorkerFailureCode.TIMEOUT


@pytest.mark.unit
def test_benign_keyerror_is_evaluation_error_not_secret_access() -> None:
    record = evaluate_in_worker(
        definition_for(BenignKeyErrorStrategy),
        params={},
        context=make_context(),
        run_id="run_benign_key_001",
    )
    assert record.status is WorkerStatus.FAILED
    assert record.failure_code is WorkerFailureCode.EVALUATION_ERROR


@pytest.mark.unit
def test_secret_access_error_classified() -> None:
    code, message = _classify_exception(SecretEnvironmentAccessError("OPENAI_API_KEY"))
    assert code is WorkerFailureCode.SECRET_ACCESS
    assert "OPENAI_API_KEY" in message


@pytest.mark.unit
def test_secret_env_guard_raises_for_denied_keys() -> None:
    install_secret_env_guard()
    try:
        with pytest.raises(SecretEnvironmentAccessError):
            _ = os.environ["OPENAI_API_KEY"]
    finally:
        uninstall_secret_env_guard()
