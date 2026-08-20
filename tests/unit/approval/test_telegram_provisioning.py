"""Offline tests for the narrow Telegram environment provisioner."""

from __future__ import annotations

import ast
import asyncio
import io
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

import ainvest.approval.telegram_provisioning as provisioning_module
from ainvest.approval.telegram import TelegramBotIdentity, TelegramChatIdentity, TelegramEnvironment
from ainvest.approval.telegram_provisioning import (
    LeasePolicy,
    ProvisioningCandidate,
    ProvisioningFailure,
    ProvisioningRequest,
    ProvisioningWebhookInfo,
    RuntimeDependencies,
    TelegramProvisioningHttpsTransport,
    TtyTokenReader,
    _candidate_from_update,
    _EnvDocument,
    _read_token_file,
    build_parser,
    execute,
    main,
)
from ainvest.db import create_all_tables, create_db_engine

STAGING_TOKEN = "123456:" + "A" * 32
STAGING_TOKEN_NEW = "123456:" + "B" * 32
PRODUCTION_TOKEN = "654321:" + "C" * 32


@dataclass
class FakeTokenReader:
    token: str

    def read(self, prompt: str) -> SecretStr:
        assert "token" in prompt.lower()
        return SecretStr(self.token)


@dataclass
class FakeSelector:
    choice: ProvisioningCandidate
    calls: int = 0

    async def select(self, candidates: tuple[ProvisioningCandidate, ...]) -> ProvisioningCandidate:
        assert self.choice in candidates
        self.calls += 1
        return self.choice


@dataclass
class FakeTransport:
    token: str
    bot_id: int
    candidate: ProvisioningCandidate
    webhook_url: str = ""
    chat_type: str = "private"
    calls: list[str] = field(default_factory=list)
    sends: int = 0

    async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity:
        assert token == self.token
        assert timeout_seconds == 5.0
        self.calls.append("get_me")
        return TelegramBotIdentity(id=self.bot_id)

    async def get_webhook_info(
        self, token: str, *, timeout_seconds: float
    ) -> ProvisioningWebhookInfo:
        assert token == self.token
        assert timeout_seconds == 5.0
        assert self.calls[0] == "get_me"
        self.calls.append("get_webhook_info")
        return ProvisioningWebhookInfo(url=self.webhook_url)

    async def discover_private_candidates(self, token: str, **kwargs: object):  # type: ignore[no-untyped-def]
        assert token == self.token
        assert kwargs == {
            "timeout_seconds": 5,
            "limit": 100,
            "allowed_updates": ("message",),
        }
        assert "offset" not in kwargs
        self.calls.append("get_updates")
        return (self.candidate,)

    async def get_chat(
        self, token: str, chat_id: int, *, timeout_seconds: float
    ) -> TelegramChatIdentity:
        assert token == self.token
        assert chat_id == self.candidate.private_chat_id
        assert timeout_seconds == 5.0
        self.calls.append("get_chat")
        return TelegramChatIdentity(id=chat_id, type=self.chat_type)

    async def send_test_message(
        self, token: str, chat_id: int, text: str, *, timeout_seconds: float
    ) -> int:
        assert token == self.token
        assert chat_id == self.candidate.private_chat_id
        assert text == "ainvest Telegram staging validation test."
        assert timeout_seconds == 5.0
        self.calls.append("send_message")
        self.sends += 1
        return 7


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "state.sqlite3"
    engine = create_db_engine(f"sqlite+pysqlite:///{path}")
    create_all_tables(engine)
    engine.dispose()
    return path


def _dependencies(
    transport: FakeTransport,
    *,
    token: str | None = None,
) -> RuntimeDependencies:
    return RuntimeDependencies(
        transport=transport,
        token_reader=FakeTokenReader(token or transport.token),
        candidate_selector=FakeSelector(transport.candidate),
        lease_policy=LeasePolicy(wait_seconds=0, lease_seconds=75, heartbeat_seconds=20),
    )


def _request(
    tmp_path: Path,
    command: str,
    *,
    environment: TelegramEnvironment = TelegramEnvironment.STAGING,
    send_test: bool = False,
) -> ProvisioningRequest:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(exist_ok=True)
    env_file = tmp_path / ".env"
    env_file.touch(exist_ok=True)
    os.chmod(env_file, 0o600)
    return ProvisioningRequest(
        command=command,
        environment=environment,
        env_file=env_file,
        secrets_dir=secrets_dir,
        database=None if command == "validate" else _database(tmp_path),
        confirm_poller_stopped=command != "validate",
        send_test=send_test,
    )


def _configured_files(tmp_path: Path, *, enabled: bool, token: str = STAGING_TOKEN) -> None:
    (tmp_path / ".env").write_text(
        "REGULAR_TRADING_HOURS_ONLY=true\n"
        f"TELEGRAM_STAGING__ENABLED={'true' if enabled else 'false'}\n"
        "TELEGRAM_STAGING__EXPECTED_BOT_ID=9001\n"
        'TELEGRAM_STAGING__ALLOWED_RECIPIENTS=[{"user_id":101,"private_chat_id":201}]\n'
        "TELEGRAM_STAGING__TRANSPORT=long_polling\n"
        "TELEGRAM_STAGING__APPROVAL_METHOD=telegram\n"
        "TELEGRAM_STAGING__APPROVAL_SCOPE=paper\n",
        encoding="utf-8",
    )
    os.chmod(tmp_path / ".env", 0o600)
    target = tmp_path / "secrets" / "TELEGRAM_STAGING__BOT_TOKEN"
    target.write_text(token + "\n", encoding="utf-8")
    os.chmod(target, 0o600)


def test_parser_has_exact_commands_and_no_token_option(tmp_path: Path) -> None:
    parser = build_parser()
    base = [
        "--environment",
        "staging",
        "--env-file",
        str(tmp_path / ".env"),
        "--secrets-dir",
        str(tmp_path),
    ]
    for command in ("add", "rotate-token", "disable"):
        parsed = parser.parse_args(
            [command, *base, "--database", str(tmp_path / "db"), "--confirm-poller-stopped"]
        )
        assert parsed.command == command
    assert parser.parse_args(["validate", *base]).command == "validate"
    with pytest.raises(ProvisioningFailure, match="invalid_cli_input"):
        parser.parse_args(["validate", *base, "--token", STAGING_TOKEN])
    with pytest.raises(ProvisioningFailure, match="invalid_cli_input"):
        parser.parse_args(["unknown", *base])


def test_main_parse_error_is_fixed_and_does_not_echo_unknown_value(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    result = main(["validate", "--token", STAGING_TOKEN])
    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err == '{"code":"invalid_cli_input","status":"error"}\n'
    assert STAGING_TOKEN not in captured.err


def test_tty_token_reader_rejects_non_tty_without_calling_getpass(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("getpass.getpass", lambda *args, **kwargs: pytest.fail("called"))
    with pytest.raises(ProvisioningFailure, match="controlling_tty_required"):
        TtyTokenReader(stdin=io.StringIO(), stderr=io.StringIO()).read("token")


def test_tty_token_reader_rejects_echo_fallback_warning(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import getpass
    import warnings

    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    def warned(*args: object, **kwargs: object) -> str:
        warnings.warn("echo fallback", getpass.GetPassWarning, stacklevel=2)
        return STAGING_TOKEN

    monkeypatch.setattr(getpass, "getpass", warned)
    with pytest.raises(ProvisioningFailure, match="token_input_cancelled"):
        TtyTokenReader(stdin=Tty(), stderr=io.StringIO()).read("token")


@pytest.mark.parametrize(
    "line",
    [
        f"TELEGRAM_STAGING__BOT_TOKEN={STAGING_TOKEN}\n",
        f"telegram_PRODUCTION__bot_TOKEN={PRODUCTION_TOKEN}\n",
        f"'TELEGRAM_STAGING__BOT_TOKEN'={STAGING_TOKEN}\n",
        f"export 'TELEGRAM_PRODUCTION__BOT_TOKEN'={PRODUCTION_TOKEN}\n",
        'TELEGRAM_STAGING={"bot_token":"not-shown"}\n',
        'telegram_production=\'{"BOT_TOKEN":"not-shown"}\'\n',
        "'TELEGRAM_STAGING'='{\"bot_token\":\"not-shown\"}'\n",
    ],
)
def test_env_rejects_every_plaintext_token_shape_without_deleting_or_disclosing(
    tmp_path: Path, line: str
) -> None:
    path = tmp_path / ".env"
    path.write_text(line, encoding="utf-8")
    with pytest.raises(ProvisioningFailure) as caught:
        _EnvDocument.read(path)
    assert "remove the plaintext" in str(caught.value)
    assert STAGING_TOKEN not in str(caught.value)
    assert PRODUCTION_TOKEN not in str(caught.value)
    assert path.read_text(encoding="utf-8") == line


def test_env_update_preserves_unrelated_bytes_order_and_crlf(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    original = b"# comment\r\nKEEP = value # exact\r\nTELEGRAM_STAGING__ENABLED=false\r\n"
    path.write_bytes(original)
    document = _EnvDocument.read(path)
    rendered = document.rendered(TelegramEnvironment.STAGING, {"ENABLED": "true"})
    assert rendered == original.replace(
        b"TELEGRAM_STAGING__ENABLED=false", b"TELEGRAM_STAGING__ENABLED=true"
    )


@pytest.mark.parametrize("control", ["\u2028", "\u0085", "\v", "\f"])
def test_env_update_preserves_non_newline_controls(tmp_path: Path, control: str) -> None:
    path = tmp_path / ".env"
    original = (
        f"# before{control}after\nTELEGRAM_STAGING__ENABLED=false\nTAIL={control}x"
    ).encode()
    path.write_bytes(original)
    rendered = _EnvDocument.read(path).rendered(TelegramEnvironment.STAGING, {"ENABLED": "true"})
    assert rendered == original.replace(
        b"TELEGRAM_STAGING__ENABLED=false", b"TELEGRAM_STAGING__ENABLED=true"
    )


def test_env_rejects_duplicate_case_variant_and_symlink(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "TELEGRAM_STAGING__ENABLED=false\ntelegram_staging__enabled=true\n",
        encoding="utf-8",
    )
    with pytest.raises(ProvisioningFailure, match="duplicate"):
        _EnvDocument.read(path)
    target = tmp_path / "real"
    target.write_text("", encoding="utf-8")
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(ProvisioningFailure, match="not_regular"):
        _EnvDocument.read(path)


def test_token_file_bounded_read_rejects_oversize_and_symlink(tmp_path: Path) -> None:
    path = tmp_path / "token"
    path.write_bytes(b"x" * 257)
    os.chmod(path, 0o600)
    with pytest.raises(ProvisioningFailure, match="token_file_invalid"):
        _read_token_file(path)
    path.unlink()
    target = tmp_path / "target"
    target.write_text(STAGING_TOKEN, encoding="utf-8")
    os.chmod(target, 0o600)
    path.symlink_to(target)
    with pytest.raises(ProvisioningFailure, match="token_file_unreadable"):
        _read_token_file(path)


def test_candidate_filter_accepts_only_original_private_messages() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=101),
        chat=SimpleNamespace(id=201, type="private"),
        forward_origin=None,
        forward_date=None,
        forward_from=None,
        forward_sender_name=None,
        new_chat_members=None,
        left_chat_member=None,
        new_chat_title=None,
        new_chat_photo=None,
        delete_chat_photo=False,
        group_chat_created=False,
        supergroup_chat_created=False,
        channel_chat_created=False,
        message_auto_delete_timer_changed=None,
        migrate_to_chat_id=None,
        migrate_from_chat_id=None,
        pinned_message=None,
        is_automatic_forward=False,
        text="ignored",
    )
    update = SimpleNamespace(message=message, edited_message=None, callback_query=None)
    assert _candidate_from_update(update) == ProvisioningCandidate(user_id=101, private_chat_id=201)
    for changed in (
        {"edited_message": message},
        {"callback_query": object()},
        {
            "message": SimpleNamespace(
                **{**vars(message), "chat": SimpleNamespace(id=201, type="group")}
            )
        },
        {"message": SimpleNamespace(**{**vars(message), "forward_origin": object()})},
        {"message": SimpleNamespace(**{**vars(message), "new_chat_members": [object()]})},
        {"message": SimpleNamespace(**{**vars(message), "from_user": None})},
    ):
        values: dict[str, object] = {
            "message": message,
            "edited_message": None,
            "callback_query": None,
        }
        values.update(changed)
        assert _candidate_from_update(SimpleNamespace(**values)) is None


@pytest.mark.parametrize(
    "field",
    [
        "via_bot",
        "sender_chat",
        "business_connection_id",
        "video_chat_started",
        "write_access_allowed",
        "users_shared",
        "gift",
    ],
)
def test_candidate_filter_rejects_current_inline_business_and_service_fields(field: str) -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=101),
        chat=SimpleNamespace(id=201, type="private"),
        text="hello",
        **{field: object()},
    )
    assert _candidate_from_update(SimpleNamespace(message=message)) is None


def test_add_is_activation_last_and_loadable_with_exact_secret(tmp_path: Path) -> None:
    request = _request(tmp_path, "add")
    request.env_file.write_text("# preserve\nREGULAR_TRADING_HOURS_ONLY=true\n", encoding="utf-8")
    transport = FakeTransport(
        token=STAGING_TOKEN,
        bot_id=9001,
        candidate=ProvisioningCandidate(user_id=101, private_chat_id=201),
    )
    result = asyncio.run(execute(request, _dependencies(transport)))
    assert result.command == "add"
    assert transport.calls == ["get_me", "get_webhook_info", "get_updates", "get_chat"]
    text = request.env_file.read_text(encoding="utf-8")
    assert text.startswith("# preserve\nREGULAR_TRADING_HOURS_ONLY=true\n")
    assert "TELEGRAM_STAGING__ENABLED=true" in text
    assert STAGING_TOKEN not in text
    target = request.secrets_dir / "TELEGRAM_STAGING__BOT_TOKEN"
    assert target.read_text(encoding="utf-8") == STAGING_TOKEN + "\n"
    assert target.stat().st_mode & 0o777 == 0o600
    assert request.env_file.stat().st_mode & 0o777 == 0o600


def test_add_rejects_webhook_and_cross_environment_token_before_write(tmp_path: Path) -> None:
    request = _request(tmp_path, "add")
    before = request.env_file.read_bytes()
    transport = FakeTransport(
        token=STAGING_TOKEN,
        bot_id=9001,
        candidate=ProvisioningCandidate(user_id=101, private_chat_id=201),
        webhook_url="https://invalid.example/webhook",
    )
    with pytest.raises(ProvisioningFailure, match="webhook_configured"):
        asyncio.run(execute(request, _dependencies(transport)))
    assert request.env_file.read_bytes() == before
    assert list(request.secrets_dir.iterdir()) == []

    other = request.secrets_dir / "TELEGRAM_PRODUCTION__BOT_TOKEN"
    other.write_text(STAGING_TOKEN + "\n", encoding="utf-8")
    os.chmod(other, 0o600)
    transport.webhook_url = ""
    with pytest.raises(ProvisioningFailure, match="cross_environment_token"):
        asyncio.run(execute(request, _dependencies(transport)))
    assert request.env_file.read_bytes() == before


def test_missing_stop_ack_never_calls_provider_or_writes(tmp_path: Path) -> None:
    request = _request(tmp_path, "add")
    request = replace(request, confirm_poller_stopped=False)
    before = request.env_file.read_bytes()
    transport = FakeTransport(
        token=STAGING_TOKEN,
        bot_id=9001,
        candidate=ProvisioningCandidate(user_id=101, private_chat_id=201),
    )
    with pytest.raises(ProvisioningFailure, match="acknowledgement"):
        asyncio.run(execute(request, _dependencies(transport)))
    assert not transport.calls
    assert request.env_file.read_bytes() == before


def test_validate_sends_nothing_by_default_and_one_only_when_explicit(tmp_path: Path) -> None:
    request = _request(tmp_path, "validate")
    _configured_files(tmp_path, enabled=True)
    transport = FakeTransport(
        token=STAGING_TOKEN,
        bot_id=9001,
        candidate=ProvisioningCandidate(user_id=101, private_chat_id=201),
    )
    asyncio.run(execute(request, _dependencies(transport)))
    assert transport.sends == 0
    explicit = replace(request, send_test=True)
    asyncio.run(execute(explicit, _dependencies(transport)))
    assert transport.sends == 1


def test_rotate_requires_same_bot_and_preserves_old_secret_on_failure(tmp_path: Path) -> None:
    request = _request(tmp_path, "rotate-token")
    _configured_files(tmp_path, enabled=False)
    mismatch = FakeTransport(
        token=STAGING_TOKEN_NEW,
        bot_id=9002,
        candidate=ProvisioningCandidate(user_id=101, private_chat_id=201),
    )
    with pytest.raises(ProvisioningFailure, match="rotation_bot_identity_mismatch"):
        asyncio.run(execute(request, _dependencies(mismatch)))
    target = request.secrets_dir / "TELEGRAM_STAGING__BOT_TOKEN"
    assert target.read_text(encoding="utf-8") == STAGING_TOKEN + "\n"
    valid = FakeTransport(
        token=STAGING_TOKEN_NEW,
        bot_id=9001,
        candidate=ProvisioningCandidate(user_id=101, private_chat_id=201),
    )
    asyncio.run(execute(request, _dependencies(valid)))
    assert target.read_text(encoding="utf-8") == STAGING_TOKEN_NEW + "\n"
    assert "TELEGRAM_STAGING__ENABLED=true" in request.env_file.read_text(encoding="utf-8")


def test_rotate_resumes_after_secret_commit_when_activation_failed(tmp_path: Path) -> None:
    request = _request(tmp_path, "rotate-token")
    _configured_files(tmp_path, enabled=False, token=STAGING_TOKEN_NEW)
    transport = FakeTransport(
        token=STAGING_TOKEN_NEW,
        bot_id=9001,
        candidate=ProvisioningCandidate(user_id=101, private_chat_id=201),
    )
    asyncio.run(execute(request, _dependencies(transport, token=STAGING_TOKEN_NEW)))
    assert "TELEGRAM_STAGING__ENABLED=true" in request.env_file.read_text(encoding="utf-8")


def test_disable_changes_only_enabled_and_keeps_secret(tmp_path: Path) -> None:
    request = _request(tmp_path, "disable")
    _configured_files(tmp_path, enabled=True)
    target = request.secrets_dir / "TELEGRAM_STAGING__BOT_TOKEN"
    before_token = target.read_bytes()
    before = request.env_file.read_text(encoding="utf-8")
    transport = FakeTransport(
        token=STAGING_TOKEN,
        bot_id=9001,
        candidate=ProvisioningCandidate(user_id=101, private_chat_id=201),
    )
    asyncio.run(execute(request, _dependencies(transport)))
    assert target.read_bytes() == before_token
    assert request.env_file.read_text(encoding="utf-8") == before.replace(
        "TELEGRAM_STAGING__ENABLED=true", "TELEGRAM_STAGING__ENABLED=false"
    )
    assert not transport.calls


def test_lazy_adapter_omits_offset_and_deduplicates_candidates(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, object]] = []
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=101),
        chat=SimpleNamespace(id=201, type="private"),
        text="hello",
    )

    class Bot:
        def __init__(self, *, token: str) -> None:
            assert token == STAGING_TOKEN

        async def get_updates(self, **kwargs: object):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            return (SimpleNamespace(message=message), SimpleNamespace(message=message))

    monkeypatch.setattr(
        "ainvest.approval.telegram_provisioning.importlib.import_module",
        lambda name: SimpleNamespace(Bot=Bot),
    )
    result = asyncio.run(
        TelegramProvisioningHttpsTransport().discover_private_candidates(
            STAGING_TOKEN,
            timeout_seconds=5,
            limit=100,
            allowed_updates=("message",),
        )
    )
    assert result == (ProvisioningCandidate(user_id=101, private_chat_id=201),)
    assert "offset" not in calls[0]
    assert calls[0]["allowed_updates"] == ("message",)


def test_results_and_failures_never_reveal_token() -> None:
    failure = ProvisioningFailure("fixed")
    assert STAGING_TOKEN not in str(failure)
    assert STAGING_TOKEN not in repr(failure)
    reader = FakeTokenReader(STAGING_TOKEN)
    assert STAGING_TOKEN not in repr(reader.read("token"))


def test_module_has_no_cursor_advance_processed_update_or_business_port() -> None:
    source = Path(provisioning_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "record_terminal" not in called_attributes
    assert "is_processed" not in called_attributes
    assert "callback_digest_seen" not in called_attributes
    assert "TelegramProcessedUpdateRow" not in source
    assert "ainvest.execution" not in source
    assert "ainvest.agents" not in source
    assert "ainvest.risk" not in source
