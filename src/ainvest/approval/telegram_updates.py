"""Bounded Telegram long-polling ingress with durable deduplication.

This boundary classifies authorized private-chat inputs and hands them to a
typed handler. It deliberately does not interpret approval callbacks or query
commands; those capabilities belong to P05-T1 and P05-T9.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from importlib import import_module
from typing import Any, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictInt,
    field_validator,
    model_validator,
)
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.telegram import (
    TELEGRAM_VALIDATION_ATTEMPTS,
    TelegramEnvironment,
    TelegramTransportRejected,
    TelegramValidationTimeout,
)
from ainvest.config import Settings, TelegramBotSettings
from ainvest.db import TelegramPollState, UnitOfWork

TELEGRAM_POLL_TIMEOUT_SECONDS = 25
TELEGRAM_POLL_DEADLINE_SECONDS = 35.0
TELEGRAM_POLL_LIMIT = 100
TELEGRAM_ALLOWED_UPDATES = ("message", "callback_query")
TELEGRAM_LEASE_SECONDS = 75
TELEGRAM_HANDLER_DEADLINE_SECONDS = 20.0
TELEGRAM_COMMIT_MARGIN_SECONDS = 10.0
TELEGRAM_MAX_UPDATE_ID = 2**63 - 2
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CALLBACK_DATA_BYTES = 64
TELEGRAM_CALLBACK_ID_BYTES = 128
_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
_CALLBACK_DIGEST_DOMAIN = b"ainvest:telegram-callback-query:v1\x00"


class TelegramProviderUpdateKind(StrEnum):
    MESSAGE = "message"
    CALLBACK = "callback"
    UNSUPPORTED = "unsupported"
    MALFORMED = "malformed"


class TelegramIgnoredReason(StrEnum):
    UNKNOWN_RECIPIENT = "unknown_recipient"
    NOT_PRIVATE_CHAT = "not_private_chat"
    MISSING_SENDER = "missing_sender"
    INLINE_CALLBACK = "inline_callback"
    FORWARDED_MESSAGE = "forwarded_message"
    SERVICE_MESSAGE = "service_message"
    UNSUPPORTED_UPDATE = "unsupported_update"
    MALFORMED_UPDATE = "malformed_update"


class TelegramHandlerDisposition(StrEnum):
    TERMINAL_HANDLED = "terminal_handled"
    RETRY_LATER = "retry_later"


class TelegramProviderUpdate(BaseModel):
    """Bounded, redacted provider record emitted by a transport adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    update_id: StrictInt
    kind: TelegramProviderUpdateKind
    sender_user_id: StrictInt | None = Field(default=None, repr=False)
    chat_id: StrictInt | None = Field(default=None, repr=False)
    message_id: StrictInt | None = Field(default=None, repr=False)
    chat_type: str | None = Field(default=None, max_length=16)
    text: SecretStr | None = Field(default=None, repr=False)
    callback_query_id: SecretStr | None = Field(default=None, repr=False)
    callback_data: SecretStr | None = Field(default=None, repr=False)
    forwarded: bool = False
    service: bool = False

    @field_validator("update_id")
    @classmethod
    def _bounded_update_id(cls, value: int) -> int:
        return _bounded_update_id(value)

    @field_validator("sender_user_id", "chat_id", "message_id")
    @classmethod
    def _bounded_identity(cls, value: int | None) -> int | None:
        if value is not None and (value < -(2**63) or value > 2**63 - 1):
            raise ValueError("Telegram identity is outside the signed 64-bit range")
        return value

    @model_validator(mode="after")
    def _validate_secret_bounds(self) -> TelegramProviderUpdate:
        if self.kind is TelegramProviderUpdateKind.CALLBACK:
            if self.callback_query_id is None or self.callback_data is None:
                raise ValueError("callback updates require bounded callback values")
            _validate_callback_values(self.callback_query_id, self.callback_data)
        if (
            self.text is not None
            and not 1 <= len(self.text.get_secret_value()) <= TELEGRAM_TEXT_LIMIT
        ):
            raise ValueError("Telegram text must contain 1..4096 code points")
        return self


class AuthorizedCallbackUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: TelegramEnvironment
    update_id: StrictInt
    sender_user_id: StrictInt = Field(repr=False)
    chat_id: StrictInt = Field(repr=False)
    message_id: StrictInt = Field(repr=False)
    callback_query_id: SecretStr = Field(repr=False)
    callback_data: SecretStr = Field(repr=False)

    @field_validator("update_id")
    @classmethod
    def _validate_update_id(cls, value: int) -> int:
        return _bounded_update_id(value)

    @field_validator("sender_user_id", "chat_id", "message_id")
    @classmethod
    def _validate_identity(cls, value: int) -> int:
        return _positive_telegram_id(value)

    @model_validator(mode="after")
    def _validate_callback(self) -> AuthorizedCallbackUpdate:
        _validate_callback_values(self.callback_query_id, self.callback_data)
        return self


class AuthorizedTextUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: TelegramEnvironment
    update_id: StrictInt
    sender_user_id: StrictInt = Field(repr=False)
    chat_id: StrictInt = Field(repr=False)
    message_id: StrictInt = Field(repr=False)
    text: SecretStr = Field(repr=False)

    @field_validator("update_id")
    @classmethod
    def _validate_update_id(cls, value: int) -> int:
        return _bounded_update_id(value)

    @field_validator("sender_user_id", "chat_id", "message_id")
    @classmethod
    def _validate_identity(cls, value: int) -> int:
        return _positive_telegram_id(value)

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: SecretStr) -> SecretStr:
        text = value.get_secret_value()
        if not 1 <= len(text) <= TELEGRAM_TEXT_LIMIT:
            raise ValueError("Telegram text must contain 1..4096 code points")
        return value


class IgnoredTelegramUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: TelegramEnvironment
    update_id: StrictInt
    reason: TelegramIgnoredReason


AuthorizedTelegramUpdate = AuthorizedCallbackUpdate | AuthorizedTextUpdate


class TelegramAuthorizedUpdateHandler(Protocol):
    async def handle(self, update: AuthorizedTelegramUpdate) -> TelegramHandlerDisposition: ...


class TelegramPollingControl(Protocol):
    def is_set(self) -> bool: ...

    async def wait(self, timeout_seconds: float) -> bool:
        """Return True when shutdown was requested, False after the timeout."""


class AsyncioTelegramPollingControl:
    """Production shutdown adapter around :class:`asyncio.Event`."""

    def __init__(self, event: asyncio.Event) -> None:
        self._event = event

    def is_set(self) -> bool:
        return self._event.is_set()

    async def wait(self, timeout_seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._event.wait(), timeout_seconds)
        except TimeoutError:
            return False
        return True


class TelegramUpdateTransport(Protocol):
    async def get_updates(
        self,
        token: str,
        *,
        offset: int,
        timeout: int,
        limit: int,
        allowed_updates: tuple[str, ...],
        deadline_seconds: float,
    ) -> Sequence[TelegramProviderUpdate]: ...


class TelegramIdentityTransport(Protocol):
    async def get_me(self, token: str, *, timeout_seconds: float) -> Any: ...


class TelegramProviderTransient(Exception):
    """Sanitized retryable provider/network failure."""


class TelegramProviderRateLimited(Exception):
    """Sanitized provider throttle with a bounded delay hint."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("Telegram provider rate limited")
        self.retry_after_seconds = retry_after_seconds


class TelegramPollingFatal(Exception):
    """Sanitized non-retryable identity/request/provider-contract failure."""


class TelegramHttpsUpdateTransport:
    """Lazy python-telegram-bot adapter that never exposes raw updates downstream."""

    async def get_updates(
        self,
        token: str,
        *,
        offset: int,
        timeout: int,
        limit: int,
        allowed_updates: tuple[str, ...],
        deadline_seconds: float,
    ) -> Sequence[TelegramProviderUpdate]:
        try:
            telegram = import_module("telegram")
            error = import_module("telegram.error")
        except ImportError:
            raise TelegramPollingFatal(
                "telegram polling transport dependency is unavailable"
            ) from None
        try:
            bot = telegram.Bot(token=token)
            async with asyncio.timeout(deadline_seconds):
                updates = await bot.get_updates(
                    offset=offset,
                    timeout=timeout,
                    limit=limit,
                    allowed_updates=allowed_updates,
                    read_timeout=deadline_seconds,
                    write_timeout=deadline_seconds,
                    connect_timeout=deadline_seconds,
                    pool_timeout=deadline_seconds,
                )
            if len(updates) > TELEGRAM_POLL_LIMIT:
                raise TelegramPollingFatal("telegram provider returned an oversized update batch")
            return tuple(_normalize_provider_update(update) for update in updates)
        except error.RetryAfter as exc:
            retry_after = getattr(exc, "retry_after", 1)
            seconds = getattr(retry_after, "total_seconds", lambda: retry_after)()
            raise TelegramProviderRateLimited(float(seconds)) from None
        except (error.TimedOut, error.NetworkError, TimeoutError):
            raise TelegramProviderTransient from None
        except (error.InvalidToken, error.Forbidden, error.BadRequest, error.Conflict):
            raise TelegramPollingFatal("telegram provider rejected the polling request") from None
        except TelegramPollingFatal:
            raise
        except Exception:
            raise TelegramPollingFatal("telegram provider returned an invalid response") from None


def _strict_int(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if value < -(2**63) or value > 2**63 - 1:
        return None
    return value


def _bounded_update_id(value: int) -> int:
    if value < 0 or value > TELEGRAM_MAX_UPDATE_ID:
        raise ValueError("Telegram update_id is outside the signed 64-bit range")
    return value


def _positive_telegram_id(value: int) -> int:
    if value <= 0 or value > 2**63 - 1:
        raise ValueError("Telegram identity must be a positive signed 64-bit integer")
    return value


def _validate_callback_values(callback_query_id: SecretStr, callback_data: SecretStr) -> None:
    callback_id = callback_query_id.get_secret_value()
    if not (1 <= len(callback_id) <= TELEGRAM_CALLBACK_ID_BYTES) or any(
        not 0x21 <= ord(character) <= 0x7E for character in callback_id
    ):
        raise ValueError("callback query id must be 1..128 visible ASCII characters")
    try:
        callback_data_size = len(callback_data.get_secret_value().encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("callback data must be valid UTF-8") from None
    if not 1 <= callback_data_size <= TELEGRAM_CALLBACK_DATA_BYTES:
        raise ValueError("callback data must be 1..64 UTF-8 bytes")


def _secret_text(
    value: object,
    *,
    max_chars: int,
    max_bytes: int | None = None,
    min_chars: int = 0,
    min_bytes: int = 0,
) -> SecretStr | None:
    if not isinstance(value, str) or not min_chars <= len(value) <= max_chars:
        return None
    if max_bytes is not None:
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            return None
        if not min_bytes <= size <= max_bytes:
            return None
    return SecretStr(value)


def _normalize_provider_update(update: object) -> TelegramProviderUpdate:
    update_id = _strict_int(getattr(update, "update_id", None))
    if update_id is None or update_id < 0 or update_id > TELEGRAM_MAX_UPDATE_ID:
        raise TelegramPollingFatal("telegram provider returned an invalid update identifier")

    callback = getattr(update, "callback_query", None)
    if callback is not None:
        callback_id = getattr(callback, "id", None)
        callback_secret = _secret_text(
            callback_id,
            max_chars=128,
            max_bytes=128,
            min_chars=1,
            min_bytes=1,
        )
        if (
            callback_secret is None
            or not isinstance(callback_id, str)
            or any(not 0x21 <= ord(char) <= 0x7E for char in callback_id)
        ):
            return TelegramProviderUpdate(
                update_id=update_id, kind=TelegramProviderUpdateKind.MALFORMED
            )
        data = _secret_text(
            getattr(callback, "data", None),
            max_chars=64,
            max_bytes=TELEGRAM_CALLBACK_DATA_BYTES,
            min_chars=1,
            min_bytes=1,
        )
        sender = getattr(callback, "from_user", None)
        message = getattr(callback, "message", None)
        chat = getattr(message, "chat", None)
        chat_type = getattr(chat, "type", None)
        message_id = _strict_int(getattr(message, "message_id", None))
        if data is None or not isinstance(chat_type, str) or len(chat_type) > 16:
            return TelegramProviderUpdate(
                update_id=update_id, kind=TelegramProviderUpdateKind.MALFORMED
            )
        return TelegramProviderUpdate(
            update_id=update_id,
            kind=TelegramProviderUpdateKind.CALLBACK,
            sender_user_id=_strict_int(getattr(sender, "id", None)),
            chat_id=_strict_int(getattr(chat, "id", None)),
            message_id=message_id,
            chat_type=chat_type,
            callback_query_id=callback_secret,
            callback_data=data,
            forwarded=_is_forwarded_message(message),
        )

    message = getattr(update, "message", None)
    if message is None:
        return TelegramProviderUpdate(
            update_id=update_id, kind=TelegramProviderUpdateKind.UNSUPPORTED
        )
    raw_text = getattr(message, "text", None)
    if raw_text is not None and (
        not isinstance(raw_text, str) or not 1 <= len(raw_text) <= TELEGRAM_TEXT_LIMIT
    ):
        return TelegramProviderUpdate(
            update_id=update_id, kind=TelegramProviderUpdateKind.MALFORMED
        )
    text = _secret_text(raw_text, max_chars=TELEGRAM_TEXT_LIMIT)
    sender = getattr(message, "from_user", None)
    chat = getattr(message, "chat", None)
    chat_type = getattr(chat, "type", None)
    if not isinstance(chat_type, str) or len(chat_type) > 16:
        return TelegramProviderUpdate(
            update_id=update_id, kind=TelegramProviderUpdateKind.MALFORMED
        )
    service = text is None
    return TelegramProviderUpdate(
        update_id=update_id,
        kind=TelegramProviderUpdateKind.MESSAGE,
        sender_user_id=_strict_int(getattr(sender, "id", None)),
        chat_id=_strict_int(getattr(chat, "id", None)),
        message_id=_strict_int(getattr(message, "message_id", None)),
        chat_type=chat_type,
        text=text,
        forwarded=_is_forwarded_message(message),
        service=service,
    )


def _is_forwarded_message(message: object | None) -> bool:
    return message is not None and (
        getattr(message, "forward_origin", None) is not None
        or getattr(message, "forward_date", None) is not None
        or getattr(message, "is_automatic_forward", False) is True
    )


def classify_update(
    update: TelegramProviderUpdate,
    *,
    environment: TelegramEnvironment,
    allowed_pairs: frozenset[tuple[int, int]],
) -> AuthorizedTelegramUpdate | IgnoredTelegramUpdate:
    """Pure authorization/classification; it performs no business action."""

    def ignored(reason: TelegramIgnoredReason) -> IgnoredTelegramUpdate:
        return IgnoredTelegramUpdate(
            environment=environment, update_id=update.update_id, reason=reason
        )

    if update.kind is TelegramProviderUpdateKind.MALFORMED:
        return ignored(TelegramIgnoredReason.MALFORMED_UPDATE)
    if update.kind is TelegramProviderUpdateKind.UNSUPPORTED:
        return ignored(TelegramIgnoredReason.UNSUPPORTED_UPDATE)
    if update.sender_user_id is None:
        return ignored(TelegramIgnoredReason.MISSING_SENDER)
    if update.kind is TelegramProviderUpdateKind.CALLBACK and (
        update.message_id is None or update.chat_id is None
    ):
        return ignored(TelegramIgnoredReason.INLINE_CALLBACK)
    if update.chat_type != "private":
        return ignored(TelegramIgnoredReason.NOT_PRIVATE_CHAT)
    if update.chat_id is None or (update.sender_user_id, update.chat_id) not in allowed_pairs:
        return ignored(TelegramIgnoredReason.UNKNOWN_RECIPIENT)
    if update.message_id is None or update.message_id <= 0:
        return ignored(TelegramIgnoredReason.MALFORMED_UPDATE)
    if update.forwarded:
        return ignored(TelegramIgnoredReason.FORWARDED_MESSAGE)
    if update.kind is TelegramProviderUpdateKind.CALLBACK:
        if update.callback_query_id is None or update.callback_data is None:
            return ignored(TelegramIgnoredReason.MALFORMED_UPDATE)
        return AuthorizedCallbackUpdate(
            environment=environment,
            update_id=update.update_id,
            sender_user_id=update.sender_user_id,
            chat_id=update.chat_id,
            message_id=update.message_id,
            callback_query_id=update.callback_query_id,
            callback_data=update.callback_data,
        )
    if update.service or update.text is None:
        return ignored(TelegramIgnoredReason.SERVICE_MESSAGE)
    return AuthorizedTextUpdate(
        environment=environment,
        update_id=update.update_id,
        sender_user_id=update.sender_user_id,
        chat_id=update.chat_id,
        message_id=update.message_id,
        text=update.text,
    )


def _callback_digest(callback_query_id: SecretStr) -> str:
    value = callback_query_id.get_secret_value().encode("ascii")
    return hashlib.sha256(_CALLBACK_DIGEST_DOMAIN + value).hexdigest()


def _bot_settings(settings: Settings, environment: TelegramEnvironment) -> TelegramBotSettings:
    return (
        settings.telegram_staging
        if environment is TelegramEnvironment.STAGING
        else settings.telegram_production
    )


class TelegramLongPoller:
    """Single-environment long poller with lease fencing and durable offset."""

    def __init__(
        self,
        *,
        settings: Settings,
        environment: TelegramEnvironment,
        session_factory: sessionmaker[Session],
        identity_transport: TelegramIdentityTransport,
        update_transport: TelegramUpdateTransport,
        handler: TelegramAuthorizedUpdateHandler,
        clock: Callable[[], datetime] | None = None,
        random_value: Callable[[], float] | None = None,
        owner: str | None = None,
    ) -> None:
        if type(environment) is not TelegramEnvironment:
            raise TypeError("environment must be TelegramEnvironment")
        if owner is not None and (
            not owner or len(owner) > 64 or not owner.isascii() or not owner.isprintable()
        ):
            raise ValueError("telegram polling owner must be bounded visible ASCII")
        self._config = _bot_settings(settings, environment)
        self._environment = environment
        self._session_factory = session_factory
        self._identity_transport = identity_transport
        self._update_transport = update_transport
        self._handler = handler
        self._clock = clock or (lambda: datetime.now(UTC))
        self._random_value = random_value or (lambda: secrets.randbelow(1_000_001) / 1_000_000)
        self._owner = owner or secrets.token_hex(16)
        self._provider_failures = 0
        self._processing_failures = 0

    async def run(self, control: TelegramPollingControl) -> None:
        token = await self._validated_token()
        while not control.is_set():
            lease = self._acquire()
            if lease is None:
                if await control.wait(1.0):
                    return
                continue
            retry_delay: float | None = None
            try:
                renewed = self._renew(lease)
                if renewed is None:
                    continue
                lease = renewed
                try:
                    updates = await self._update_transport.get_updates(
                        token,
                        offset=lease.next_offset,
                        timeout=TELEGRAM_POLL_TIMEOUT_SECONDS,
                        limit=TELEGRAM_POLL_LIMIT,
                        allowed_updates=TELEGRAM_ALLOWED_UPDATES,
                        deadline_seconds=TELEGRAM_POLL_DEADLINE_SECONDS,
                    )
                    normalized = _ordered_unique_batch(updates)
                except TelegramProviderRateLimited as exc:
                    retry_delay = self._rate_limit_delay(exc.retry_after_seconds)
                except TelegramProviderTransient:
                    retry_delay = self._next_backoff(provider=True)
                except TelegramPollingFatal:
                    raise
                except Exception:
                    raise TelegramPollingFatal(
                        "telegram provider transport violated its contract"
                    ) from None
                else:
                    self._provider_failures = 0
                    renewed = self._renew(lease)
                    if renewed is None:
                        continue
                    lease = renewed
                    retry_delay = await self._process_batch(normalized, lease, control)
            finally:
                self._release(lease)
            if retry_delay is not None and await control.wait(retry_delay):
                return

    async def _validated_token(self) -> str:
        if (
            not self._config.enabled
            or self._config.bot_token is None
            or self._config.expected_bot_id is None
            or not self._config.allowed_recipients
        ):
            raise TelegramPollingFatal("telegram polling configuration is disabled or incomplete")
        token = self._config.bot_token.get_secret_value()
        for attempt in range(TELEGRAM_VALIDATION_ATTEMPTS):
            try:
                identity = await self._identity_transport.get_me(token, timeout_seconds=5.0)
            except TelegramValidationTimeout:
                if attempt + 1 == TELEGRAM_VALIDATION_ATTEMPTS:
                    raise TelegramPollingFatal(
                        "telegram bot identity validation timed out"
                    ) from None
                continue
            except TelegramTransportRejected:
                raise TelegramPollingFatal(
                    "telegram bot identity validation was rejected"
                ) from None
            except Exception:
                raise TelegramPollingFatal("telegram bot identity validation failed") from None
            if _strict_int(getattr(identity, "id", None)) != self._config.expected_bot_id:
                raise TelegramPollingFatal("telegram bot identity does not match configuration")
            return token
        raise TelegramPollingFatal("telegram bot identity validation failed")

    async def _process_batch(
        self,
        updates: Sequence[TelegramProviderUpdate],
        lease: TelegramPollState,
        control: TelegramPollingControl,
    ) -> float | None:
        allowed_pairs = frozenset(
            (recipient.user_id, recipient.private_chat_id)
            for recipient in self._config.allowed_recipients
        )
        for provider_update in updates:
            if control.is_set():
                return None
            state = self._state()
            if provider_update.update_id < state.next_offset or self._is_processed(
                provider_update.update_id
            ):
                continue
            classified = classify_update(
                provider_update, environment=self._environment, allowed_pairs=allowed_pairs
            )
            digest: str | None = None
            disposition = "ignored"
            kind = "ignored"
            if isinstance(classified, AuthorizedCallbackUpdate):
                kind = "callback"
                digest = _callback_digest(classified.callback_query_id)
                if self._callback_seen(digest):
                    digest = None
                    disposition = "duplicate_callback"
                else:
                    renewed = self._renew(lease)
                    if renewed is None:
                        return None
                    lease = renewed
                    result = await self._call_handler(classified, control)
                    if result is not TelegramHandlerDisposition.TERMINAL_HANDLED:
                        return self._next_backoff(provider=False)
                    disposition = "handled"
            elif isinstance(classified, AuthorizedTextUpdate):
                kind = "text"
                renewed = self._renew(lease)
                if renewed is None:
                    return None
                lease = renewed
                result = await self._call_handler(classified, control)
                if result is not TelegramHandlerDisposition.TERMINAL_HANDLED:
                    return self._next_backoff(provider=False)
                disposition = "handled"

            renewed = self._renew(lease)
            if renewed is None:
                return None
            lease = renewed
            terminal_state = self._record_terminal(
                lease,
                update_id=provider_update.update_id,
                kind=kind,
                disposition=disposition,
                callback_digest=digest,
            )
            if terminal_state is None:
                return None
            lease = terminal_state
            self._processing_failures = 0
        return None

    async def _call_handler(
        self, update: AuthorizedTelegramUpdate, control: TelegramPollingControl
    ) -> TelegramHandlerDisposition:
        try:
            result = await asyncio.wait_for(
                self._handler.handle(update), TELEGRAM_HANDLER_DEADLINE_SECONDS
            )
        except asyncio.CancelledError:
            if control.is_set():
                raise
            return TelegramHandlerDisposition.RETRY_LATER
        except Exception:
            return TelegramHandlerDisposition.RETRY_LATER
        return (
            result
            if isinstance(result, TelegramHandlerDisposition)
            else TelegramHandlerDisposition.RETRY_LATER
        )

    def _acquire(self) -> TelegramPollState | None:
        now = self._now()
        try:
            with UnitOfWork(self._session_factory) as uow:
                return uow.telegram_updates_repo.acquire_lease(
                    self._environment.value,
                    owner=self._owner,
                    now=now,
                    expires_at=now + timedelta(seconds=TELEGRAM_LEASE_SECONDS),
                )
        except TelegramPollingFatal:
            raise
        except Exception:
            raise TelegramPollingFatal("telegram polling persistence failed") from None

    def _renew(self, lease: TelegramPollState) -> TelegramPollState | None:
        now = self._now()
        try:
            with UnitOfWork(self._session_factory) as uow:
                renewed = uow.telegram_updates_repo.renew_lease(
                    self._environment.value,
                    owner=self._owner,
                    epoch=lease.lease_epoch,
                    version=lease.version,
                    now=now,
                    expires_at=now + timedelta(seconds=TELEGRAM_LEASE_SECONDS),
                )
        except TelegramPollingFatal:
            raise
        except Exception:
            raise TelegramPollingFatal("telegram polling persistence failed") from None
        if renewed is not None:
            remaining = (renewed.lease_expires_at - now).total_seconds()  # type: ignore[operator]
            if remaining < TELEGRAM_HANDLER_DEADLINE_SECONDS + TELEGRAM_COMMIT_MARGIN_SECONDS:
                raise TelegramPollingFatal("telegram lease horizon is too short")
        return renewed

    def _release(self, lease: TelegramPollState) -> None:
        try:
            with UnitOfWork(self._session_factory) as uow:
                current = uow.telegram_updates_repo.get_state(self._environment.value)
                if (
                    current is None
                    or current.lease_owner != self._owner
                    or current.lease_epoch != lease.lease_epoch
                ):
                    return
                uow.telegram_updates_repo.release_lease(
                    self._environment.value,
                    owner=self._owner,
                    epoch=lease.lease_epoch,
                    version=current.version,
                )
        except Exception:
            # Best effort only: expiry remains the cross-process recovery path.
            return

    def _state(self) -> TelegramPollState:
        try:
            with UnitOfWork(self._session_factory) as uow:
                state = uow.telegram_updates_repo.get_state(self._environment.value)
        except Exception:
            raise TelegramPollingFatal("telegram polling persistence failed") from None
        if state is None:
            raise TelegramPollingFatal("telegram poll state is missing")
        return state

    def _is_processed(self, update_id: int) -> bool:
        try:
            with UnitOfWork(self._session_factory) as uow:
                return uow.telegram_updates_repo.is_processed(self._environment.value, update_id)
        except Exception:
            raise TelegramPollingFatal("telegram polling persistence failed") from None

    def _callback_seen(self, digest: str) -> bool:
        try:
            with UnitOfWork(self._session_factory) as uow:
                return uow.telegram_updates_repo.callback_digest_seen(
                    self._environment.value, digest
                )
        except Exception:
            raise TelegramPollingFatal("telegram polling persistence failed") from None

    def _record_terminal(
        self,
        lease: TelegramPollState,
        *,
        update_id: int,
        kind: str,
        disposition: str,
        callback_digest: str | None,
    ) -> TelegramPollState | None:
        try:
            with UnitOfWork(self._session_factory) as uow:
                return uow.telegram_updates_repo.record_terminal(
                    self._environment.value,
                    owner=self._owner,
                    epoch=lease.lease_epoch,
                    version=lease.version,
                    now=self._now(),
                    update_id=update_id,
                    kind=kind,
                    disposition=disposition,
                    callback_query_digest=callback_digest,
                )
        except TelegramPollingFatal:
            raise
        except Exception:
            raise TelegramPollingFatal("telegram polling persistence failed") from None

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise TelegramPollingFatal("telegram polling clock must be timezone-aware")
        return value.astimezone(UTC)

    def _jitter(self, base: float) -> float:
        value = self._random_value()
        if not math.isfinite(value) or value < 0 or value > 1:
            raise TelegramPollingFatal("telegram polling random source is invalid")
        return value * min(1.0, base / 4.0)

    def _next_backoff(self, *, provider: bool) -> float:
        count = self._provider_failures if provider else self._processing_failures
        base = _BACKOFF_SECONDS[min(count, len(_BACKOFF_SECONDS) - 1)]
        if provider:
            self._provider_failures += 1
        else:
            self._processing_failures += 1
        return min(30.0, base + self._jitter(base))

    def _rate_limit_delay(self, retry_after_seconds: float) -> float:
        if (
            isinstance(retry_after_seconds, bool)
            or not isinstance(retry_after_seconds, (int, float))
            or not math.isfinite(retry_after_seconds)
        ):
            raise TelegramPollingFatal("telegram provider returned an invalid retry delay")
        base = min(60.0, max(1.0, float(retry_after_seconds)))
        return min(60.0, base + self._jitter(base))


def _ordered_unique_batch(
    updates: Sequence[TelegramProviderUpdate],
) -> tuple[TelegramProviderUpdate, ...]:
    if len(updates) > TELEGRAM_POLL_LIMIT:
        raise TelegramPollingFatal("telegram provider returned an oversized update batch")
    by_id: dict[int, TelegramProviderUpdate] = {}
    for update in updates:
        if not isinstance(update, TelegramProviderUpdate):
            raise TelegramPollingFatal("telegram update transport returned an invalid record")
        existing = by_id.get(update.update_id)
        if existing is not None and existing != update:
            raise TelegramPollingFatal("telegram provider returned conflicting duplicate updates")
        by_id[update.update_id] = update
    return tuple(by_id[update_id] for update_id in sorted(by_id))


__all__ = [
    "AsyncioTelegramPollingControl",
    "AuthorizedCallbackUpdate",
    "AuthorizedTelegramUpdate",
    "AuthorizedTextUpdate",
    "IgnoredTelegramUpdate",
    "TelegramAuthorizedUpdateHandler",
    "TelegramHandlerDisposition",
    "TelegramHttpsUpdateTransport",
    "TelegramIdentityTransport",
    "TelegramIgnoredReason",
    "TelegramLongPoller",
    "TelegramPollingControl",
    "TelegramPollingFatal",
    "TelegramProviderRateLimited",
    "TelegramProviderTransient",
    "TelegramProviderUpdate",
    "TelegramProviderUpdateKind",
    "TelegramUpdateTransport",
    "classify_update",
]
