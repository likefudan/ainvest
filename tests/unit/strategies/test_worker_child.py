"""In-process unit tests for worker child helpers (coverage + fail-closed)."""

from __future__ import annotations

import os
from collections.abc import Generator, Iterator

import pytest

from ainvest.strategies.worker.child import evaluate_request, os_environ_clear_and_update
from ainvest.strategies.worker.codes import WorkerFailureCode, WorkerStatus
from ainvest.strategies.worker.env import (
    assert_no_secrets_in_environ,
    scrub_environ,
    uninstall_secret_env_guard,
)
from ainvest.strategies.worker.isolation import (
    NetworkAccessDeniedError,
    block_network,
    enforce_memory_allocation,
    restore_network,
)
from ainvest.strategies.worker.runner import build_request
from strategies.strategy_fixtures import make_context
from strategies.worker_probes import HealthyProbeStrategy, NetworkAccessStrategy, definition_for


@pytest.fixture(autouse=True)
def _no_lingering_secret_guard() -> Generator[None, None, None]:
    uninstall_secret_env_guard()
    yield
    uninstall_secret_env_guard()


@pytest.fixture
def scrubbed_environ() -> Iterator[None]:
    """Match child startup: evaluate only after secrets are scrubbed."""
    original = dict(os.environ)
    os_environ_clear_and_update(scrub_environ())
    try:
        yield
    finally:
        os_environ_clear_and_update(original)


@pytest.mark.unit
def test_evaluate_request_success_in_process(scrubbed_environ: None) -> None:
    del scrubbed_environ
    request = build_request(
        definition=definition_for(HealthyProbeStrategy),
        params={"note": "inproc"},
        context=make_context(),
        run_id="run_inproc_ok_001",
    )
    response = evaluate_request(request)
    assert response.status is WorkerStatus.SUCCESS
    assert response.success is not None
    assert response.success.result.diagnostics.notes == ("ok:inproc",)


@pytest.mark.unit
def test_evaluate_request_network_denied_in_process(scrubbed_environ: None) -> None:
    del scrubbed_environ
    block_network()
    try:
        request = build_request(
            definition=definition_for(NetworkAccessStrategy),
            params={},
            context=make_context(),
            run_id="run_inproc_net_001",
        )
        response = evaluate_request(request)
        assert response.status is WorkerStatus.FAILED
        assert response.failure is not None
        assert response.failure.code is WorkerFailureCode.NETWORK_DENIED
    finally:
        restore_network()


@pytest.mark.unit
def test_enforce_memory_allocation_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AINVEST_WORKER_MEMORY_LIMIT_BYTES", str(1024))
    with pytest.raises(MemoryError):
        enforce_memory_allocation(2048)


@pytest.mark.unit
def test_assert_no_secrets_detects_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-be-absent")
    with pytest.raises(RuntimeError, match="sensitive environment"):
        assert_no_secrets_in_environ()
    cleaned = scrub_environ()
    assert "OPENAI_API_KEY" not in cleaned
    os.environ.pop("OPENAI_API_KEY", None)


@pytest.mark.unit
def test_blocked_socket_raises() -> None:
    block_network()
    try:
        import socket

        with pytest.raises(NetworkAccessDeniedError):
            socket.create_connection(("127.0.0.1", 9), timeout=0.1)
    finally:
        restore_network()
