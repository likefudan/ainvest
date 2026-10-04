"""Research reconstruction uses only captured inputs and local calculations."""

import socket
from pathlib import Path

import pytest
from indicator_fixtures import snapshot

from ainvest.data.cache import SnapshotStore
from ainvest.data.snapshots import replay_snapshot


def test_offline_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = snapshot()

    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline replay attempted network access")

    monkeypatch.setattr(socket, "socket", denied)
    cache = SnapshotStore(
        tmp_path / "research", max_total_bytes=1_000_000, max_entry_bytes=500_000, max_entries=2
    )
    key = cache.put(original)
    assert replay_snapshot(cache.get(key)) == original.packet
