"""Filesystem plus real-SQLite proof for Telegram provisioning."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, update

from ainvest.approval.telegram import TelegramBotIdentity, TelegramChatIdentity, TelegramEnvironment
from ainvest.approval.telegram_provisioning import (
    LeasePolicy,
    ProvisioningCandidate,
    ProvisioningFailure,
    ProvisioningRequest,
    ProvisioningWebhookInfo,
    RuntimeDependencies,
    execute,
)
from ainvest.config import load_settings
from ainvest.db import TelegramUpdateRepository, create_all_tables, create_db_engine
from ainvest.db.models import TelegramPollStateRow, TelegramProcessedUpdateRow
from ainvest.db.session import create_session_factory

TOKEN = "123456:" + "A" * 32


@dataclass
class TokenReader:
    token: str = TOKEN

    def read(self, prompt: str) -> SecretStr:
        assert prompt
        return SecretStr(self.token)


@dataclass
class Selector:
    async def select(self, candidates: tuple[ProvisioningCandidate, ...]) -> ProvisioningCandidate:
        assert candidates == (ProvisioningCandidate(user_id=101, private_chat_id=201),)
        return candidates[0]


@dataclass
class Transport:
    token: str = TOKEN
    bot_id: int = 9001
    sends: int = 0

    async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity:
        assert token == self.token
        return TelegramBotIdentity(id=self.bot_id)

    async def get_webhook_info(
        self, token: str, *, timeout_seconds: float
    ) -> ProvisioningWebhookInfo:
        assert token == self.token
        return ProvisioningWebhookInfo(url="")

    async def discover_private_candidates(self, token: str, **kwargs: object):  # type: ignore[no-untyped-def]
        assert token == self.token
        assert "offset" not in kwargs
        return (ProvisioningCandidate(user_id=101, private_chat_id=201),)

    async def get_chat(
        self, token: str, chat_id: int, *, timeout_seconds: float
    ) -> TelegramChatIdentity:
        assert token == self.token
        return TelegramChatIdentity(id=chat_id, type="private")

    async def send_test_message(
        self, token: str, chat_id: int, text: str, *, timeout_seconds: float
    ) -> int:
        self.sends += 1
        return 1


def test_add_preserves_existing_offset_and_never_processes_discovery_updates(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("# exact\nREGULAR_TRADING_HOURS_ONLY=true\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    database = tmp_path / "state.sqlite3"
    engine = create_db_engine(f"sqlite+pysqlite:///{database}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    with factory.begin() as session:
        repository = TelegramUpdateRepository(session)
        repository.ensure_state("staging")
        # Use the repository's terminal method once to create a non-zero
        # pre-existing cursor; provisioning must preserve it exactly.
        lease = repository.acquire_lease(
            "staging",
            owner="fixture",
            now=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )
        assert lease is not None
        state = repository.record_terminal(
            "staging",
            owner="fixture",
            epoch=lease.lease_epoch,
            version=lease.version,
            now=datetime.now(UTC),
            update_id=40,
            kind="ignored",
            disposition="ignored",
            callback_query_digest=None,
        )
        assert state is not None
        assert repository.release_lease(
            "staging", owner="fixture", epoch=state.lease_epoch, version=state.version
        )
    processed_before: int
    with factory() as session:
        processed_before = session.scalar(select(func.count(TelegramProcessedUpdateRow.id))) or 0
    transport = Transport()
    request = ProvisioningRequest(
        command="add",
        environment=TelegramEnvironment.STAGING,
        env_file=env_file,
        secrets_dir=secrets_dir,
        database=database,
        confirm_poller_stopped=True,
    )
    dependencies = RuntimeDependencies(
        transport=transport,
        token_reader=TokenReader(),
        candidate_selector=Selector(),
        lease_policy=LeasePolicy(wait_seconds=0),
    )
    asyncio.run(execute(request, dependencies))

    settings = load_settings(environ={}, env_file=env_file, secrets_dir=secrets_dir)
    assert settings.telegram_staging.enabled is True
    assert settings.telegram_staging.expected_bot_id == 9001
    assert settings.telegram_staging.allowed_recipients[0].user_id == 101
    assert settings.telegram_production.enabled is False
    assert env_file.read_text(encoding="utf-8").startswith(
        "# exact\nREGULAR_TRADING_HOURS_ONLY=true\n"
    )
    with factory() as session:
        state = TelegramUpdateRepository(session).get_state("staging")
        assert state is not None
        assert state.next_offset == 41
        assert state.lease_owner is None
        processed_after = session.scalar(select(func.count(TelegramProcessedUpdateRow.id))) or 0
    assert processed_after == processed_before
    assert transport.sends == 0
    engine.dispose()


def test_execute_provisions_both_bots_for_same_owner_with_distinct_identity(
    tmp_path: Path,
) -> None:
    production_token = "654321:" + "B" * 32
    env_file = tmp_path / ".env"
    env_file.write_text("REGULAR_TRADING_HOURS_ONLY=true\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    database = tmp_path / "state.sqlite3"
    engine = create_db_engine(f"sqlite+pysqlite:///{database}")
    create_all_tables(engine)
    policy = LeasePolicy(wait_seconds=0)
    for environment, token, bot_id in (
        (TelegramEnvironment.STAGING, TOKEN, 9001),
        (TelegramEnvironment.PRODUCTION, production_token, 9002),
    ):
        asyncio.run(
            execute(
                ProvisioningRequest(
                    command="add",
                    environment=environment,
                    env_file=env_file,
                    secrets_dir=secrets_dir,
                    database=database,
                    confirm_poller_stopped=True,
                ),
                RuntimeDependencies(
                    transport=Transport(token=token, bot_id=bot_id),
                    token_reader=TokenReader(token),
                    candidate_selector=Selector(),
                    lease_policy=policy,
                ),
            )
        )
    settings = load_settings(environ={}, env_file=env_file, secrets_dir=secrets_dir)
    assert settings.telegram_staging.expected_bot_id == 9001
    assert settings.telegram_production.expected_bot_id == 9002
    assert (
        settings.telegram_staging.allowed_recipients
        == settings.telegram_production.allowed_recipients
    )
    with create_session_factory(engine)() as session:
        production = TelegramUpdateRepository(session).get_state("production")
        assert production is not None and production.next_offset == 0
        assert session.scalar(select(func.count(TelegramProcessedUpdateRow.id))) == 0
    engine.dispose()


def test_active_lease_prevents_every_file_write(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_STAGING__ENABLED=true\n"
        "TELEGRAM_STAGING__EXPECTED_BOT_ID=9001\n"
        'TELEGRAM_STAGING__ALLOWED_RECIPIENTS=[{"user_id":101,"private_chat_id":201}]\n'
        "TELEGRAM_STAGING__TRANSPORT=long_polling\n"
        "TELEGRAM_STAGING__APPROVAL_METHOD=telegram\n"
        "TELEGRAM_STAGING__APPROVAL_SCOPE=paper\n",
        encoding="utf-8",
    )
    os.chmod(env_file, 0o600)
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    secret = secrets_dir / "TELEGRAM_STAGING__BOT_TOKEN"
    secret.write_text(TOKEN + "\n", encoding="utf-8")
    os.chmod(secret, 0o600)
    database = tmp_path / "state.sqlite3"
    engine = create_db_engine(f"sqlite+pysqlite:///{database}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory.begin() as session:
        lease = TelegramUpdateRepository(session).acquire_lease(
            "staging",
            owner="running-poller",
            now=now,
            expires_at=now + timedelta(seconds=60),
        )
        assert lease is not None
    before_env = env_file.read_bytes()
    before_secret = secret.read_bytes()
    request = ProvisioningRequest(
        command="disable",
        environment=TelegramEnvironment.STAGING,
        env_file=env_file,
        secrets_dir=secrets_dir,
        database=database,
        confirm_poller_stopped=True,
    )
    dependencies = RuntimeDependencies(
        transport=Transport(),
        token_reader=TokenReader(),
        candidate_selector=Selector(),
        lease_policy=LeasePolicy(wait_seconds=0),
    )
    with pytest.raises(ProvisioningFailure, match="maintenance_lease_busy"):
        asyncio.run(execute(request, dependencies))
    assert env_file.read_bytes() == before_env
    assert secret.read_bytes() == before_secret
    engine.dispose()


def test_takeover_after_prewrite_check_cannot_be_claimed_as_filesystem_fencing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ainvest.approval import telegram_provisioning

    env_file = tmp_path / ".env"
    env_file.write_text("REGULAR_TRADING_HOURS_ONLY=true\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    database = tmp_path / "state.sqlite3"
    engine = create_db_engine(f"sqlite+pysqlite:///{database}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    original_replace = telegram_provisioning._atomic_replace
    replacements = 0

    def replace_then_take_over(path: Path, content: bytes) -> None:
        nonlocal replacements
        original_replace(path, content)
        replacements += 1
        if replacements == 1:
            with factory.begin() as session:
                session.execute(
                    update(TelegramPollStateRow)
                    .where(TelegramPollStateRow.environment == "staging")
                    .values(
                        lease_owner="takeover",
                        lease_epoch=TelegramPollStateRow.lease_epoch + 1,
                        version=TelegramPollStateRow.version + 1,
                        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
                    )
                )

    monkeypatch.setattr(telegram_provisioning, "_atomic_replace", replace_then_take_over)
    request = ProvisioningRequest(
        command="add",
        environment=TelegramEnvironment.STAGING,
        env_file=env_file,
        secrets_dir=secrets_dir,
        database=database,
        confirm_poller_stopped=True,
    )
    transport = Transport()
    dependencies = RuntimeDependencies(
        transport=transport,
        token_reader=TokenReader(),
        candidate_selector=Selector(),
        lease_policy=LeasePolicy(wait_seconds=0),
    )
    with pytest.raises(ProvisioningFailure, match="maintenance_lease_lost"):
        asyncio.run(execute(request, dependencies))
    assert replacements == 1
    assert "TELEGRAM_STAGING__ENABLED=false" in env_file.read_text(encoding="utf-8")
    assert not (secrets_dir / "TELEGRAM_STAGING__BOT_TOKEN").exists()
    engine.dispose()
