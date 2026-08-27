"""Fail-closed local provisioning for the two Telegram Bot environments.

This module is deliberately an operator utility, not a general administration
surface.  It owns four commands, never accepts a token in command-line input,
and has no approval, query, broker, or update-confirmation capability.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib
import io
import json
import os
import secrets
import stat
import sys
import tempfile
import warnings
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Never, Protocol, TextIO, cast

from dotenv.parser import parse_stream
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.telegram import TelegramBotIdentity, TelegramChatIdentity, TelegramEnvironment
from ainvest.approval.telegram_maintenance import (
    TelegramMaintenanceLeaseError,
    TelegramMaintenanceLeasePolicy,
    TelegramPollingMaintenanceLease,
)
from ainvest.config import Settings, TelegramBotSettings, load_settings
from ainvest.config.settings import (
    MAX_TELEGRAM_TOKEN_FILE_BYTES,
    TELEGRAM_BOT_TOKEN_FILENAMES,
    TELEGRAM_BOT_TOKEN_PATTERN,
)
from ainvest.db import create_db_engine

_INT64_MAX: Final[int] = 2**63 - 1
_PROVIDER_TIMEOUT_SECONDS: Final[float] = 5.0
_DISCOVERY_TIMEOUT_SECONDS: Final[int] = 5
_DISCOVERY_LIMIT: Final[int] = 100
_TOKEN_REMEDIATION: Final[str] = (
    "remove the plaintext Telegram bot-token assignment manually and retry"
)
_MANAGED_SUFFIXES: Final[tuple[str, ...]] = (
    "ENABLED",
    "EXPECTED_BOT_ID",
    "ALLOWED_RECIPIENTS",
    "TRANSPORT",
    "APPROVAL_METHOD",
    "APPROVAL_SCOPE",
)


class ProvisioningFailure(Exception):
    """A stable, sanitized operator failure with no provider or secret data."""

    def __init__(self, code: str, *, remediation: str | None = None) -> None:
        self.code = code
        self.remediation = remediation
        message = code if remediation is None else f"{code}: {remediation}"
        super().__init__(message)

    def __repr__(self) -> str:
        return f"ProvisioningFailure(code={self.code!r}, remediation={self.remediation!r})"


class ProvisioningWebhookInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str


class ProvisioningCandidate(BaseModel):
    """One numeric user/private-chat pair observed through the selected Bot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: StrictInt = Field(gt=0, le=_INT64_MAX)
    private_chat_id: StrictInt = Field(gt=0, le=_INT64_MAX)


class ProvisioningTransport(Protocol):
    """Narrow provider port; it contains no webhook mutation or update offset."""

    async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity: ...

    async def get_webhook_info(
        self, token: str, *, timeout_seconds: float
    ) -> ProvisioningWebhookInfo: ...

    async def discover_private_candidates(
        self,
        token: str,
        *,
        timeout_seconds: int,
        limit: int,
        allowed_updates: tuple[str, ...],
    ) -> tuple[ProvisioningCandidate, ...]: ...

    async def get_chat(
        self, token: str, chat_id: int, *, timeout_seconds: float
    ) -> TelegramChatIdentity: ...

    async def send_test_message(
        self, token: str, chat_id: int, text: str, *, timeout_seconds: float
    ) -> int: ...


class TelegramProvisioningHttpsTransport:
    """Lazy ``python-telegram-bot`` adapter for provisioning-only methods."""

    async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity:
        bot = self._bot(token)
        try:
            result = await bot.get_me(**_timeouts(timeout_seconds))
            return TelegramBotIdentity(id=result.id)
        except Exception:
            raise ProvisioningFailure("provider_identity_failed") from None

    async def get_webhook_info(
        self, token: str, *, timeout_seconds: float
    ) -> ProvisioningWebhookInfo:
        bot = self._bot(token)
        try:
            result = await bot.get_webhook_info(**_timeouts(timeout_seconds))
            return ProvisioningWebhookInfo(url=result.url)
        except Exception:
            raise ProvisioningFailure("provider_webhook_check_failed") from None

    async def discover_private_candidates(
        self,
        token: str,
        *,
        timeout_seconds: int,
        limit: int,
        allowed_updates: tuple[str, ...],
    ) -> tuple[ProvisioningCandidate, ...]:
        bot = self._bot(token)
        try:
            # Deliberately no offset keyword: discovery does not confirm an update.
            updates = await bot.get_updates(
                timeout=timeout_seconds,
                limit=limit,
                allowed_updates=allowed_updates,
                **_timeouts(float(timeout_seconds + 2)),
            )
        except Exception:
            raise ProvisioningFailure("provider_discovery_failed") from None
        try:
            if not isinstance(updates, (list, tuple)) or len(updates) > limit:
                raise ValueError
            candidates = {
                candidate
                for update in updates
                if (candidate := _candidate_from_update(update)) is not None
            }
        except Exception:
            raise ProvisioningFailure("provider_discovery_failed") from None
        return tuple(sorted(candidates, key=lambda item: (item.user_id, item.private_chat_id)))

    async def get_chat(
        self, token: str, chat_id: int, *, timeout_seconds: float
    ) -> TelegramChatIdentity:
        bot = self._bot(token)
        try:
            result = await bot.get_chat(chat_id=chat_id, **_timeouts(timeout_seconds))
            return TelegramChatIdentity(id=result.id, type=result.type)
        except Exception:
            raise ProvisioningFailure("provider_chat_check_failed") from None

    async def send_test_message(
        self, token: str, chat_id: int, text: str, *, timeout_seconds: float
    ) -> int:
        bot = self._bot(token)
        try:
            result = await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=None,
                **_timeouts(timeout_seconds),
            )
        except BaseException:
            # Once send begins, every ambiguous failure is terminal.  The caller
            # never retries this method.
            raise ProvisioningFailure("test_delivery_unknown") from None
        message_id = getattr(result, "message_id", None)
        if not _positive_int64(message_id):
            raise ProvisioningFailure("test_delivery_unknown")
        return cast(int, message_id)

    @staticmethod
    def _bot(token: str) -> Any:
        try:
            telegram = importlib.import_module("telegram")
            return telegram.Bot(token=token)
        except Exception:
            raise ProvisioningFailure("provider_unavailable") from None


def _timeouts(value: float) -> dict[str, float]:
    return {
        "read_timeout": value,
        "write_timeout": value,
        "connect_timeout": value,
        "pool_timeout": value,
    }


def _positive_int64(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= _INT64_MAX


def _candidate_from_update(update: object) -> ProvisioningCandidate | None:
    """Return only an original, non-service private ``message`` pair."""
    if getattr(update, "edited_message", None) is not None:
        return None
    message = getattr(update, "message", None)
    if message is None or getattr(update, "callback_query", None) is not None:
        return None
    chat = getattr(message, "chat", None)
    sender = getattr(message, "from_user", None)
    if chat is None or sender is None or getattr(chat, "type", None) != "private":
        return None
    if not isinstance(getattr(message, "text", None), str) or not message.text:
        return None
    if any(
        bool(getattr(message, field, None))
        for field in (
            "forward_origin",
            "forward_date",
            "forward_from",
            "forward_sender_name",
            "new_chat_members",
            "left_chat_member",
            "new_chat_title",
            "new_chat_photo",
            "delete_chat_photo",
            "group_chat_created",
            "supergroup_chat_created",
            "channel_chat_created",
            "message_auto_delete_timer_changed",
            "migrate_to_chat_id",
            "migrate_from_chat_id",
            "pinned_message",
            "via_bot",
            "sender_chat",
            "business_connection_id",
            "video_chat_started",
            "video_chat_ended",
            "video_chat_participants_invited",
            "video_chat_scheduled",
            "write_access_allowed",
            "users_shared",
            "chat_shared",
            "gift",
            "unique_gift",
            "giveaway",
            "giveaway_created",
            "giveaway_completed",
            "giveaway_winners",
            "boost_added",
            "forum_topic_created",
            "forum_topic_closed",
            "forum_topic_edited",
            "forum_topic_reopened",
            "general_forum_topic_hidden",
            "general_forum_topic_unhidden",
            "proximity_alert_triggered",
            "web_app_data",
        )
    ) or getattr(message, "is_automatic_forward", False):
        return None
    if not _positive_int64(getattr(sender, "id", None)) or not _positive_int64(
        getattr(chat, "id", None)
    ):
        return None
    try:
        return ProvisioningCandidate(user_id=sender.id, private_chat_id=chat.id)
    except ValidationError:
        return None


class TokenReader(Protocol):
    def read(self, prompt: str) -> SecretStr: ...


class CandidateSelector(Protocol):
    async def select(
        self, candidates: tuple[ProvisioningCandidate, ...]
    ) -> ProvisioningCandidate: ...


class TtyTokenReader:
    """Read exactly one token from a controlling TTY without echo."""

    def __init__(self, *, stdin: TextIO = sys.stdin, stderr: TextIO = sys.stderr) -> None:
        self._stdin = stdin
        self._stderr = stderr

    def read(self, prompt: str) -> SecretStr:
        if not self._stdin.isatty():
            raise ProvisioningFailure("controlling_tty_required")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = getpass.getpass(prompt, stream=self._stderr)
        except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
            raise ProvisioningFailure("token_input_cancelled") from None
        if TELEGRAM_BOT_TOKEN_PATTERN.fullmatch(value) is None:
            raise ProvisioningFailure("token_invalid")
        return SecretStr(value)


class TtyCandidateSelector:
    def __init__(self, *, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        self._stdin = stdin
        self._stdout = stdout

    async def select(self, candidates: tuple[ProvisioningCandidate, ...]) -> ProvisioningCandidate:
        if not self._stdin.isatty():
            raise ProvisioningFailure("controlling_tty_required")
        return await asyncio.to_thread(self._select_blocking, candidates)

    def _select_blocking(
        self, candidates: tuple[ProvisioningCandidate, ...]
    ) -> ProvisioningCandidate:
        for index, candidate in enumerate(candidates, start=1):
            self._stdout.write(
                f"{index}: user_id={candidate.user_id} "
                f"private_chat_id={candidate.private_chat_id}\n"
            )
        self._stdout.write("Select candidate number: ")
        self._stdout.flush()
        selection = self._stdin.readline().strip()
        if not selection.isascii() or not selection.isdigit():
            raise ProvisioningFailure("recipient_selection_invalid")
        index = int(selection)
        if index < 1 or index > len(candidates):
            raise ProvisioningFailure("recipient_selection_invalid")
        chosen = candidates[index - 1]
        self._stdout.write(
            "Confirm exact pair as user_id:private_chat_id "
            f"({chosen.user_id}:{chosen.private_chat_id}): "
        )
        self._stdout.flush()
        confirmation = self._stdin.readline().strip()
        if confirmation != f"{chosen.user_id}:{chosen.private_chat_id}":
            raise ProvisioningFailure("recipient_confirmation_failed")
        return chosen


@dataclass(frozen=True, slots=True)
class ProvisioningRequest:
    command: str
    environment: TelegramEnvironment
    env_file: Path
    secrets_dir: Path
    database: Path | None = None
    confirm_poller_stopped: bool = False
    send_test: bool = False


@dataclass(frozen=True, slots=True)
class ProvisioningResult:
    command: str
    environment: TelegramEnvironment
    test_message_sent: bool = False

    def as_json(self) -> str:
        return json.dumps(
            {
                "command": self.command,
                "environment": self.environment.value,
                "status": "ok",
                "test_message_sent": self.test_message_sent,
            },
            separators=(",", ":"),
            sort_keys=True,
        )


LeasePolicy = TelegramMaintenanceLeasePolicy


@dataclass(frozen=True, slots=True)
class RuntimeDependencies:
    transport: ProvisioningTransport
    token_reader: TokenReader
    candidate_selector: CandidateSelector
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    lease_policy: LeasePolicy = field(default_factory=LeasePolicy)


@dataclass(frozen=True, slots=True)
class _EnvDocument:
    path: Path
    text: str
    newline: str
    trailing_newline: bool
    assignments: dict[str, int]

    @classmethod
    def read(cls, path: Path) -> _EnvDocument:
        try:
            if path.exists() or path.is_symlink():
                target = path.lstat()
                if stat.S_ISLNK(target.st_mode) or not stat.S_ISREG(target.st_mode):
                    raise ProvisioningFailure("env_file_not_regular")
                raw = path.read_bytes()
            else:
                raw = b""
        except ProvisioningFailure:
            raise
        except OSError:
            raise ProvisioningFailure("env_file_unreadable") from None
        if b"\0" in raw:
            raise ProvisioningFailure("env_file_invalid")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ProvisioningFailure("env_file_invalid") from None
        has_crlf = "\r\n" in text
        without_crlf = text.replace("\r\n", "")
        if "\r" in without_crlf or (has_crlf and "\n" in without_crlf):
            raise ProvisioningFailure("env_file_ambiguous_newlines")
        newline = "\r\n" if has_crlf else "\n"
        assignments: dict[str, int] = {}
        for binding in parse_stream(io.StringIO(text)):
            if binding.error:
                raise ProvisioningFailure("env_file_ambiguous_assignment")
            if binding.key is None:
                continue
            key = binding.key.casefold()
            value = binding.value or ""
            _reject_plaintext_token_assignment(key, value)
            if _is_managed_key(key):
                if key in assignments:
                    raise ProvisioningFailure("env_file_duplicate_managed_key")
                original = binding.original.string
                body = original.removesuffix("\r\n").removesuffix("\n")
                if "\n" in body or "\r" in body:
                    raise ProvisioningFailure("env_file_ambiguous_managed_assignment")
                assignments[key] = binding.original.line - 1
        return cls(
            path=path,
            text=text,
            newline=newline,
            trailing_newline=text.endswith(("\n", "\r")),
            assignments=assignments,
        )

    def rendered(self, environment: TelegramEnvironment, values: dict[str, str]) -> bytes:
        prefix = f"TELEGRAM_{environment.value.upper()}__"
        canonical = {f"{prefix}{suffix}": values[suffix] for suffix in values}
        lines = self.text.split(self.newline)
        if self.trailing_newline:
            lines.pop()
        replaced: set[str] = set()
        for index, _line in enumerate(lines):
            for full_key, value in canonical.items():
                if self.assignments.get(full_key.casefold()) == index:
                    lines[index] = f"{full_key}={value}"
                    replaced.add(full_key)
                    break
        for full_key, value in canonical.items():
            if full_key not in replaced:
                lines.append(f"{full_key}={value}")
        rendered = self.newline.join(lines)
        if lines and (self.trailing_newline or not self.text):
            rendered += self.newline
        return rendered.encode("utf-8")


def _is_managed_key(key: str) -> bool:
    return any(
        key == f"telegram_{environment}__{suffix}".casefold()
        for environment in ("staging", "production")
        for suffix in _MANAGED_SUFFIXES
    )


def _dotenv_json(value: str) -> object:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == "'":
        stripped = stripped[1:-1]
    elif len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        try:
            stripped = json.loads(stripped)
        except json.JSONDecodeError:
            return None
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return None


def _reject_plaintext_token_assignment(key: str, value: str) -> None:
    nested = {
        "telegram_staging__bot_token",
        "telegram_production__bot_token",
    }
    if key in nested:
        raise ProvisioningFailure("plaintext_token_assignment", remediation=_TOKEN_REMEDIATION)
    if key not in {"telegram_staging", "telegram_production"}:
        return
    decoded = _dotenv_json(value)
    if isinstance(decoded, dict) and any(str(item).casefold() == "bot_token" for item in decoded):
        raise ProvisioningFailure("plaintext_token_assignment", remediation=_TOKEN_REMEDIATION)
    # A top-level object can override the exact nested values this utility owns.
    raise ProvisioningFailure("env_file_ambiguous_managed_assignment")


def _secret_path(directory: Path, environment: TelegramEnvironment) -> Path:
    key = f"telegram_{environment.value}"
    return directory / TELEGRAM_BOT_TOKEN_FILENAMES[key]


def _validate_directory(directory: Path) -> None:
    try:
        details = directory.lstat()
    except OSError:
        raise ProvisioningFailure("secrets_directory_invalid") from None
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise ProvisioningFailure("secrets_directory_invalid")


def _read_token_file(path: Path) -> SecretStr:
    descriptor = -1
    try:
        descriptor = os.open(path, _secure_token_read_flags())
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ProvisioningFailure("token_file_not_regular")
        if stat.S_IMODE(details.st_mode) != 0o600:
            raise ProvisioningFailure("token_file_permissions_invalid")
        raw = os.read(descriptor, MAX_TELEGRAM_TOKEN_FILE_BYTES + 1)
    except ProvisioningFailure:
        raise
    except OSError:
        raise ProvisioningFailure("token_file_unreadable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not raw or len(raw) > MAX_TELEGRAM_TOKEN_FILE_BYTES:
        raise ProvisioningFailure("token_file_invalid")
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ProvisioningFailure("token_file_invalid") from None
    if TELEGRAM_BOT_TOKEN_PATTERN.fullmatch(value) is None:
        raise ProvisioningFailure("token_file_invalid")
    return SecretStr(value)


def _secure_token_read_flags() -> int:
    try:
        no_follow = os.O_NOFOLLOW
        non_block = os.O_NONBLOCK
    except AttributeError:
        raise ProvisioningFailure("secure_file_open_unavailable") from None
    return os.O_RDONLY | no_follow | non_block


def _atomic_replace(path: Path, content: bytes) -> None:
    try:
        if path.exists() or path.is_symlink():
            details = path.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
                raise ProvisioningFailure("target_not_regular")
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
    except ProvisioningFailure:
        raise
    except OSError:
        raise ProvisioningFailure("atomic_write_failed") from None


def _atomic_install(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ProvisioningFailure("token_file_exists")
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, path, follow_symlinks=False)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
    except FileExistsError:
        raise ProvisioningFailure("token_file_exists") from None
    except OSError:
        raise ProvisioningFailure("atomic_write_failed") from None


def _selected(settings: Settings, environment: TelegramEnvironment) -> TelegramBotSettings:
    return (
        settings.telegram_staging
        if environment is TelegramEnvironment.STAGING
        else settings.telegram_production
    )


def _other(settings: Settings, environment: TelegramEnvironment) -> TelegramBotSettings:
    return (
        settings.telegram_production
        if environment is TelegramEnvironment.STAGING
        else settings.telegram_staging
    )


def _load_inspected(request: ProvisioningRequest) -> tuple[_EnvDocument, Settings]:
    document = _EnvDocument.read(request.env_file)
    _validate_directory(request.secrets_dir)
    # Validate exact paths before Settings opens them, so a symlink or
    # over-permissive file is rejected without first following/reading it.
    for environment in TelegramEnvironment:
        path = _secret_path(request.secrets_dir, environment)
        if path.exists() or path.is_symlink():
            _read_token_file(path)
    try:
        settings = load_settings(
            environ={},
            env_file=request.env_file,
            secrets_dir=request.secrets_dir,
        )
    except Exception:
        raise ProvisioningFailure("configuration_invalid") from None
    return document, settings


def _database_factory(path: Path) -> sessionmaker[Session]:
    try:
        engine = create_db_engine(f"sqlite+pysqlite:///{path.resolve()}")
        return sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    except Exception:
        raise ProvisioningFailure("maintenance_database_failed") from None


async def execute(
    request: ProvisioningRequest, dependencies: RuntimeDependencies
) -> ProvisioningResult:
    """Execute one sanitized provisioning command using injected boundaries."""
    document, settings = _load_inspected(request)
    if request.command == "validate":
        return await _validate(request, settings, dependencies)
    if not request.confirm_poller_stopped:
        raise ProvisioningFailure("poller_stop_acknowledgement_required")
    if request.database is None:
        raise ProvisioningFailure("database_required")
    selected = _selected(settings, request.environment)
    if request.command in {"add", "rotate-token"} and selected.enabled:
        raise ProvisioningFailure("environment_must_be_disabled")
    pending_token: SecretStr | None = None
    if request.command == "add":
        pending_token = dependencies.token_reader.read("Telegram Bot token: ")
    elif request.command == "rotate-token":
        pending_token = dependencies.token_reader.read("New Telegram Bot token: ")
    factory = _database_factory(request.database)
    try:
        async with TelegramPollingMaintenanceLease(
            factory,
            request.environment,
            clock=dependencies.clock,
            sleep=dependencies.sleep,
            policy=dependencies.lease_policy,
            owner_prefix="provision",
        ) as lease:
            if request.command == "disable":
                values = {"ENABLED": "false"}
                await lease.verify_before_write()
                _atomic_replace(request.env_file, document.rendered(request.environment, values))
                return ProvisioningResult(request.command, request.environment)
            if request.command == "add":
                assert pending_token is not None
                return await _add(request, document, settings, lease, dependencies, pending_token)
            if request.command == "rotate-token":
                assert pending_token is not None
                return await _rotate(
                    request, document, settings, lease, dependencies, pending_token
                )
    except TelegramMaintenanceLeaseError as exc:
        raise ProvisioningFailure(exc.code) from None
    raise ProvisioningFailure("invalid_command")


async def _provider_identity(
    request: ProvisioningRequest,
    token: str,
    settings: Settings,
    dependencies: RuntimeDependencies,
) -> TelegramBotIdentity:
    identity = await dependencies.transport.get_me(token, timeout_seconds=_PROVIDER_TIMEOUT_SECONDS)
    if not _positive_int64(identity.id):
        raise ProvisioningFailure("bot_identity_invalid")
    webhook = await dependencies.transport.get_webhook_info(
        token, timeout_seconds=_PROVIDER_TIMEOUT_SECONDS
    )
    if webhook.url != "":
        raise ProvisioningFailure("webhook_configured")
    other = _other(settings, request.environment)
    if other.expected_bot_id is not None and other.expected_bot_id == identity.id:
        raise ProvisioningFailure("cross_environment_bot_identity")
    return identity


def _reject_cross_environment_token(request: ProvisioningRequest, token: str) -> None:
    other_path = _secret_path(request.secrets_dir, _other_environment(request.environment))
    if not (other_path.exists() or other_path.is_symlink()):
        return
    other_token = _read_token_file(other_path)
    if secrets.compare_digest(other_token.get_secret_value(), token):
        raise ProvisioningFailure("cross_environment_token")


async def _add(
    request: ProvisioningRequest,
    document: _EnvDocument,
    settings: Settings,
    lease: TelegramPollingMaintenanceLease,
    dependencies: RuntimeDependencies,
    token_secret: SecretStr,
) -> ProvisioningResult:
    token = token_secret.get_secret_value()
    if TELEGRAM_BOT_TOKEN_PATTERN.fullmatch(token) is None:
        raise ProvisioningFailure("token_invalid")
    _reject_cross_environment_token(request, token)
    target = _secret_path(request.secrets_dir, request.environment)
    target_exists = target.exists() or target.is_symlink()
    if target_exists:
        current = _read_token_file(target)
        if not secrets.compare_digest(current.get_secret_value(), token):
            raise ProvisioningFailure("token_file_exists")
    identity = await _provider_identity(request, token, settings, dependencies)
    candidates = await dependencies.transport.discover_private_candidates(
        token,
        timeout_seconds=_DISCOVERY_TIMEOUT_SECONDS,
        limit=_DISCOVERY_LIMIT,
        allowed_updates=("message",),
    )
    if not candidates:
        raise ProvisioningFailure("recipient_candidate_missing")
    candidate = await dependencies.candidate_selector.select(candidates)
    if candidate not in candidates:
        raise ProvisioningFailure("recipient_confirmation_failed")
    chat = await dependencies.transport.get_chat(
        token,
        candidate.private_chat_id,
        timeout_seconds=_PROVIDER_TIMEOUT_SECONDS,
    )
    if chat.id != candidate.private_chat_id or chat.type != "private":
        raise ProvisioningFailure("recipient_not_private")

    if target_exists:
        selected = _selected(settings, request.environment)
        expected_pair = ((candidate.user_id, candidate.private_chat_id),)
        actual_pair = tuple(
            (item.user_id, item.private_chat_id) for item in selected.allowed_recipients
        )
        if selected.expected_bot_id != identity.id or actual_pair != expected_pair:
            raise ProvisioningFailure("resume_configuration_mismatch")

    disabled_values = _environment_values(identity.id, candidate, enabled=False)
    await lease.verify_before_write()
    _atomic_replace(request.env_file, document.rendered(request.environment, disabled_values))
    if not target_exists:
        await lease.verify_before_write()
        _atomic_install(target, token.encode("utf-8") + b"\n")
    refreshed = _EnvDocument.read(request.env_file)
    await lease.verify_before_write()
    _atomic_replace(
        request.env_file,
        refreshed.rendered(request.environment, {"ENABLED": "true"}),
    )
    return ProvisioningResult(request.command, request.environment)


async def _rotate(
    request: ProvisioningRequest,
    document: _EnvDocument,
    settings: Settings,
    lease: TelegramPollingMaintenanceLease,
    dependencies: RuntimeDependencies,
    new_token_secret: SecretStr,
) -> ProvisioningResult:
    selected = _selected(settings, request.environment)
    if selected.expected_bot_id is None or len(selected.allowed_recipients) != 1:
        raise ProvisioningFailure("rotation_configuration_incomplete")
    target = _secret_path(request.secrets_dir, request.environment)
    old_token = _read_token_file(target).get_secret_value()
    new_token = new_token_secret.get_secret_value()
    if TELEGRAM_BOT_TOKEN_PATTERN.fullmatch(new_token) is None:
        raise ProvisioningFailure("token_invalid")
    resume_existing_secret = secrets.compare_digest(old_token, new_token)
    _reject_cross_environment_token(request, new_token)
    identity = await _provider_identity(request, new_token, settings, dependencies)
    if identity.id != selected.expected_bot_id:
        raise ProvisioningFailure("rotation_bot_identity_mismatch")
    recipient = selected.allowed_recipients[0]
    chat = await dependencies.transport.get_chat(
        new_token,
        recipient.private_chat_id,
        timeout_seconds=_PROVIDER_TIMEOUT_SECONDS,
    )
    if chat.id != recipient.private_chat_id or chat.type != "private":
        raise ProvisioningFailure("recipient_not_private")
    if not resume_existing_secret:
        await lease.verify_before_write()
        _atomic_replace(target, new_token.encode("utf-8") + b"\n")
    await lease.verify_before_write()
    _atomic_replace(
        request.env_file,
        document.rendered(request.environment, {"ENABLED": "true"}),
    )
    return ProvisioningResult(request.command, request.environment)


async def _validate(
    request: ProvisioningRequest,
    settings: Settings,
    dependencies: RuntimeDependencies,
) -> ProvisioningResult:
    selected = _selected(settings, request.environment)
    if (
        selected.bot_token is None
        or selected.expected_bot_id is None
        or len(selected.allowed_recipients) != 1
    ):
        raise ProvisioningFailure("configuration_incomplete")
    target = _secret_path(request.secrets_dir, request.environment)
    token = _read_token_file(target).get_secret_value()
    _reject_cross_environment_token(request, token)
    identity = await _provider_identity(request, token, settings, dependencies)
    if identity.id != selected.expected_bot_id:
        raise ProvisioningFailure("bot_identity_mismatch")
    recipient = selected.allowed_recipients[0]
    chat = await dependencies.transport.get_chat(
        token,
        recipient.private_chat_id,
        timeout_seconds=_PROVIDER_TIMEOUT_SECONDS,
    )
    if chat.id != recipient.private_chat_id or chat.type != "private":
        raise ProvisioningFailure("recipient_not_private")
    sent = False
    if request.send_test:
        await dependencies.transport.send_test_message(
            token,
            recipient.private_chat_id,
            f"ainvest Telegram {request.environment.value} validation test.",
            timeout_seconds=_PROVIDER_TIMEOUT_SECONDS,
        )
        sent = True
    return ProvisioningResult(request.command, request.environment, test_message_sent=sent)


def _other_environment(environment: TelegramEnvironment) -> TelegramEnvironment:
    return (
        TelegramEnvironment.PRODUCTION
        if environment is TelegramEnvironment.STAGING
        else TelegramEnvironment.STAGING
    )


def _environment_values(
    bot_id: int,
    candidate: ProvisioningCandidate,
    *,
    enabled: bool,
) -> dict[str, str]:
    recipients = json.dumps(
        [{"user_id": candidate.user_id, "private_chat_id": candidate.private_chat_id}],
        separators=(",", ":"),
    )
    return {
        "ENABLED": "true" if enabled else "false",
        "EXPECTED_BOT_ID": str(bot_id),
        "ALLOWED_RECIPIENTS": recipients,
        "TRANSPORT": "long_polling",
        "APPROVAL_METHOD": "telegram",
        "APPROVAL_SCOPE": "paper",
    }


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        raise ProvisioningFailure("invalid_cli_input")


def build_parser() -> argparse.ArgumentParser:
    parser = _SanitizedArgumentParser(prog="ainvest-telegram-provision")
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=_SanitizedArgumentParser
    )
    for command in ("add", "validate", "rotate-token", "disable"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument(
            "--environment",
            required=True,
            choices=tuple(item.value for item in TelegramEnvironment),
        )
        command_parser.add_argument("--env-file", type=Path, required=True)
        command_parser.add_argument("--secrets-dir", type=Path, required=True)
        if command == "validate":
            command_parser.add_argument("--send-test", action="store_true")
        else:
            command_parser.add_argument("--database", type=Path, required=True)
            command_parser.add_argument("--confirm-poller-stopped", action="store_true")
    return parser


def _request_from_namespace(namespace: argparse.Namespace) -> ProvisioningRequest:
    return ProvisioningRequest(
        command=namespace.command,
        environment=TelegramEnvironment(namespace.environment),
        env_file=namespace.env_file,
        secrets_dir=namespace.secrets_dir,
        database=getattr(namespace, "database", None),
        confirm_poller_stopped=getattr(namespace, "confirm_poller_stopped", False),
        send_test=getattr(namespace, "send_test", False),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        request = _request_from_namespace(parser.parse_args(argv))
        dependencies = RuntimeDependencies(
            transport=TelegramProvisioningHttpsTransport(),
            token_reader=TtyTokenReader(),
            candidate_selector=TtyCandidateSelector(),
        )
        result = asyncio.run(execute(request, dependencies))
    except ProvisioningFailure as exc:
        payload = {"status": "error", "code": exc.code}
        if exc.remediation is not None:
            payload["remediation"] = exc.remediation
        sys.stderr.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
        return 2 if exc.code == "invalid_cli_input" else 1
    except Exception:
        sys.stderr.write('{"status":"error","code":"internal_error"}\n')
        return 1
    sys.stdout.write(result.as_json() + "\n")
    return 0


__all__ = [
    "CandidateSelector",
    "LeasePolicy",
    "ProvisioningCandidate",
    "ProvisioningFailure",
    "ProvisioningRequest",
    "ProvisioningResult",
    "ProvisioningTransport",
    "ProvisioningWebhookInfo",
    "RuntimeDependencies",
    "TelegramProvisioningHttpsTransport",
    "TokenReader",
    "TtyCandidateSelector",
    "TtyTokenReader",
    "build_parser",
    "execute",
    "main",
]
