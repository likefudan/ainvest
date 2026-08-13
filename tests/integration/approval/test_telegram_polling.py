"""Offline end-to-end Telegram polling tests with real SQLite persistence."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.telegram import TelegramBotIdentity, TelegramEnvironment
from ainvest.approval.telegram_updates import (
    AsyncioTelegramPollingControl,
    AuthorizedTelegramUpdate,
    TelegramHandlerDisposition,
    TelegramLongPoller,
    TelegramPollingFatal,
    TelegramProviderRateLimited,
    TelegramProviderTransient,
    TelegramProviderUpdate,
    TelegramProviderUpdateKind,
)
from ainvest.config import Settings, TelegramBotSettings, TelegramRecipient
from ainvest.db import UnitOfWork, create_all_tables, create_db_engine, create_session_factory
from ainvest.db.models import TelegramProcessedUpdateRow


@dataclass
class Identity:
    calls: int = 0

    async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity:
        assert token == "synthetic-token"
        assert timeout_seconds == 5.0
        self.calls += 1
        return TelegramBotIdentity(id=9001)


@dataclass
class Updates:
    batches: list[tuple[TelegramProviderUpdate, ...]]
    stop: asyncio.Event
    calls: list[dict[str, object]] = field(default_factory=list)

    async def get_updates(self, token: str, **kwargs: object):  # type: ignore[no-untyped-def]
        assert token == "synthetic-token"
        self.calls.append(kwargs)
        if self.batches:
            return self.batches.pop(0)
        self.stop.set()
        return ()


@dataclass
class Handler:
    disposition: TelegramHandlerDisposition = TelegramHandlerDisposition.TERMINAL_HANDLED
    updates: list[AuthorizedTelegramUpdate] = field(default_factory=list)

    async def handle(self, update: AuthorizedTelegramUpdate) -> TelegramHandlerDisposition:
        self.updates.append(update)
        return self.disposition


class StopOnDelay:
    def __init__(self) -> None:
        self.delays: list[float] = []
        self.stopped = False

    def is_set(self) -> bool:
        return self.stopped

    async def wait(self, timeout_seconds: float) -> bool:
        self.delays.append(timeout_seconds)
        self.stopped = True
        return True


class StopAfterDelays:
    def __init__(self, count: int) -> None:
        self.count = count
        self.delays: list[float] = []

    def is_set(self) -> bool:
        return False

    async def wait(self, timeout_seconds: float) -> bool:
        self.delays.append(timeout_seconds)
        return len(self.delays) >= self.count


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


@dataclass
class ScriptedHandler:
    outcomes: list[TelegramHandlerDisposition | BaseException]
    updates: list[AuthorizedTelegramUpdate] = field(default_factory=list)

    async def handle(self, update: AuthorizedTelegramUpdate) -> TelegramHandlerDisposition:
        self.updates.append(update)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@dataclass
class ScriptedUpdates:
    outcomes: list[tuple[TelegramProviderUpdate, ...] | BaseException]

    async def get_updates(self, token: str, **kwargs: object):  # type: ignore[no-untyped-def]
        del token, kwargs
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _settings() -> Settings:
    return Settings(
        telegram_staging=TelegramBotSettings(
            enabled=True,
            bot_token=SecretStr("synthetic-token"),
            expected_bot_id=9001,
            allowed_recipients=(TelegramRecipient(user_id=101, private_chat_id=201),),
        )
    )


def _callback(
    update_id: int, callback_query_id: str = "same-callback-query"
) -> TelegramProviderUpdate:
    return TelegramProviderUpdate(
        update_id=update_id,
        kind=TelegramProviderUpdateKind.CALLBACK,
        sender_user_id=101,
        chat_id=201,
        message_id=301,
        chat_type="private",
        callback_query_id=SecretStr(callback_query_id),
        callback_data=SecretStr("opaque-callback-data"),
    )


@pytest.mark.integration
def test_polling_advances_gaps_and_deduplicates_callback_ids(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'poll.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    stop = asyncio.Event()
    transport = Updates(
        batches=[
            (
                TelegramProviderUpdate(update_id=5, kind=TelegramProviderUpdateKind.UNSUPPORTED),
                TelegramProviderUpdate(update_id=6, kind=TelegramProviderUpdateKind.MALFORMED),
                _callback(7),
                _callback(8),
            )
        ],
        stop=stop,
    )
    handler = Handler()
    poller = TelegramLongPoller(
        settings=_settings(),
        environment=TelegramEnvironment.STAGING,
        session_factory=factory,
        identity_transport=Identity(),
        update_transport=transport,
        handler=handler,
        clock=lambda: datetime(2026, 8, 12, 12, tzinfo=UTC),
        random_value=lambda: 0,
        owner="worker-a",
    )
    asyncio.run(poller.run(AsyncioTelegramPollingControl(stop)))

    assert len(handler.updates) == 1
    assert transport.calls[0] == {
        "offset": 0,
        "timeout": 25,
        "limit": 100,
        "allowed_updates": ("message", "callback_query"),
        "deadline_seconds": 35.0,
    }
    with UnitOfWork(factory) as uow:
        state = uow.telegram_updates_repo.get_state("staging")
        assert state is not None and state.next_offset == 9
    with factory() as session:
        rows = session.query(TelegramProcessedUpdateRow).order_by(
            TelegramProcessedUpdateRow.update_id
        )
        assert [(row.update_id, row.disposition) for row in rows] == [
            (5, "ignored"),
            (6, "ignored"),
            (7, "handled"),
            (8, "duplicate_callback"),
        ]
        assert rows[0].callback_query_digest is None
        assert rows[1].callback_query_digest is None
        assert rows[2].callback_query_digest is not None
        assert rows[3].callback_query_digest is None
    engine.dispose()


@pytest.mark.integration
def test_retry_later_preserves_cursor_releases_lease_and_uses_processing_backoff(
    tmp_path: Path,
) -> None:
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'retry.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    transport = Updates(batches=[(_callback(3),)], stop=asyncio.Event())
    handler = Handler(disposition=TelegramHandlerDisposition.RETRY_LATER)
    control = StopOnDelay()
    poller = TelegramLongPoller(
        settings=_settings(),
        environment=TelegramEnvironment.STAGING,
        session_factory=factory,
        identity_transport=Identity(),
        update_transport=transport,
        handler=handler,
        clock=lambda: datetime(2026, 8, 12, 12, tzinfo=UTC),
        random_value=lambda: 0,
        owner="worker-a",
    )
    asyncio.run(poller.run(control))
    assert control.delays == [1.0]
    with UnitOfWork(factory) as uow:
        state = uow.telegram_updates_repo.get_state("staging")
        assert state is not None
        assert state.next_offset == 0
        assert state.lease_owner is None
    with factory() as session:
        assert session.query(TelegramProcessedUpdateRow).count() == 0
    engine.dispose()


@pytest.mark.integration
def test_processing_backoff_does_not_reset_on_provider_success_and_resets_on_commit(
    tmp_path: Path,
) -> None:
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'retry-sequence.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    transport = ScriptedUpdates(
        outcomes=[
            (_callback(3),),
            (_callback(3),),
            (_callback(3),),
            (_callback(4, "another-callback-query"),),
        ]
    )
    handler = ScriptedHandler(
        outcomes=[
            TelegramHandlerDisposition.RETRY_LATER,
            RuntimeError("not logged"),
            TelegramHandlerDisposition.TERMINAL_HANDLED,
            TelegramHandlerDisposition.RETRY_LATER,
        ]
    )
    control = StopAfterDelays(3)
    poller = TelegramLongPoller(
        settings=_settings(),
        environment=TelegramEnvironment.STAGING,
        session_factory=factory,
        identity_transport=Identity(),
        update_transport=transport,
        handler=handler,
        clock=lambda: datetime(2026, 8, 12, 12, tzinfo=UTC),
        random_value=lambda: 0,
        owner="worker-a",
    )
    asyncio.run(poller.run(control))
    assert control.delays == [1.0, 2.0, 1.0]
    with UnitOfWork(factory) as uow:
        state = uow.telegram_updates_repo.get_state("staging")
        assert state is not None and state.next_offset == 4
        assert state.lease_owner is None
    engine.dispose()


@pytest.mark.integration
def test_network_and_rate_limit_backoffs_are_bounded_and_independent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'provider-backoff.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    transport = ScriptedUpdates(
        outcomes=[
            TelegramProviderTransient(),
            TelegramProviderTransient(),
            (),
            TelegramProviderTransient(),
        ]
    )
    control = StopAfterDelays(3)
    poller = TelegramLongPoller(
        settings=_settings(),
        environment=TelegramEnvironment.STAGING,
        session_factory=factory,
        identity_transport=Identity(),
        update_transport=transport,
        handler=Handler(),
        clock=lambda: datetime(2026, 8, 12, 12, tzinfo=UTC),
        random_value=lambda: 1,
        owner="worker-a",
    )
    asyncio.run(poller.run(control))
    assert control.delays == [1.25, 2.5, 1.25]

    rate_control = StopOnDelay()
    production = _settings().model_copy(
        update={"telegram_production": _settings().telegram_staging}
    )
    rate_poller = TelegramLongPoller(
        settings=production,
        environment=TelegramEnvironment.PRODUCTION,
        session_factory=factory,
        identity_transport=Identity(),
        update_transport=ScriptedUpdates(outcomes=[TelegramProviderRateLimited(1_000)]),
        handler=Handler(),
        clock=lambda: datetime(2026, 8, 12, 12, tzinfo=UTC),
        random_value=lambda: 1,
        owner="worker-rate",
    )
    asyncio.run(rate_poller.run(rate_control))
    assert rate_control.delays == [60.0]
    engine.dispose()


@dataclass
class TakeoverAfterPoll:
    clock: MutableClock
    factory: sessionmaker[Session]

    async def get_updates(self, token: str, **kwargs: object):  # type: ignore[no-untyped-def]
        del token, kwargs
        self.clock.advance(76)
        with UnitOfWork(self.factory) as uow:
            takeover = uow.telegram_updates_repo.acquire_lease(
                "staging",
                owner="worker-b",
                now=self.clock(),
                expires_at=self.clock() + timedelta(seconds=75),
            )
        assert takeover is not None
        return (_callback(3),)


@dataclass
class TakeoverInHandler:
    clock: MutableClock
    factory: sessionmaker[Session]
    calls: int = 0

    async def handle(self, update: AuthorizedTelegramUpdate) -> TelegramHandlerDisposition:
        del update
        self.calls += 1
        self.clock.advance(76)
        with UnitOfWork(self.factory) as uow:
            takeover = uow.telegram_updates_repo.acquire_lease(
                "staging",
                owner="worker-b",
                now=self.clock(),
                expires_at=self.clock() + timedelta(seconds=75),
            )
        assert takeover is not None
        return TelegramHandlerDisposition.TERMINAL_HANDLED


@pytest.mark.integration
def test_takeover_after_full_poll_fences_handler_dispatch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'poll-takeover.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    clock = MutableClock(datetime(2026, 8, 12, 12, tzinfo=UTC))
    handler = Handler()
    control = StopOnDelay()
    poller = TelegramLongPoller(
        settings=_settings(),
        environment=TelegramEnvironment.STAGING,
        session_factory=factory,
        identity_transport=Identity(),
        update_transport=TakeoverAfterPoll(clock, factory),
        handler=handler,
        clock=clock,
        random_value=lambda: 0,
        owner="worker-a",
    )
    asyncio.run(poller.run(control))
    assert handler.updates == []
    with UnitOfWork(factory) as uow:
        state = uow.telegram_updates_repo.get_state("staging")
        assert state is not None
        assert state.lease_owner == "worker-b"
        assert state.next_offset == 0
    engine.dispose()


@pytest.mark.integration
def test_takeover_during_handler_fences_terminal_commit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'handler-takeover.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    clock = MutableClock(datetime(2026, 8, 12, 12, tzinfo=UTC))
    handler = TakeoverInHandler(clock, factory)
    control = StopOnDelay()
    poller = TelegramLongPoller(
        settings=_settings(),
        environment=TelegramEnvironment.STAGING,
        session_factory=factory,
        identity_transport=Identity(),
        update_transport=ScriptedUpdates(outcomes=[(_callback(3),)]),
        handler=handler,
        clock=clock,
        random_value=lambda: 0,
        owner="worker-a",
    )
    asyncio.run(poller.run(control))
    assert handler.calls == 1
    with UnitOfWork(factory) as uow:
        state = uow.telegram_updates_repo.get_state("staging")
        assert state is not None
        assert state.lease_owner == "worker-b"
        assert state.next_offset == 0
    with factory() as session:
        assert session.query(TelegramProcessedUpdateRow).count() == 0
    engine.dispose()


@dataclass
class BlockingHandler:
    started: asyncio.Event

    async def handle(self, update: AuthorizedTelegramUpdate) -> TelegramHandlerDisposition:
        del update
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.integration
def test_shutdown_cancellation_releases_lease_without_repolling(tmp_path) -> None:  # type: ignore[no-untyped-def]
    async def scenario() -> None:
        engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'shutdown.db'}")
        create_all_tables(engine)
        factory = create_session_factory(engine)
        stop = asyncio.Event()
        started = asyncio.Event()
        transport = ScriptedUpdates(outcomes=[(_callback(3),)])
        poller = TelegramLongPoller(
            settings=_settings(),
            environment=TelegramEnvironment.STAGING,
            session_factory=factory,
            identity_transport=Identity(),
            update_transport=transport,
            handler=BlockingHandler(started),
            clock=lambda: datetime(2026, 8, 12, 12, tzinfo=UTC),
            random_value=lambda: 0,
            owner="worker-a",
        )
        task = asyncio.create_task(poller.run(AsyncioTelegramPollingControl(stop)))
        await asyncio.wait_for(started.wait(), 1)
        stop.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with UnitOfWork(factory) as uow:
            state = uow.telegram_updates_repo.get_state("staging")
            assert state is not None
            assert state.lease_owner is None
            assert state.next_offset == 0
        assert transport.outcomes == []
        engine.dispose()

    asyncio.run(scenario())


@dataclass
class SlowHandler:
    async def handle(self, update: AuthorizedTelegramUpdate) -> TelegramHandlerDisposition:
        del update
        await asyncio.sleep(1)
        return TelegramHandlerDisposition.TERMINAL_HANDLED


@pytest.mark.integration
@pytest.mark.parametrize(
    "handler",
    [ScriptedHandler(outcomes=[asyncio.CancelledError()]), SlowHandler()],
)
def test_handler_cancellation_and_timeout_are_nonterminal_with_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler: ScriptedHandler | SlowHandler,
) -> None:
    if isinstance(handler, SlowHandler):
        monkeypatch.setattr(
            "ainvest.approval.telegram_updates.TELEGRAM_HANDLER_DEADLINE_SECONDS",
            0.001,
        )
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'handler-failure.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    control = StopOnDelay()
    poller = TelegramLongPoller(
        settings=_settings(),
        environment=TelegramEnvironment.STAGING,
        session_factory=factory,
        identity_transport=Identity(),
        update_transport=ScriptedUpdates(outcomes=[(_callback(3),)]),
        handler=handler,
        clock=lambda: datetime(2026, 8, 12, 12, tzinfo=UTC),
        random_value=lambda: 0,
        owner="worker-a",
    )
    asyncio.run(poller.run(control))
    assert control.delays == [1.0]
    with UnitOfWork(factory) as uow:
        state = uow.telegram_updates_repo.get_state("staging")
        assert state is not None
        assert state.next_offset == 0
        assert state.lease_owner is None
    engine.dispose()


@pytest.mark.integration
def test_fatal_provider_failure_is_sanitized_and_releases_lease(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'fatal.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    poller = TelegramLongPoller(
        settings=_settings(),
        environment=TelegramEnvironment.STAGING,
        session_factory=factory,
        identity_transport=Identity(),
        update_transport=ScriptedUpdates(
            outcomes=[TelegramPollingFatal("telegram provider rejected the polling request")]
        ),
        handler=Handler(),
        clock=lambda: datetime(2026, 8, 12, 12, tzinfo=UTC),
        random_value=lambda: 0,
        owner="worker-a",
    )
    with pytest.raises(TelegramPollingFatal) as captured:
        asyncio.run(poller.run(StopOnDelay()))
    assert str(captured.value) == "telegram provider rejected the polling request"
    with UnitOfWork(factory) as uow:
        state = uow.telegram_updates_repo.get_state("staging")
        assert state is not None and state.lease_owner is None
    engine.dispose()
