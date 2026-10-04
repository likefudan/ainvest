"""Resident-memory accounting cannot borrow a parent's peak or fail open."""

import io
import os
import resource
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from ainvest.strategies.worker import isolation


@pytest.mark.parametrize(
    "body",
    [
        b"VmHWM:\t400 kB\nVmRSS:\t100 kB\n",
        b"Name:\tworker\nVmRSS: 100 kB\nVmHWM: 400 kB\n",
    ],
)
def test_linux_own_peak_not_process_lifetime(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: io.BytesIO(body))
    monkeypatch.setattr(resource, "getrusage", lambda _: SimpleNamespace(ru_maxrss=999999))
    assert isolation._rss_bytes() == 400 * 1024


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"VmHWM: 0 kB\nVmRSS: 0 kB\n",
        b"VmHWM: -1 kB\nVmRSS: 1 kB\n",
        b"VmHWM: 1 MB\nVmRSS: 1 kB\n",
        b"VmHWM: 1 kB\nVmRSS: 2 kB\n",
        b"VmHWM: 2 kB\nVmHWM: 3 kB\nVmRSS: 1 kB\n",
        b"x" * 65537,
    ],
)
def test_invalid_proc_falls_back_conservatively(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: io.BytesIO(body))
    monkeypatch.setattr(resource, "getrusage", lambda _: SimpleNamespace(ru_maxrss=900))
    assert isolation._rss_bytes() == 900 * 1024


def test_missing_proc_and_macos_units(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise OSError("proc unavailable")

    monkeypatch.setattr(Path, "open", missing)
    monkeypatch.setattr(resource, "getrusage", lambda _: SimpleNamespace(ru_maxrss=1234))
    monkeypatch.setattr(sys, "platform", "linux")
    assert isolation._rss_bytes() == 1234 * 1024
    monkeypatch.setattr(sys, "platform", "darwin")
    assert isolation._rss_bytes() == 1234


@pytest.mark.parametrize("peak", [0, -1, float("nan"), float("inf")])
def test_invalid_fallback_is_not_zero_usage(monkeypatch: pytest.MonkeyPatch, peak: float) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(resource, "getrusage", lambda _: SimpleNamespace(ru_maxrss=peak))
    with pytest.raises(OSError):
        isolation._rss_bytes()


@pytest.mark.parametrize("mode", ["over", "error", "memory_error", "under"])
def test_watchdog_does_not_silently_exit(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    stop = threading.Event()
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(isolation, "_MEMORY_WATCHDOG_STOP", stop)
    monkeypatch.setattr(isolation, "_MEMORY_WATCHDOG", None)
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    def usage() -> int:
        if mode == "error":
            raise OSError("unreadable accounting")
        if mode == "memory_error":
            raise MemoryError
        if mode == "under":
            stop.set()
            return 10
        return 101

    monkeypatch.setattr(isolation, "_rss_bytes", usage)

    class ImmediateThread:
        def __init__(self, *, target: Callable[[], None], name: str, daemon: bool) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(threading, "Thread", ImmediateThread)
    assert isolation.start_memory_watchdog(100)
    assert bool(killed) == (mode != "under")
