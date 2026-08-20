"""Real poller/UoW integration for the Telegram read-query runner."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, func, select

import ainvest.approval.telegram_updates as polling_module
import ainvest.orchestrator.telegram_queries as query_module
from ainvest.approval.telegram import TelegramBotIdentity, TelegramEnvironment
from ainvest.approval.telegram_updates import (
    TELEGRAM_HANDLER_DEADLINE_SECONDS,
    TelegramPollingFatal,
    TelegramProviderUpdate,
    TelegramProviderUpdateKind,
)
from ainvest.config import Settings, TelegramBotSettings, TelegramRecipient
from ainvest.db import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.models import TelegramPollStateRow, TelegramProcessedUpdateRow
from ainvest.orchestrator.telegram_queries import run_telegram_read

TOKEN = "900000001:" + "A" * 35


@dataclass
class Control:
    stopped: bool = False

    def is_set(self) -> bool:
        return self.stopped

    async def wait(self, timeout_seconds: float) -> bool:
        del timeout_seconds
        self.stopped = True
        return True


@dataclass
class IdentityTransport:
    calls: int = 0

    async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity:
        assert token == TOKEN
        assert timeout_seconds == 5.0
        self.calls += 1
        return TelegramBotIdentity(id=9001)


@dataclass
class UpdateTransport:
    control: Control
    text: str
    update_id: int = 1
    calls: int = 0

    async def get_updates(self, token: str, **kwargs: object) -> tuple[TelegramProviderUpdate, ...]:
        assert token == TOKEN
        self.calls += 1
        if self.calls > 1:
            self.control.stopped = True
            return ()
        return (
            TelegramProviderUpdate(
                update_id=self.update_id,
                kind=TelegramProviderUpdateKind.MESSAGE,
                sender_user_id=101,
                chat_id=201,
                message_id=301,
                chat_type="private",
                text=SecretStr(self.text),
            ),
        )


@dataclass
class CallbackUpdateTransport:
    control: Control
    calls: int = 0

    async def get_updates(self, token: str, **kwargs: object) -> tuple[TelegramProviderUpdate, ...]:
        assert token == TOKEN
        self.calls += 1
        if self.calls > 1:
            self.control.stopped = True
            return ()
        return (
            TelegramProviderUpdate(
                update_id=1,
                kind=TelegramProviderUpdateKind.CALLBACK,
                sender_user_id=101,
                chat_id=201,
                message_id=301,
                chat_type="private",
                callback_query_id=SecretStr("callback-id"),
                callback_data=SecretStr("approve"),
            ),
        )


@dataclass
class ReplyTransport:
    block: bool = False
    messages: list[str] = field(default_factory=list)

    async def send_plain_message(
        self,
        token: str,
        chat_id: int,
        text: str,
        *,
        timeout_seconds: float,
    ) -> int:
        assert token == TOKEN
        assert chat_id == 201
        assert timeout_seconds == 4.0
        self.messages.append(text)
        if self.block:
            await asyncio.Event().wait()
        return 77


@dataclass
class EngineHandle:
    inner: Engine
    disposals: int = 0

    def dispose(self) -> None:
        self.disposals += 1
        self.inner.dispose()


def _settings() -> Settings:
    return Settings(
        telegram_staging=TelegramBotSettings(
            enabled=True,
            bot_token=SecretStr(TOKEN),
            expected_bot_id=9001,
            allowed_recipients=(TelegramRecipient(user_id=101, private_chat_id=201),),
        ),
    )


def _engine(path: Path) -> tuple[Engine, Any]:
    engine = create_db_engine(f"sqlite+pysqlite:///{path}")
    create_all_tables(engine)
    return engine, create_session_factory(engine)


def _gateway(*, block: bool = False, lifecycle: list[str] | None = None):  # type: ignore[no-untyped-def]
    events = lifecycle if lifecycle is not None else []

    @asynccontextmanager
    async def opened():  # type: ignore[no-untyped-def]
        events.append("enter")
        try:
            if block:
                await asyncio.Event().wait()
            yield SimpleNamespace(client=SimpleNamespace())
        finally:
            events.append("exit")

    return opened


def _run_once(
    database: Path,
    *,
    text: str,
    update_id: int = 1,
    reply: ReplyTransport | None = None,
    gateway: Any = None,
) -> tuple[ReplyTransport, EngineHandle]:
    inner, factory = _engine(database)
    engine = EngineHandle(inner)
    control = Control()
    selected_reply = reply or ReplyTransport()
    asyncio.run(
        run_telegram_read(
            settings=_settings(),
            environment=TelegramEnvironment.STAGING,
            engine=cast(Engine, engine),
            session_factory=factory,
            identity_transport=IdentityTransport(),
            update_transport=UpdateTransport(control, text, update_id),
            reply_transport=selected_reply,
            control=control,
            gateway_factory=gateway or _gateway(),
            monotonic=lambda: 0.0,
            account_loader=lambda: SecretStr("synthetic-account-reference"),
        )
    )
    return selected_reply, engine


@pytest.mark.integration
def test_runner_uses_real_poller_uow_persists_terminal_and_restart_deduplicates(
    tmp_path: Path,
) -> None:
    database = tmp_path / "queries.sqlite3"
    lifecycle: list[str] = []
    first_reply, first_engine = _run_once(
        database,
        text="/rh_status",
        gateway=_gateway(lifecycle=lifecycle),
    )
    assert first_engine.disposals == 1
    assert lifecycle == ["enter", "exit"]
    assert len(first_reply.messages) == 1
    assert first_reply.messages[0].startswith("[READ ONLY - NOT FOR TRADING]\n")

    second_reply, second_engine = _run_once(
        database,
        text="/rh_status",
        gateway=_gateway(lifecycle=lifecycle),
    )
    assert second_engine.disposals == 1
    assert second_reply.messages == []
    assert lifecycle == ["enter", "exit"]

    check = create_db_engine(f"sqlite+pysqlite:///{database}")
    with create_session_factory(check)() as session:
        rows = session.scalar(select(func.count(TelegramProcessedUpdateRow.id)))
        row = session.scalar(select(TelegramProcessedUpdateRow))
        assert rows == 1
        assert row is not None and row.update_id == 1 and row.disposition == "handled"
        assert not hasattr(row, "message_id")
    check.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("phase", ["pre_send", "post_send"])
def test_real_poller_outer_deadline_distinguishes_pre_and_post_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    assert TELEGRAM_HANDLER_DEADLINE_SECONDS == 20.0
    monkeypatch.setattr(polling_module, "TELEGRAM_HANDLER_DEADLINE_SECONDS", 0.01)
    database = tmp_path / f"deadline-{phase}.sqlite3"
    reply = ReplyTransport(block=phase == "post_send")
    gateway = _gateway(block=phase == "pre_send")

    selected_reply, engine = _run_once(
        database,
        text="/rh_status",
        reply=reply,
        gateway=gateway,
    )
    assert engine.disposals == 1
    check = create_db_engine(f"sqlite+pysqlite:///{database}")
    with create_session_factory(check)() as session:
        terminal_count = session.scalar(select(func.count(TelegramProcessedUpdateRow.id)))
    check.dispose()

    if phase == "pre_send":
        assert selected_reply.messages == []
        assert terminal_count == 0
    else:
        assert len(selected_reply.messages) == 1
        assert terminal_count == 1


@pytest.mark.integration
def test_query_only_runner_parks_callback_without_reply_commit_or_offset_advance(
    tmp_path: Path,
) -> None:
    database = tmp_path / "callback.sqlite3"
    inner, factory = _engine(database)
    engine = EngineHandle(inner)
    control = Control()
    reply = ReplyTransport()

    asyncio.run(
        run_telegram_read(
            settings=_settings(),
            environment=TelegramEnvironment.STAGING,
            engine=cast(Engine, engine),
            session_factory=factory,
            identity_transport=IdentityTransport(),
            update_transport=CallbackUpdateTransport(control),
            reply_transport=reply,
            control=control,
            gateway_factory=lambda: (_ for _ in ()).throw(AssertionError("gateway opened")),
            account_loader=lambda: (_ for _ in ()).throw(AssertionError("account loaded")),
            monotonic=lambda: 0.0,
        )
    )

    assert engine.disposals == 1
    assert reply.messages == []
    check = create_db_engine(f"sqlite+pysqlite:///{database}")
    with create_session_factory(check)() as session:
        assert session.scalar(select(func.count(TelegramProcessedUpdateRow.id))) == 0
        state = session.scalar(select(TelegramPollStateRow))
        assert state is not None
        assert state.next_offset == 0
        assert state.lease_owner is None
    check.dispose()


def _migrate(database: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database}"},
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["normal", "fatal", "cancel"])
def test_real_cli_composition_cleans_shared_transport_engine_and_signals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    class ProviderError(Exception):
        pass

    monkeypatch.setattr(
        polling_module,
        "import_module",
        lambda name: SimpleNamespace(
            RetryAfter=ProviderError,
            TimedOut=ProviderError,
            NetworkError=ProviderError,
            InvalidToken=ProviderError,
            Forbidden=ProviderError,
            BadRequest=ProviderError,
            Conflict=ProviderError,
        ),
    )
    database = tmp_path / f"cli-{mode}.sqlite3"
    _migrate(database)
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "TELEGRAM_STAGING__BOT_TOKEN").write_text(TOKEN, encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "TELEGRAM_STAGING__ENABLED=true",
                "TELEGRAM_STAGING__EXPECTED_BOT_ID=9001",
                'TELEGRAM_STAGING__ALLOWED_RECIPIENTS=[{"user_id":101,"private_chat_id":201}]',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    namespace = query_module.build_parser().parse_args(
        [
            "--environment",
            "staging",
            "--database",
            str(database),
            "--env-file",
            str(env_file),
            "--secrets-dir",
            str(secrets_dir),
        ]
    )
    events: list[str] = []
    started = asyncio.Event()

    class ManagedTransport:
        async def __aenter__(self) -> ManagedTransport:
            events.append("transport_enter")
            return self

        async def __aexit__(self, *args: object) -> None:
            events.append("transport_exit")

        async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity:
            events.append("get_me")
            return TelegramBotIdentity(id=9001)

        async def get_raw_updates(self, token: str, **kwargs: object) -> tuple[object, ...]:
            events.append("get_updates")
            if mode == "fatal":
                raise TelegramPollingFatal("synthetic fatal")
            if mode == "cancel":
                started.set()
                await asyncio.Event().wait()
            return ()

        async def send_plain_message(
            self,
            token: str,
            chat_id: int,
            text: str,
            *,
            timeout_seconds: float,
        ) -> int:
            raise AssertionError("unexpected send")

    class Control:
        checks = 0

        def __init__(self, event: asyncio.Event) -> None:
            del event

        def is_set(self) -> bool:
            self.checks += 1
            return mode == "normal" and self.checks > 1

        async def wait(self, timeout_seconds: float) -> bool:
            del timeout_seconds
            return False

    transport = ManagedTransport()
    monkeypatch.setattr(query_module, "AsyncioTelegramPollingControl", Control)
    original_dispose = Engine.dispose

    def dispose(engine: Engine, close: bool = True) -> None:
        events.append("engine_dispose")
        original_dispose(engine, close=close)

    monkeypatch.setattr(Engine, "dispose", dispose)

    async def run() -> None:
        loop = asyncio.get_running_loop()

        def remove_signal_handler(signal_number: int) -> bool:
            events.append(f"signal_remove:{signal_number}")
            return True

        monkeypatch.setattr(
            loop,
            "add_signal_handler",
            lambda signal_number, callback: events.append(f"signal_add:{signal_number}"),
        )
        monkeypatch.setattr(
            loop,
            "remove_signal_handler",
            remove_signal_handler,
        )
        task = asyncio.create_task(
            query_module._run_cli(
                namespace,
                transport_factory=cast(
                    query_module.TelegramTransportContextFactory,
                    lambda token: transport,
                ),
                gateway_factory=lambda: (_ for _ in ()).throw(AssertionError("gateway opened")),
            )
        )
        if mode == "cancel":
            await started.wait()
            task.cancel()
        if mode == "fatal":
            with pytest.raises(TelegramPollingFatal):
                await task
        elif mode == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task

    asyncio.run(run())
    assert events.count("transport_enter") == 1
    assert events.count("transport_exit") == 1
    assert events.count("engine_dispose") == 1
    assert len([event for event in events if event.startswith("signal_add:")]) == 2
    assert len([event for event in events if event.startswith("signal_remove:")]) == 2
