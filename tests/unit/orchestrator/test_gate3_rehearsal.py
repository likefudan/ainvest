"""Tests for the bounded staging Telegram -> Paper rehearsal composition."""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from ainvest.approval.telegram import (
    TelegramBotIdentity,
    TelegramChatIdentity,
    TelegramOutboundAction,
)
from ainvest.approval.telegram_updates import (
    TelegramProviderUpdate,
    TelegramProviderUpdateKind,
)
from ainvest.config import AinvestEnv, Settings, TelegramBotSettings, TelegramRecipient
from ainvest.db import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.models import ApprovalEventRow, ApprovalOutboxRow, AuditEventRow
from ainvest.orchestrator.gate3_rehearsal import (
    Gate3RehearsalFailure,
    build_parser,
    run_gate3_rehearsal,
)

TOKEN = "900000001:" + "A" * 35
NOW = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)


class FakeTelegramTransport:
    def __init__(self) -> None:
        self.callback_data: str | None = None
        self.message_id = 303
        self.update_sent = False
        self.answers: list[str] = []

    async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity:
        assert token == TOKEN
        assert timeout_seconds > 0
        return TelegramBotIdentity(id=900000001)

    async def get_chat(
        self, token: str, chat_id: int, *, timeout_seconds: float
    ) -> TelegramChatIdentity:
        assert token == TOKEN
        assert chat_id == 202
        assert timeout_seconds > 0
        return TelegramChatIdentity(id=202, type="private")

    async def send_message(
        self,
        token: str,
        chat_id: int,
        text: str,
        action: TelegramOutboundAction,
        *,
        timeout_seconds: float,
    ) -> int:
        assert token == TOKEN
        assert chat_id == 202
        assert text
        assert timeout_seconds > 0
        self.callback_data, link = action.reveal()
        assert link is None
        return self.message_id

    async def get_updates(self, token: str, **kwargs: object) -> tuple[TelegramProviderUpdate, ...]:
        assert token == TOKEN
        assert kwargs["offset"] == 0
        if self.update_sent:
            await asyncio.sleep(0)
            return ()
        self.update_sent = True
        assert self.callback_data is not None
        return (
            TelegramProviderUpdate(
                update_id=404,
                kind=TelegramProviderUpdateKind.CALLBACK,
                sender_user_id=101,
                chat_id=202,
                message_id=self.message_id,
                chat_type="private",
                callback_query_id=SecretStr("gate3-callback-01"),
                callback_data=SecretStr(self.callback_data),
            ),
        )

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str,
        *,
        timeout_seconds: float,
    ) -> None:
        assert callback_query_id == "gate3-callback-01"
        assert timeout_seconds > 0
        self.answers.append(text)


def _settings(*, environment: AinvestEnv = AinvestEnv.STAGING) -> Settings:
    return Settings(
        ainvest_env=environment,
        telegram_staging=TelegramBotSettings(
            enabled=True,
            bot_token=SecretStr(TOKEN),
            expected_bot_id=900000001,
            allowed_recipients=(TelegramRecipient(user_id=101, private_chat_id=202),),
        ),
    )


@pytest.mark.unit
def test_real_composition_records_one_paper_fill_and_blocks_replay(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'gate3.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    transport = FakeTelegramTransport()

    result = asyncio.run(
        run_gate3_rehearsal(
            settings=_settings(),
            session_factory=factory,
            notification_transport=transport,
            identity_transport=transport,
            update_transport=transport,
            answer_transport=transport,
            timeout_seconds=60,
            clock=lambda: NOW,
        )
    )

    assert result.replay_blocked is True
    assert result.proposal_id.startswith("ordp_")
    assert result.approval_event_id.startswith("apev_")
    assert result.paper_broker_order_id.startswith("paper_")
    assert transport.answers == ["Paper approval recorded."]
    with factory() as session:
        event = session.scalar(select(ApprovalEventRow))
        outbox = session.scalar(select(ApprovalOutboxRow))
        audits = tuple(session.scalars(select(AuditEventRow)))
        assert event is not None and outbox is not None
        assert (event.method, event.scope, event.outcome) == ("telegram", "paper", "APPROVED")
        assert outbox.status == "CONSUMED"
        persisted = (
            f"{event.payload_json}{outbox.payload_json}{[row.payload_json for row in audits]}"
        )
        assert TOKEN not in persisted
        assert transport.callback_data is not None
        assert transport.callback_data not in persisted


@pytest.mark.unit
def test_rehearsal_rejects_non_staging_and_ambiguous_recipient(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'rejected.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    transport = FakeTelegramTransport()

    with pytest.raises(Gate3RehearsalFailure):
        asyncio.run(
            run_gate3_rehearsal(
                settings=_settings(environment=AinvestEnv.DEVELOPMENT),
                session_factory=factory,
                notification_transport=transport,
                identity_transport=transport,
                update_transport=transport,
                answer_transport=transport,
                timeout_seconds=60,
                clock=lambda: NOW,
            )
        )

    ambiguous = _settings().model_copy(
        update={
            "telegram_staging": _settings().telegram_staging.model_copy(
                update={
                    "allowed_recipients": (
                        TelegramRecipient(user_id=101, private_chat_id=202),
                        TelegramRecipient(user_id=111, private_chat_id=212),
                    )
                }
            )
        }
    )
    with pytest.raises(Gate3RehearsalFailure):
        asyncio.run(
            run_gate3_rehearsal(
                settings=ambiguous,
                session_factory=factory,
                notification_transport=transport,
                identity_transport=transport,
                update_transport=transport,
                answer_transport=transport,
                timeout_seconds=60,
                clock=lambda: NOW,
            )
        )


@pytest.mark.unit
def test_poller_failure_returns_without_waiting_for_approval_timeout(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'poller-failed.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)

    class IdentityChangesAfterSend(FakeTelegramTransport):
        identity_calls = 0

        async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity:
            identity = await super().get_me(token, timeout_seconds=timeout_seconds)
            self.identity_calls += 1
            return identity if self.identity_calls == 1 else TelegramBotIdentity(id=900000002)

    transport = IdentityChangesAfterSend()

    async def exercise() -> None:
        with pytest.raises(Gate3RehearsalFailure, match="poller_failed"):
            await run_gate3_rehearsal(
                settings=_settings(),
                session_factory=factory,
                notification_transport=transport,
                identity_transport=transport,
                update_transport=transport,
                answer_transport=transport,
                timeout_seconds=60,
                clock=lambda: NOW,
            )

    asyncio.run(asyncio.wait_for(exercise(), timeout=1))


@pytest.mark.unit
def test_cli_requires_explicit_paths_and_stopped_poller_confirmation(tmp_path: Path) -> None:
    parsed = build_parser().parse_args(
        [
            "--env-file",
            str(tmp_path / "staging.env"),
            "--secrets-dir",
            str(tmp_path / "secrets"),
            "--database",
            str(tmp_path / "staging.sqlite3"),
            "--confirm-poller-stopped",
        ]
    )
    assert parsed.timeout_seconds == 100.0
    assert parsed.confirm_poller_stopped is True
    with pytest.raises(Gate3RehearsalFailure):
        build_parser().parse_args(
            [
                "--env-file",
                str(tmp_path / "staging.env"),
                "--secrets-dir",
                str(tmp_path / "secrets"),
                "--database",
                str(tmp_path / "staging.sqlite3"),
            ]
        )


@pytest.mark.unit
def test_composition_has_no_robinhood_or_live_execution_import() -> None:
    source_path = Path(__file__).parents[3] / "src/ainvest/orchestrator/gate3_rehearsal.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(
        "robinhood" in name.casefold() or "rh_mcp" in name.casefold() for name in imported
    )
    assert "AccountScope.AGENTIC" not in source_path.read_text(encoding="utf-8")
