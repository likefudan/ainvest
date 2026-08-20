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
    _secure_token_read_flags,
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


@pytest.mark.parametrize(
    ("content", "mode", "accepted"),
    [
        (STAGING_TOKEN.encode(), 0o600, True),
        ((STAGING_TOKEN + "\n").encode(), 0o600, True),
        ((STAGING_TOKEN + "\r\n").encode(), 0o600, False),
        ((STAGING_TOKEN + "\n\n").encode(), 0o600, False),
        (b"\xff", 0o600, False),
        (STAGING_TOKEN.encode(), 0o644, False),
    ],
)
def test_token_file_exact_content_and_mode_matrix(
    tmp_path: Path, content: bytes, mode: int, accepted: bool
) -> None:
    path = tmp_path / "token"
    path.write_bytes(content)
    os.chmod(path, mode)
    if accepted:
        assert _read_token_file(path).get_secret_value() == STAGING_TOKEN
    else:
        with pytest.raises(ProvisioningFailure):
            _read_token_file(path)


def test_token_file_rejects_fifo_and_directory_without_blocking(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ProvisioningFailure):
        _read_token_file(directory)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ProvisioningFailure, match="not_regular"):
        _read_token_file(fifo)


@pytest.mark.parametrize(
    ("content", "accepted"),
    [
        (b"", False),
        (("1" * 20 + ":" + "A" * 128).encode(), True),
        (("1" * 20 + ":" + "A" * 129).encode(), False),
    ],
)
def test_token_grammar_empty_and_exact_maximum(
    tmp_path: Path, content: bytes, accepted: bool
) -> None:
    path = tmp_path / "token"
    path.write_bytes(content)
    os.chmod(path, 0o600)
    if accepted:
        assert _read_token_file(path).get_secret_value()
    else:
        with pytest.raises(ProvisioningFailure):
            _read_token_file(path)


def test_secure_open_flags_require_both_platform_guarantees(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert _secure_token_read_flags() & os.O_NOFOLLOW
    assert _secure_token_read_flags() & os.O_NONBLOCK
    monkeypatch.delattr(os, "O_NOFOLLOW")
    with pytest.raises(ProvisioningFailure, match="secure_file_open_unavailable"):
        _secure_token_read_flags()


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


def test_rotate_resumes_after_real_secret_commit_and_activation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(tmp_path, "rotate-token")
    _configured_files(tmp_path, enabled=False, token=STAGING_TOKEN)
    transport = FakeTransport(
        token=STAGING_TOKEN_NEW,
        bot_id=9001,
        candidate=ProvisioningCandidate(user_id=101, private_chat_id=201),
    )
    original = provisioning_module._atomic_replace
    replacements = 0

    def fail_activation(path: Path, content: bytes) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise ProvisioningFailure("injected_activation_failure")
        original(path, content)

    monkeypatch.setattr(provisioning_module, "_atomic_replace", fail_activation)
    with pytest.raises(ProvisioningFailure, match="injected_activation_failure"):
        asyncio.run(execute(request, _dependencies(transport, token=STAGING_TOKEN_NEW)))
    target = request.secrets_dir / "TELEGRAM_STAGING__BOT_TOKEN"
    assert target.read_text(encoding="utf-8") == STAGING_TOKEN_NEW + "\n"
    assert "TELEGRAM_STAGING__ENABLED=false" in request.env_file.read_text(encoding="utf-8")
    monkeypatch.setattr(provisioning_module, "_atomic_replace", original)
    resumed_paths: list[Path] = []

    def spy(path: Path, content: bytes) -> None:
        resumed_paths.append(path)
        original(path, content)

    monkeypatch.setattr(provisioning_module, "_atomic_replace", spy)
    asyncio.run(execute(request, _dependencies(transport, token=STAGING_TOKEN_NEW)))
    assert resumed_paths == [request.env_file]
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


def test_lazy_adapter_uses_exact_read_and_send_arguments(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, dict[str, object]]] = []

    class Bot:
        def __init__(self, *, token: str) -> None:
            assert token == STAGING_TOKEN

        async def get_me(self, **kwargs: object):  # type: ignore[no-untyped-def]
            calls.append(("get_me", kwargs))
            return SimpleNamespace(id=9001)

        async def get_webhook_info(self, **kwargs: object):  # type: ignore[no-untyped-def]
            calls.append(("get_webhook_info", kwargs))
            return SimpleNamespace(url="")

        async def get_chat(self, **kwargs: object):  # type: ignore[no-untyped-def]
            calls.append(("get_chat", kwargs))
            return SimpleNamespace(id=201, type="private")

        async def send_message(self, **kwargs: object):  # type: ignore[no-untyped-def]
            calls.append(("send_message", kwargs))
            return SimpleNamespace(message_id=7)

    monkeypatch.setattr(
        "ainvest.approval.telegram_provisioning.importlib.import_module",
        lambda name: SimpleNamespace(Bot=Bot),
    )
    adapter = TelegramProvisioningHttpsTransport()

    async def run() -> None:
        assert (await adapter.get_me(STAGING_TOKEN, timeout_seconds=5)).id == 9001
        assert (await adapter.get_webhook_info(STAGING_TOKEN, timeout_seconds=5)).url == ""
        assert (await adapter.get_chat(STAGING_TOKEN, 201, timeout_seconds=5)).type == "private"
        assert await adapter.send_test_message(STAGING_TOKEN, 201, "fixed", timeout_seconds=5) == 7

    asyncio.run(run())
    assert [name for name, _ in calls] == [
        "get_me",
        "get_webhook_info",
        "get_chat",
        "send_message",
    ]
    assert calls[2][1]["chat_id"] == 201
    timeout_kwargs = {
        "read_timeout": 5,
        "write_timeout": 5,
        "connect_timeout": 5,
        "pool_timeout": 5,
    }
    assert calls[0][1] == timeout_kwargs
    assert calls[1][1] == timeout_kwargs
    assert calls[2][1] == {"chat_id": 201, **timeout_kwargs}
    assert calls[3][1]["parse_mode"] is None
    assert calls[3][1]["text"] == "fixed"
    assert calls[3][1] == {
        "chat_id": 201,
        "text": "fixed",
        "parse_mode": None,
        **timeout_kwargs,
    }


@pytest.mark.parametrize(
    ("method", "expected_code", "malformed"),
    [
        ("get_me", "provider_identity_failed", True),
        ("get_me", "provider_identity_failed", False),
        ("get_webhook_info", "provider_webhook_check_failed", True),
        ("get_webhook_info", "provider_webhook_check_failed", False),
        ("get_chat", "provider_chat_check_failed", True),
        ("get_chat", "provider_chat_check_failed", False),
    ],
)
def test_lazy_adapter_read_failures_are_one_call_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    expected_code: str,
    malformed: bool,
) -> None:
    calls: list[str] = []

    class Bot:
        def __init__(self, *, token: str) -> None:
            assert token == STAGING_TOKEN

        async def get_me(self, **kwargs: object):  # type: ignore[no-untyped-def]
            calls.append("get_me")
            if not malformed:
                raise RuntimeError(f"provider-payload {STAGING_TOKEN}")
            return SimpleNamespace(id="provider-payload")

        async def get_webhook_info(self, **kwargs: object):  # type: ignore[no-untyped-def]
            calls.append("get_webhook_info")
            if not malformed:
                raise RuntimeError(f"provider-payload {STAGING_TOKEN}")
            return SimpleNamespace(url={"provider": "payload"})

        async def get_chat(self, **kwargs: object):  # type: ignore[no-untyped-def]
            calls.append("get_chat")
            if not malformed:
                raise RuntimeError(f"provider-payload {STAGING_TOKEN}")
            return SimpleNamespace(id="provider-payload", type={"provider": "payload"})

    monkeypatch.setattr(
        "ainvest.approval.telegram_provisioning.importlib.import_module",
        lambda name: SimpleNamespace(Bot=Bot),
    )
    adapter = TelegramProvisioningHttpsTransport()

    async def run() -> object:
        if method == "get_me":
            return await adapter.get_me(STAGING_TOKEN, timeout_seconds=5)
        if method == "get_webhook_info":
            return await adapter.get_webhook_info(STAGING_TOKEN, timeout_seconds=5)
        return await adapter.get_chat(STAGING_TOKEN, 201, timeout_seconds=5)

    with pytest.raises(ProvisioningFailure, match=expected_code) as caught:
        asyncio.run(run())
    assert calls == [method]
    assert caught.value.code == expected_code
    assert "provider-payload" not in str(caught.value)
    assert STAGING_TOKEN not in str(caught.value)


@pytest.mark.parametrize(
    "failure", [TimeoutError(), RuntimeError("provider detail"), asyncio.CancelledError()]
)
def test_lazy_adapter_send_failure_is_one_call_unknown_and_sanitized(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    calls = 0

    class Bot:
        def __init__(self, *, token: str) -> None:
            pass

        async def send_message(self, **kwargs: object):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            raise failure

    monkeypatch.setattr(
        "ainvest.approval.telegram_provisioning.importlib.import_module",
        lambda name: SimpleNamespace(Bot=Bot),
    )
    with pytest.raises(ProvisioningFailure, match="test_delivery_unknown") as caught:
        asyncio.run(
            TelegramProvisioningHttpsTransport().send_test_message(
                STAGING_TOKEN, 201, "fixed", timeout_seconds=5
            )
        )
    assert calls == 1
    assert "provider detail" not in str(caught.value)


def test_lazy_adapter_missing_dependency_is_sanitized(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "ainvest.approval.telegram_provisioning.importlib.import_module",
        lambda name: (_ for _ in ()).throw(ImportError()),
    )
    with pytest.raises(ProvisioningFailure, match="provider_unavailable"):
        asyncio.run(TelegramProvisioningHttpsTransport().get_me(STAGING_TOKEN, timeout_seconds=5))


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
