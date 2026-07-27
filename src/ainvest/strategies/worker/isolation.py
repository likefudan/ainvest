"""CPU, memory, filesystem, and network isolation for strategy workers.

Network isolation expectations
------------------------------
Python cannot reliably create a network namespace on every host OS. This module
applies a best-effort in-process socket block so strategy code that calls
``socket`` / ``create_connection`` fails closed inside the worker.

Production and CI deployments SHOULD additionally run strategy workers with
OS/container network isolation, for example:

- Linux: ``docker run --network=none``, a dedicated network namespace, or
  seccomp/AppArmor profiles that deny ``connect`` / ``sendto``.
- macOS (local dev): rely on the in-process socket block; do not treat it as a
  kernel capability sandbox.
- Kubernetes: ``hostNetwork: false`` plus a NetworkPolicy that denies egress
  for the strategy-worker pod.

Memory limits on macOS often cannot lower ``RLIMIT_AS``. This module therefore
combines best-effort rlimits, an RSS watchdog, and a simulated allocation guard
so oversized ``bytearray`` requests fail closed with ``MemoryError``.
"""

from __future__ import annotations

import os
import signal
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ainvest.strategies.worker.codes import (
    ENV_BLOCK_NETWORK,
    ENV_CPU_SECONDS,
    ENV_MEMORY_LIMIT,
    ENV_READ_ONLY_WORKDIR,
    ENV_WALL_TIMEOUT,
    ENV_WORKDIR,
)
from ainvest.strategies.worker.protocol import WorkerLimits

_ORIGINAL_SOCKET: type[socket.socket] | None = None
_ORIGINAL_CREATE_CONNECTION: Callable[..., Any] | None = None
_MEMORY_WATCHDOG: threading.Thread | None = None
_MEMORY_WATCHDOG_STOP = threading.Event()


@dataclass(frozen=True, slots=True)
class AppliedLimits:
    """What isolation controls were successfully applied in this process."""

    cpu_rlimit_applied: bool
    memory_rlimit_applied: bool
    memory_watchdog_applied: bool
    network_blocked: bool
    read_only_workdir: Path | None


def _rss_bytes() -> int:
    """Return approximate current peak RSS in bytes (platform-normalized)."""
    import resource

    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes; macOS reports bytes.
    platform = sys.platform
    if platform.startswith("linux"):
        return int(usage) * 1024
    return int(usage)


def _set_rlimit(resource_name: str, limit: int) -> bool:
    import resource

    attr = getattr(resource, resource_name, None)
    if attr is None:
        return False
    try:
        resource.setrlimit(attr, (limit, limit))
    except (ValueError, OSError):
        return False
    return True


def apply_cpu_limit(cpu_seconds: float | None) -> bool:
    """Apply ``RLIMIT_CPU`` when supported. Returns whether it was applied."""
    if cpu_seconds is None:
        return False
    limit = max(1, int(cpu_seconds) if float(cpu_seconds).is_integer() else int(cpu_seconds) + 1)
    return _set_rlimit("RLIMIT_CPU", limit)


def apply_memory_rlimit(memory_limit_bytes: int | None) -> bool:
    """Try ``RLIMIT_AS`` then ``RLIMIT_DATA``. Often unavailable on macOS."""
    if memory_limit_bytes is None:
        return False
    if _set_rlimit("RLIMIT_AS", memory_limit_bytes):
        return True
    return _set_rlimit("RLIMIT_DATA", memory_limit_bytes)


def start_memory_watchdog(memory_limit_bytes: int | None) -> bool:
    """Kill this process when peak RSS exceeds the configured soft limit."""
    global _MEMORY_WATCHDOG
    if memory_limit_bytes is None:
        return False
    if _MEMORY_WATCHDOG is not None and _MEMORY_WATCHDOG.is_alive():
        return True

    limit = int(memory_limit_bytes)
    pid = os.getpid()
    _MEMORY_WATCHDOG_STOP.clear()

    def _watch() -> None:
        while not _MEMORY_WATCHDOG_STOP.is_set():
            try:
                if _rss_bytes() > limit:
                    os.kill(pid, signal.SIGKILL)
                    return
            except OSError:
                return
            time.sleep(0.02)

    thread = threading.Thread(target=_watch, name="ainvest-worker-mem", daemon=True)
    thread.start()
    _MEMORY_WATCHDOG = thread
    return True


def stop_memory_watchdog() -> None:
    """Signal the soft memory watchdog to stop (tests / clean shutdown)."""
    _MEMORY_WATCHDOG_STOP.set()


def enforce_memory_allocation(size_bytes: int) -> None:
    """Fail closed when a requested allocation exceeds the configured memory limit.

    Used as a portable simulated memory limit when OS ``RLIMIT_AS`` cannot be
    lowered. Strategy probes and defensive adapters may call this before large
    allocations; the RSS watchdog remains the second line of defense.
    """
    raw = os.environ.get(ENV_MEMORY_LIMIT)
    if raw is None or raw == "":
        return
    limit = int(raw)
    if size_bytes > limit:
        raise MemoryError("strategy worker memory limit exceeded")


class NetworkAccessDeniedError(PermissionError):
    """Raised when strategy code attempts network I/O inside a worker."""

    def __init__(self, message: str = "strategy worker network access denied") -> None:
        super().__init__(message)


def block_network() -> None:
    """Replace socket constructors so outbound network I/O fails closed."""
    global _ORIGINAL_SOCKET, _ORIGINAL_CREATE_CONNECTION
    if _ORIGINAL_SOCKET is None:
        _ORIGINAL_SOCKET = socket.socket
    if _ORIGINAL_CREATE_CONNECTION is None:
        _ORIGINAL_CREATE_CONNECTION = socket.create_connection

    class _BlockedSocket(socket.socket):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise NetworkAccessDeniedError()

        def connect(self, *args: Any, **kwargs: Any) -> None:
            raise NetworkAccessDeniedError()

        def connect_ex(self, *args: Any, **kwargs: Any) -> int:
            raise NetworkAccessDeniedError()

    def _blocked_create_connection(*args: Any, **kwargs: Any) -> socket.socket:
        raise NetworkAccessDeniedError()

    socket.socket = _BlockedSocket  # type: ignore[misc]
    socket.create_connection = _blocked_create_connection


def restore_network() -> None:
    """Restore original socket APIs (tests only)."""
    global _ORIGINAL_SOCKET, _ORIGINAL_CREATE_CONNECTION
    if _ORIGINAL_SOCKET is not None:
        socket.socket = _ORIGINAL_SOCKET  # type: ignore[misc]
        _ORIGINAL_SOCKET = None
    if _ORIGINAL_CREATE_CONNECTION is not None:
        socket.create_connection = _ORIGINAL_CREATE_CONNECTION
        _ORIGINAL_CREATE_CONNECTION = None


def prepare_workdir(*, read_only: bool) -> Path:
    """Create a dedicated worker directory; optionally make it read-only."""
    workdir = Path(tempfile.mkdtemp(prefix="ainvest-strategy-worker-"))
    os.chdir(workdir)
    if read_only:
        os.chmod(workdir, 0o555)
    return workdir


def apply_isolation(limits: WorkerLimits) -> AppliedLimits:
    """Apply resource, network, and filesystem isolation for this worker."""
    cpu_applied = apply_cpu_limit(limits.cpu_seconds)
    mem_rlimit = apply_memory_rlimit(limits.memory_limit_bytes)
    mem_watchdog = False
    if limits.memory_limit_bytes is not None:
        # Ensure child code can observe the limit via environ as well as rlimit.
        os.environ[ENV_MEMORY_LIMIT] = str(limits.memory_limit_bytes)
        mem_watchdog = start_memory_watchdog(limits.memory_limit_bytes)

    network_blocked = False
    if limits.block_network:
        block_network()
        network_blocked = True

    workdir: Path | None = None
    if limits.read_only_workdir:
        workdir = prepare_workdir(read_only=True)

    return AppliedLimits(
        cpu_rlimit_applied=cpu_applied,
        memory_rlimit_applied=mem_rlimit or mem_watchdog,
        memory_watchdog_applied=mem_watchdog,
        network_blocked=network_blocked,
        read_only_workdir=workdir,
    )


def limits_from_environ() -> WorkerLimits:
    """Rebuild limits from child environment knobs (parent-injected)."""

    def _float(name: str, default: float | None) -> float | None:
        raw = os.environ.get(name)
        if raw is None or raw == "":
            return default
        return float(raw)

    def _int(name: str, default: int | None) -> int | None:
        raw = os.environ.get(name)
        if raw is None or raw == "":
            return default
        return int(raw)

    def _bool(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None or raw == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    return WorkerLimits(
        wall_timeout_seconds=_float(ENV_WALL_TIMEOUT, 5.0) or 5.0,
        cpu_seconds=_float(ENV_CPU_SECONDS, 5.0),
        memory_limit_bytes=_int(ENV_MEMORY_LIMIT, 256 * 1024 * 1024),
        block_network=_bool(ENV_BLOCK_NETWORK, True),
        read_only_workdir=_bool(ENV_READ_ONLY_WORKDIR, True),
    )


def inject_limit_environ(
    environ: dict[str, str],
    limits: WorkerLimits,
    workdir: Path | None,
) -> None:
    """Copy limit knobs into the child environment (non-secret)."""
    environ[ENV_WALL_TIMEOUT] = str(limits.wall_timeout_seconds)
    if limits.cpu_seconds is not None:
        environ[ENV_CPU_SECONDS] = str(limits.cpu_seconds)
    if limits.memory_limit_bytes is not None:
        environ[ENV_MEMORY_LIMIT] = str(limits.memory_limit_bytes)
    environ[ENV_BLOCK_NETWORK] = "1" if limits.block_network else "0"
    environ[ENV_READ_ONLY_WORKDIR] = "1" if limits.read_only_workdir else "0"
    if workdir is not None:
        environ[ENV_WORKDIR] = str(workdir)


__all__ = [
    "AppliedLimits",
    "NetworkAccessDeniedError",
    "apply_cpu_limit",
    "apply_isolation",
    "apply_memory_rlimit",
    "block_network",
    "enforce_memory_allocation",
    "inject_limit_environ",
    "limits_from_environ",
    "prepare_workdir",
    "restore_network",
    "start_memory_watchdog",
    "stop_memory_watchdog",
]
