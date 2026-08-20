"""Real poller/UoW integration for the Telegram read-query runner."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, func, select

import ainvest.approval.telegram_updates as polling_module
from ainvest.approval.telegram import TelegramBotIdentity, TelegramEnvironment
from ainvest.approval.telegram_updates import (
    TELEGRAM_HANDLER_DEADLINE_SECONDS,
    TelegramProviderUpdate,
    TelegramProviderUpdateKind,
)
from ainvest.config import Settings, TelegramBotSettings, TelegramRecipient
from ainvest.db import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.models import TelegramProcessedUpdateRow
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
        robinhood_read_account_number=SecretStr("synthetic-account-reference"),
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
