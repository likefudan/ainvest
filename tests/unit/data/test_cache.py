"""Private temporary stores only; never operational retention or data."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from indicator_fixtures import snapshot

from ainvest.data.cache import SnapshotStore
from ainvest.data.ports import DataConflictError, DataInvalidRequestError, DataUpstreamError
from ainvest.data.snapshots import build_snapshot


def store(
    root: Path, *, total: int = 1_000_000, entry: int = 500_000, count: int = 2
) -> SnapshotStore:
    return SnapshotStore(root, max_total_bytes=total, max_entry_bytes=entry, max_entries=count)


def test_roundtrip_idempotent_and_concurrent(tmp_path: Path) -> None:
    saved = snapshot()
    cache = store(tmp_path / "cache")
    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = list(pool.map(lambda _: store(cache.root).put(saved), range(8)))
    assert len(set(keys)) == 1
    assert cache.get(keys[0]) == saved
    assert cache.usage().entries == 1
    assert cache.usage().bytes_used > 0
    assert (cache.root.stat().st_mode & 0o777) == 0o700


def test_collision_never_overwrites(tmp_path: Path) -> None:
    saved = snapshot()
    cache = store(tmp_path / "cache")
    key = cache.put(saved)
    different = build_snapshot(saved.inputs, research_id="research_other_001")
    with pytest.raises(DataConflictError):
        cache.put(different)
    assert cache.get(key) == saved


@pytest.mark.parametrize("limit", ["entry", "total", "count"])
def test_quotas(tmp_path: Path, limit: str) -> None:
    saved = snapshot()
    cache = store(tmp_path / "cache")
    key = cache.put(saved)
    size = cache.usage().bytes_used
    tight = store(cache.root, total=size, entry=size, count=1)
    assert tight.put(saved) == key
    other_expected = saved.inputs.expected.model_copy(update={"max_age_seconds": 121})
    other = build_snapshot(
        saved.inputs.model_copy(update={"expected": other_expected}),
        research_id="research_other_001",
    )
    if limit == "entry":
        tight = store(cache.root, entry=1)
    elif limit == "total":
        tight = store(cache.root, total=size, entry=size, count=2)
    with pytest.raises(DataInvalidRequestError):
        tight.put(other)
    assert cache.get(key) == saved


@pytest.mark.parametrize("key", ["../escape", "sha256:../escape", "a" * 64, "sha256:" + "A" * 64])
def test_invalid_keys(tmp_path: Path, key: str) -> None:
    with pytest.raises(DataInvalidRequestError):
        store(tmp_path / "cache").get(key)


def test_tamper(tmp_path: Path) -> None:
    cache = store(tmp_path / "cache")
    key = cache.put(snapshot())
    path = cache.root / (key[7:] + ".json")
    original = path.read_bytes()
    path.write_bytes(
        original.replace(b'"payload_digest":"sha256:', b'"payload_digest":"sha256:0', 1)
    )
    with pytest.raises(DataInvalidRequestError):
        cache.get(key)
    path.write_bytes(original + b" ")
    with pytest.raises(DataConflictError):
        cache.get(key)


def test_symlinks_and_permissions(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(DataInvalidRequestError):
        store(link)
    cache = store(real)
    saved = snapshot()
    (real / (saved.inputs.cache_key[7:] + ".json")).symlink_to(tmp_path / "outside")
    with pytest.raises(DataUpstreamError):
        cache.get(saved.inputs.cache_key)
    real.chmod(0o755)
    with pytest.raises(DataInvalidRequestError):
        store(real)


@pytest.mark.parametrize("total,entry,count", [(0, 1, 1), (1, 2, 1), (10, 1, 0), (True, 1, 1)])
def test_invalid_quotas(tmp_path: Path, total: int, entry: int, count: int) -> None:
    with pytest.raises(DataInvalidRequestError):
        store(tmp_path / "cache", total=total, entry=entry, count=count)


def test_failed_publication_cleans_only_own_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = store(tmp_path / "cache")

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(DataUpstreamError):
        cache.put(snapshot())
    assert list(cache.root.iterdir()) == [cache.root / ".lock"]
    assert cache.usage().entries == 0


def test_unknown_file_fail_closed(tmp_path: Path) -> None:
    cache = store(tmp_path / "cache")
    pending = cache.root / ".pending-interrupted"
    pending.write_bytes(b"retain for operator inspection")
    with pytest.raises(DataInvalidRequestError):
        cache.put(snapshot())
    assert pending.exists()
