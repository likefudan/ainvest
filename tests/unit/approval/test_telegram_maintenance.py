"""Public maintenance-lease boundary tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ainvest.approval.telegram import TelegramEnvironment
from ainvest.approval.telegram_maintenance import (
    TelegramMaintenanceLeaseError,
    TelegramMaintenanceLeasePolicy,
    TelegramPollingMaintenanceLease,
)
from ainvest.db import TelegramUpdateRepository, create_all_tables, create_db_engine
from ainvest.db.session import create_session_factory


def test_public_lease_preserves_offset_and_releases(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'state.sqlite3'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    with factory.begin() as session:
        repository = TelegramUpdateRepository(session)
        state = repository.ensure_state("staging")
        assert state.next_offset == 0

    async def run() -> None:
        async with TelegramPollingMaintenanceLease(
            factory,
            TelegramEnvironment.STAGING,
            policy=TelegramMaintenanceLeasePolicy(wait_seconds=0),
        ) as lease:
            await lease.verify_before_write()

    asyncio.run(run())
    with factory() as session:
        released_state = TelegramUpdateRepository(session).get_state("staging")
        assert released_state is not None
        assert released_state.next_offset == 0
        assert released_state.lease_owner is None
    engine.dispose()


def test_public_lease_rejects_active_competitor(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'state.sqlite3'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory.begin() as session:
        state = TelegramUpdateRepository(session).acquire_lease(
            "production",
            owner="poller",
            now=now,
            expires_at=now + timedelta(seconds=60),
        )
        assert state is not None

    async def run() -> None:
        async with TelegramPollingMaintenanceLease(
            factory,
            TelegramEnvironment.PRODUCTION,
            policy=TelegramMaintenanceLeasePolicy(wait_seconds=0),
        ):
            pytest.fail("lease unexpectedly acquired")

    with pytest.raises(TelegramMaintenanceLeaseError, match="maintenance_lease_busy"):
        asyncio.run(run())
    engine.dispose()
