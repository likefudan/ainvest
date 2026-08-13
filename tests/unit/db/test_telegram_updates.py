"""Telegram cursor, lease-fencing, and terminal-marker repository tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ainvest.db import UnitOfWork, create_all_tables, create_db_engine, create_session_factory
from ainvest.db.models import TelegramProcessedUpdateRow


def _factory(tmp_path: Path) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'telegram.db'}")
    create_all_tables(engine)
    return engine, create_session_factory(engine)


def test_lease_takeover_fences_old_owner_and_terminal_is_atomic(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine, factory = _factory(tmp_path)
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    with UnitOfWork(factory) as uow:
        first = uow.telegram_updates_repo.acquire_lease(
            "staging", owner="worker-a", now=now, expires_at=now + timedelta(seconds=75)
        )
    assert first is not None
    assert first.lease_epoch == 1
    assert first.next_offset == 0

    with UnitOfWork(factory) as uow:
        assert (
            uow.telegram_updates_repo.acquire_lease(
                "staging",
                owner="worker-b",
                now=now + timedelta(seconds=10),
                expires_at=now + timedelta(seconds=85),
            )
            is None
        )
    with UnitOfWork(factory) as uow:
        second = uow.telegram_updates_repo.acquire_lease(
            "staging",
            owner="worker-b",
            now=now + timedelta(seconds=76),
            expires_at=now + timedelta(seconds=151),
        )
    assert second is not None
    assert second.lease_epoch == 2

    with UnitOfWork(factory) as uow:
        assert not uow.telegram_updates_repo.record_terminal(
            "staging",
            owner="worker-a",
            epoch=first.lease_epoch,
            version=first.version,
            now=now + timedelta(seconds=77),
            update_id=10,
            kind="ignored",
            disposition="ignored",
            callback_query_digest=None,
        )
    with factory() as session:
        assert session.query(TelegramProcessedUpdateRow).count() == 0

    with UnitOfWork(factory) as uow:
        assert uow.telegram_updates_repo.record_terminal(
            "staging",
            owner="worker-b",
            epoch=second.lease_epoch,
            version=second.version,
            now=now + timedelta(seconds=77),
            update_id=10,
            kind="ignored",
            disposition="ignored",
            callback_query_digest=None,
        )
    with UnitOfWork(factory) as uow:
        state = uow.telegram_updates_repo.get_state("staging")
        assert state is not None and state.next_offset == 11
        assert uow.telegram_updates_repo.is_processed("staging", 10)
    engine.dispose()


def test_release_requires_exact_owner_and_epoch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine, factory = _factory(tmp_path)
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    with UnitOfWork(factory) as uow:
        lease = uow.telegram_updates_repo.acquire_lease(
            "production", owner="worker", now=now, expires_at=now + timedelta(seconds=75)
        )
    assert lease is not None
    with UnitOfWork(factory) as uow:
        assert not uow.telegram_updates_repo.release_lease(
            "production", owner="worker", epoch=lease.lease_epoch + 1, version=lease.version
        )
        assert uow.telegram_updates_repo.release_lease(
            "production", owner="worker", epoch=lease.lease_epoch, version=lease.version
        )
    engine.dispose()


def test_renew_preserves_epoch_and_expired_lease_cannot_commit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine, factory = _factory(tmp_path)
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    with UnitOfWork(factory) as uow:
        lease = uow.telegram_updates_repo.acquire_lease(
            "staging", owner="worker", now=now, expires_at=now + timedelta(seconds=75)
        )
    assert lease is not None
    with UnitOfWork(factory) as uow:
        renewed = uow.telegram_updates_repo.renew_lease(
            "staging",
            owner="worker",
            epoch=lease.lease_epoch,
            version=lease.version,
            now=now + timedelta(seconds=30),
            expires_at=now + timedelta(seconds=105),
        )
    assert renewed is not None
    assert renewed.lease_epoch == lease.lease_epoch
    assert renewed.version == lease.version + 1

    with UnitOfWork(factory) as uow:
        assert not uow.telegram_updates_repo.record_terminal(
            "staging",
            owner="worker",
            epoch=renewed.lease_epoch,
            version=renewed.version,
            now=now + timedelta(seconds=106),
            update_id=1,
            kind="ignored",
            disposition="ignored",
            callback_query_digest=None,
        )
    with factory() as session:
        assert session.query(TelegramProcessedUpdateRow).count() == 0
    engine.dispose()


def test_terminal_rejects_non_sha256_callback_digest(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine, factory = _factory(tmp_path)
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    with UnitOfWork(factory) as uow:
        lease = uow.telegram_updates_repo.acquire_lease(
            "staging", owner="worker", now=now, expires_at=now + timedelta(seconds=75)
        )
    assert lease is not None
    with pytest.raises(ValueError, match="lowercase SHA-256"), UnitOfWork(factory) as uow:
        uow.telegram_updates_repo.record_terminal(
            "staging",
            owner="worker",
            epoch=lease.lease_epoch,
            version=lease.version,
            now=now,
            update_id=1,
            kind="callback",
            disposition="handled",
            callback_query_digest="not-a-digest",
        )
    engine.dispose()
