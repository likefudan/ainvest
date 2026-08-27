"""Best-effort database coordination for stopped Telegram pollers.

The external process-manager stop plus an operator acknowledgement is the
authoritative quiescence boundary.  This lease only detects a conforming
poller that is still visible in the shared P05-T5 state; it is not a
filesystem fence and does not make database and filesystem changes atomic.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.telegram import TelegramEnvironment
from ainvest.db import TelegramPollState, TelegramUpdateRepository


class TelegramMaintenanceLeaseError(Exception):
    """Stable value-free maintenance coordination failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"TelegramMaintenanceLeaseError(code={self.code!r})"


@dataclass(frozen=True, slots=True)
class TelegramMaintenanceLeasePolicy:
    """Bounded timing policy shared by local Telegram maintenance tools."""

    wait_seconds: float = 90.0
    lease_seconds: float = 75.0
    heartbeat_seconds: float = 20.0
    poll_seconds: float = 0.5


class TelegramPollingMaintenanceLease:
    """Reuse P05-T5's fenced lease without advancing its poll cursor."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        environment: TelegramEnvironment,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        policy: TelegramMaintenanceLeasePolicy | None = None,
        owner_prefix: str = "maintenance",
    ) -> None:
        self._factory = factory
        self._environment = environment.value
        self._clock = clock
        self._sleep = sleep
        self._policy = TelegramMaintenanceLeasePolicy() if policy is None else policy
        self._owner = f"{owner_prefix}-{secrets.token_hex(16)}"
        self._state: TelegramPollState | None = None
        self._initial_offset: int | None = None
        self._failed = False
        self._lock = asyncio.Lock()
        self._heartbeat: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        await self._acquire()
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat
        async with self._lock:
            state = self._state
            if state is None:
                return
            try:
                with self._factory.begin() as session:
                    repository = TelegramUpdateRepository(session)
                    current = repository.get_state(self._environment)
                    self._assert_offset(current)
                    if current is not None and (
                        current.lease_owner == self._owner
                        and current.lease_epoch == state.lease_epoch
                    ):
                        released = repository.release_lease(
                            self._environment,
                            owner=self._owner,
                            epoch=state.lease_epoch,
                            version=current.version,
                        )
                        if released:
                            self._assert_offset(repository.get_state(self._environment))
            except TelegramMaintenanceLeaseError:
                raise
            except Exception:
                # Release is best effort; lease expiry is the recovery path.
                return

    async def verify_before_write(self) -> None:
        """Detect an already-visible loss; this cannot fence a later write."""
        async with self._lock:
            if self._failed or self._state is None:
                raise TelegramMaintenanceLeaseError("maintenance_lease_lost")
            self._renew_locked()

    async def _acquire(self) -> None:
        deadline = self._clock() + timedelta(seconds=self._policy.wait_seconds)
        first = True
        while first or self._clock() < deadline:
            first = False
            now = self._clock()
            try:
                with self._factory.begin() as session:
                    repository = TelegramUpdateRepository(session)
                    before = repository.get_state(self._environment)
                    if self._initial_offset is None and before is not None:
                        self._initial_offset = before.next_offset
                    state = repository.acquire_lease(
                        self._environment,
                        owner=self._owner,
                        now=now,
                        expires_at=now + timedelta(seconds=self._policy.lease_seconds),
                    )
                    if state is not None:
                        if self._initial_offset is None:
                            self._initial_offset = 0
                        self._assert_offset(state)
                        self._state = state
                        return
            except TelegramMaintenanceLeaseError:
                raise
            except Exception:
                raise TelegramMaintenanceLeaseError("maintenance_database_failed") from None
            if self._clock() >= deadline:
                break
            await self._sleep(self._policy.poll_seconds)
        raise TelegramMaintenanceLeaseError("maintenance_lease_busy")

    def _renew_locked(self) -> None:
        state = self._state
        if state is None:
            self._failed = True
            raise TelegramMaintenanceLeaseError("maintenance_lease_lost")
        now = self._clock()
        try:
            with self._factory.begin() as session:
                repository = TelegramUpdateRepository(session)
                renewed = repository.renew_lease(
                    self._environment,
                    owner=self._owner,
                    epoch=state.lease_epoch,
                    version=state.version,
                    now=now,
                    expires_at=now + timedelta(seconds=self._policy.lease_seconds),
                )
                self._assert_offset(renewed)
        except TelegramMaintenanceLeaseError:
            self._failed = True
            raise
        except Exception:
            self._failed = True
            raise TelegramMaintenanceLeaseError("maintenance_database_failed") from None
        if renewed is None:
            self._failed = True
            raise TelegramMaintenanceLeaseError("maintenance_lease_lost")
        self._state = renewed

    async def _heartbeat_loop(self) -> None:
        while True:
            await self._sleep(self._policy.heartbeat_seconds)
            async with self._lock:
                if self._failed:
                    return
                try:
                    self._renew_locked()
                except TelegramMaintenanceLeaseError:
                    return

    def _assert_offset(self, state: TelegramPollState | None) -> None:
        if state is None or self._initial_offset is None:
            return
        if state.next_offset != self._initial_offset:
            raise TelegramMaintenanceLeaseError("poll_offset_changed")


__all__ = [
    "TelegramMaintenanceLeaseError",
    "TelegramMaintenanceLeasePolicy",
    "TelegramPollingMaintenanceLease",
]
