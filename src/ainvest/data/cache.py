"""Opt-in private POSIX snapshot storage; explicit quotas and no eviction."""

import fcntl
import os
import re
import secrets
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from ainvest.data.indicators import Digest, digest_bytes
from ainvest.data.ports import (
    DataConflictError,
    DataInvalidRequestError,
    DataOperation,
    DataUpstreamError,
)
from ainvest.data.snapshots import ResearchSnapshot, replay_snapshot
from ainvest.schemas.common import DomainModel, SchemaVersion


class StoredSnapshot(DomainModel):
    schema_version: SchemaVersion = "1.0"
    payload_digest: Digest
    snapshot: ResearchSnapshot


class CacheUsage(DomainModel):
    schema_version: SchemaVersion = "1.0"
    entries: int
    bytes_used: int


def _invalid(code: str) -> DataInvalidRequestError:
    return DataInvalidRequestError(
        "Snapshot store request rejected", operation=DataOperation.DATASET, reason_code=code
    )


class SnapshotStore:
    """Single-host cooperating writers; hashes detect corruption, not hostile owners."""

    def __init__(
        self, root: Path, *, max_total_bytes: int, max_entry_bytes: int, max_entries: int
    ) -> None:
        if (
            any(
                type(value) is not int or value <= 0
                for value in (max_total_bytes, max_entry_bytes, max_entries)
            )
            or max_entry_bytes > max_total_bytes
        ):
            raise _invalid("CACHE_QUOTA_INVALID")
        self.root = root.absolute()
        self.max_total_bytes = max_total_bytes
        self.max_entry_bytes = max_entry_bytes
        self.max_entries = max_entries
        try:
            if self.root.is_symlink():
                raise _invalid("CACHE_PATH_INVALID")
            self.root.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            raise _invalid("CACHE_PATH_INVALID") from None
        with self._locked():
            pass

    @contextmanager
    def _locked(self) -> Iterator[int]:
        directory = lock = -1
        try:
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise _invalid("CACHE_PERMISSIONS_INVALID")
            lock = os.open(".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            self._check_file(os.fstat(lock))
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise _invalid("CACHE_LOCK_TIMEOUT") from None
                    time.sleep(0.01)
            yield directory
        except OSError:
            raise DataUpstreamError(
                "Snapshot storage unavailable",
                operation=DataOperation.DATASET,
                reason_code="CACHE_IO_FAILED",
            ) from None
        finally:
            if lock >= 0:
                os.close(lock)
            if directory >= 0:
                os.close(directory)

    @staticmethod
    def _check_file(info: os.stat_result) -> None:
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise _invalid("CACHE_ENTRY_INVALID")

    @staticmethod
    def _name(key: str) -> str:
        if re.fullmatch(r"sha256:[a-f0-9]{64}", key) is None:
            raise _invalid("CACHE_KEY_INVALID")
        return key[7:] + ".json"

    def _usage(self, directory: int) -> CacheUsage:
        entries = size = 0
        with os.scandir(directory) as listing:
            for item in listing:
                if item.name == ".lock":
                    continue
                if re.fullmatch(r"[a-f0-9]{64}\.json", item.name) is None:
                    raise _invalid("CACHE_DIRECTORY_INVALID")
                info = item.stat(follow_symlinks=False)
                self._check_file(info)
                entries += 1
                size += info.st_size
                if entries > self.max_entries or size > self.max_total_bytes:
                    raise _invalid("CACHE_CAPACITY_REACHED")
        return CacheUsage(entries=entries, bytes_used=size)

    def usage(self) -> CacheUsage:
        with self._locked() as directory:
            return self._usage(directory)

    def _read(self, directory: int, name: str) -> bytes:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            self._check_file(info)
            if info.st_size > self.max_entry_bytes:
                raise _invalid("CACHE_ENTRY_TOO_LARGE")
            body = stream.read(self.max_entry_bytes + 1)
        if len(body) > self.max_entry_bytes:
            raise _invalid("CACHE_ENTRY_TOO_LARGE")
        return body

    def put(self, snapshot: ResearchSnapshot) -> str:
        replay_snapshot(snapshot)
        body = (
            StoredSnapshot(
                payload_digest=digest_bytes(snapshot.model_dump_json().encode()), snapshot=snapshot
            )
            .model_dump_json()
            .encode()
        )
        if len(body) > self.max_entry_bytes:
            raise _invalid("CACHE_ENTRY_TOO_LARGE")
        key = snapshot.inputs.cache_key
        name = self._name(key)
        with self._locked() as directory:
            try:
                previous = self._read(directory, name)
            except FileNotFoundError:
                previous = None
            if previous is not None:
                if previous != body:
                    raise DataConflictError(
                        "Immutable snapshot key collision",
                        operation=DataOperation.DATASET,
                        reason_code="CACHE_KEY_CONFLICT",
                    )
                return key
            usage = self._usage(directory)
            if (
                usage.entries >= self.max_entries
                or usage.bytes_used + len(body) > self.max_total_bytes
            ):
                raise _invalid("CACHE_CAPACITY_REACHED")
            pending = ".pending-" + secrets.token_hex(16)
            descriptor = os.open(
                pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(
                    pending, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False
                )
            finally:
                os.unlink(pending, dir_fd=directory)
            os.fsync(directory)
        return key

    def get(self, key: str) -> ResearchSnapshot:
        name = self._name(key)
        with self._locked() as directory:
            body = self._read(directory, name)
        try:
            record = StoredSnapshot.model_validate_json(body)
        except ValidationError:
            raise _invalid("CACHE_ENTRY_INVALID") from None
        if (
            record.model_dump_json().encode() != body
            or digest_bytes(record.snapshot.model_dump_json().encode()) != record.payload_digest
            or record.snapshot.inputs.cache_key != key
        ):
            raise DataConflictError(
                "Snapshot integrity failed",
                operation=DataOperation.DATASET,
                reason_code="CACHE_INTEGRITY_FAILED",
            )
        replay_snapshot(record.snapshot)
        return record.snapshot
