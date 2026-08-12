"""Private Telegram notification sender with no approval or broker authority.

P05-T4 owns outbound display only.  It verifies the selected Bot and exact
bound private recipient before making one send attempt.  Inbound updates,
approval decisions, persistence, and broker access intentionally do not exist
in this module.
"""

from __future__ import annotations

import asyncio
import importlib
import re
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Annotated, Any, Final, Protocol
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictInt,
    StringConstraints,
    model_validator,
)

from ainvest.approval.order_hash import verify_order_hash
from ainvest.approval.tokens import OpaqueApprovalToken
from ainvest.config import Settings, TelegramBotSettings
from ainvest.schemas.common import UtcDateTime, format_canonical_decimal
from ainvest.schemas.orders import OrderProposal
from ainvest.schemas.portfolio import AccountScope
from ainvest.schemas.risk import RiskDecision, RiskOutcome

TELEGRAM_MESSAGE_LIMIT: Final[int] = 3_500
TELEGRAM_VALIDATION_ATTEMPTS: Final[int] = 2
_CONTROL_CHARACTER: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")


class TelegramEnvironment(StrEnum):
    """Explicit Bot environment; there is no fallback between values."""

    STAGING = "staging"
    PRODUCTION = "production"


class TelegramNotificationCategory(StrEnum):
    """Display/action category, never an authorization outcome."""

    PAPER = "PAPER"
    LIVE = "LIVE"


class TelegramDeliveryCode(StrEnum):
    """Sanitized, stable outbound delivery outcomes."""

    SENT = "sent"
    CONFIG_INVALID = "config_invalid"
    BOT_IDENTITY_MISMATCH = "bot_identity_mismatch"
    RECIPIENT_NOT_ALLOWED = "recipient_not_allowed"
    CHAT_NOT_PRIVATE = "chat_not_private"
    MESSAGE_INVALID = "message_invalid"
    VALIDATION_TIMEOUT = "validation_timeout"
    DELIVERY_UNKNOWN = "delivery_unknown"
    DELIVERY_FAILED = "delivery_failed"


class TelegramNotificationRequest(BaseModel):
    """Frozen server-owned outbound request accepted from trusted code only."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    environment: TelegramEnvironment
    category: TelegramNotificationCategory
    intent_correlation_id: Annotated[
        str,
        StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,128}$", min_length=1, max_length=128),
    ]
    recipient_user_id: Annotated[StrictInt, Field(gt=0, le=2**63 - 1)]
    recipient_private_chat_id: Annotated[StrictInt, Field(gt=0, le=2**63 - 1)]
    proposal: OrderProposal
    risk_decision: RiskDecision
    expires_at: UtcDateTime
    paper_nonce: OpaqueApprovalToken | None = Field(default=None, repr=False)
    live_approval_link: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def _require_exact_action(self) -> TelegramNotificationRequest:
        if self.category is TelegramNotificationCategory.PAPER:
            if self.paper_nonce is None or self.live_approval_link is not None:
                raise ValueError("PAPER notification requires only paper_nonce")
        elif self.live_approval_link is None or self.paper_nonce is not None:
            raise ValueError("LIVE notification requires only live_approval_link")
        return self


class TelegramNotificationOutcome(BaseModel):
    """Delivery-only result with no recipient, action, message, or secret data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: TelegramDeliveryCode
    retryable: bool
    environment: TelegramEnvironment
    intent_correlation_id: str
    telegram_message_id: int | None = None

    @model_validator(mode="after")
    def _outcome_contract(self) -> TelegramNotificationOutcome:
        expected_retryable = self.code is TelegramDeliveryCode.VALIDATION_TIMEOUT
        if self.retryable is not expected_retryable:
            raise ValueError("retryable does not match Telegram delivery code")
        if self.code is TelegramDeliveryCode.SENT:
            if self.telegram_message_id is None or self.telegram_message_id <= 0:
                raise ValueError("sent outcome requires a positive Telegram message ID")
        elif self.telegram_message_id is not None:
            raise ValueError("failure outcomes cannot contain a Telegram message ID")
        return self


class TelegramBotIdentity(BaseModel):
    """Minimal getMe response used for exact numeric identity verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: StrictInt


class TelegramChatIdentity(BaseModel):
    """Minimal getChat response used to reject groups and channels."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: StrictInt
    type: str


class TelegramOutboundAction:
    """Redacted action revealed only by the narrow transport implementation."""

    __slots__ = ("__callback_data", "__url", "label")

    def __init__(self, *, label: str, callback_data: str | None, url: str | None) -> None:
        self.label = label
        self.__callback_data = callback_data
        self.__url = url

    def reveal(self) -> tuple[str | None, str | None]:
        """Return callback/link values only at the outbound transport boundary."""
        return self.__callback_data, self.__url

    def __repr__(self) -> str:
        return f"TelegramOutboundAction(label={self.label!r}, action=<redacted>)"


class TelegramTransport(Protocol):
    """Narrow async HTTPS boundary; notably contains no update/poll method."""

    async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity: ...

    async def get_chat(
        self, token: str, chat_id: int, *, timeout_seconds: float
    ) -> TelegramChatIdentity: ...

    async def send_message(
        self,
        token: str,
        chat_id: int,
        text: str,
        action: TelegramOutboundAction,
        *,
        timeout_seconds: float,
    ) -> int: ...


class TelegramValidationTimeout(Exception):
    """Sanitized transport signal for a bounded read timeout."""


class TelegramTransportRejected(Exception):
    """Sanitized transport signal for a definitive provider rejection."""


class TelegramDeliveryUnknown(Exception):
    """Sanitized signal for an ambiguous outcome after send started."""


class TelegramHttpsTransport:
    """Lazy python-telegram-bot HTTPS adapter for the narrow transport port."""

    async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity:
        telegram, error = _telegram_modules()
        try:
            bot = telegram.Bot(token=token)
            result = await bot.get_me(
                read_timeout=timeout_seconds,
                write_timeout=timeout_seconds,
                connect_timeout=timeout_seconds,
                pool_timeout=timeout_seconds,
            )
            return TelegramBotIdentity(id=result.id)
        except error.BadRequest:
            raise TelegramTransportRejected from None
        except (error.TimedOut, error.NetworkError, TimeoutError):
            raise TelegramValidationTimeout from None
        except Exception:
            raise TelegramTransportRejected from None

    async def get_chat(
        self, token: str, chat_id: int, *, timeout_seconds: float
    ) -> TelegramChatIdentity:
        telegram, error = _telegram_modules()
        try:
            bot = telegram.Bot(token=token)
            result = await bot.get_chat(
                chat_id=chat_id,
                read_timeout=timeout_seconds,
                write_timeout=timeout_seconds,
                connect_timeout=timeout_seconds,
                pool_timeout=timeout_seconds,
            )
            return TelegramChatIdentity(id=result.id, type=result.type)
        except error.BadRequest:
            raise TelegramTransportRejected from None
        except (error.TimedOut, error.NetworkError, TimeoutError):
            raise TelegramValidationTimeout from None
        except Exception:
            raise TelegramTransportRejected from None

    async def send_message(
        self,
        token: str,
        chat_id: int,
        text: str,
        action: TelegramOutboundAction,
        *,
        timeout_seconds: float,
    ) -> int:
        telegram, error = _telegram_modules()
        callback_data, url = action.reveal()
        button = telegram.InlineKeyboardButton(
            text=action.label,
            callback_data=callback_data,
            url=url,
        )
        markup = telegram.InlineKeyboardMarkup(((button,),))
        try:
            bot = telegram.Bot(token=token)
            result = await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=None,
                reply_markup=markup,
                read_timeout=timeout_seconds,
                write_timeout=timeout_seconds,
                connect_timeout=timeout_seconds,
                pool_timeout=timeout_seconds,
            )
            return int(result.message_id)
        except asyncio.CancelledError:
            raise TelegramDeliveryUnknown from None
        except error.BadRequest:
            raise TelegramTransportRejected from None
        except (error.TimedOut, TimeoutError, error.NetworkError):
            raise TelegramDeliveryUnknown from None
        except Exception:
            raise TelegramTransportRejected from None


class TelegramNotificationSender:
    """Validate one selected private destination and attempt one outbound send."""

    def __init__(
        self,
        settings: Settings,
        transport: TelegramTransport,
        *,
        validation_timeout_seconds: float = 5.0,
        send_timeout_seconds: float = 5.0,
    ) -> None:
        if validation_timeout_seconds <= 0 or send_timeout_seconds <= 0:
            raise ValueError("Telegram timeouts must be positive")
        self._settings = settings
        self._transport = transport
        self._validation_timeout_seconds = validation_timeout_seconds
        self._send_timeout_seconds = send_timeout_seconds

    async def send(self, request: TelegramNotificationRequest) -> TelegramNotificationOutcome:
        """Return a sanitized delivery result; never mutate approval/order state."""
        config = self._select_config(request.environment)
        if config is None:
            return _failure(request, TelegramDeliveryCode.CONFIG_INVALID)
        if not _is_bound_recipient(config, request):
            return _failure(request, TelegramDeliveryCode.RECIPIENT_NOT_ALLOWED)

        rendered = _render_notification(request, self._settings)
        if rendered is None:
            return _failure(request, TelegramDeliveryCode.MESSAGE_INVALID)
        text, action = rendered
        token = config.bot_token.get_secret_value() if config.bot_token is not None else ""

        try:
            identity = await self._validate(
                lambda: self._transport.get_me(
                    token,
                    timeout_seconds=self._validation_timeout_seconds,
                )
            )
        except TelegramValidationTimeout:
            return _failure(request, TelegramDeliveryCode.VALIDATION_TIMEOUT)
        except TelegramTransportRejected:
            return _failure(request, TelegramDeliveryCode.BOT_IDENTITY_MISMATCH)
        if identity.id != config.expected_bot_id:
            return _failure(request, TelegramDeliveryCode.BOT_IDENTITY_MISMATCH)

        try:
            chat = await self._validate(
                lambda: self._transport.get_chat(
                    token,
                    request.recipient_private_chat_id,
                    timeout_seconds=self._validation_timeout_seconds,
                )
            )
        except TelegramValidationTimeout:
            return _failure(request, TelegramDeliveryCode.VALIDATION_TIMEOUT)
        except TelegramTransportRejected:
            return _failure(request, TelegramDeliveryCode.CHAT_NOT_PRIVATE)
        if chat.id != request.recipient_private_chat_id or chat.type != "private":
            return _failure(request, TelegramDeliveryCode.CHAT_NOT_PRIVATE)

        try:
            message_id = await self._transport.send_message(
                token,
                request.recipient_private_chat_id,
                text,
                action,
                timeout_seconds=self._send_timeout_seconds,
            )
        except (TelegramDeliveryUnknown, asyncio.CancelledError):
            return _failure(request, TelegramDeliveryCode.DELIVERY_UNKNOWN)
        except Exception:
            return _failure(request, TelegramDeliveryCode.DELIVERY_FAILED)
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
            return _failure(request, TelegramDeliveryCode.DELIVERY_FAILED)
        return TelegramNotificationOutcome(
            code=TelegramDeliveryCode.SENT,
            retryable=False,
            environment=request.environment,
            intent_correlation_id=request.intent_correlation_id,
            telegram_message_id=message_id,
        )

    def _select_config(self, environment: TelegramEnvironment) -> TelegramBotSettings | None:
        config = (
            self._settings.telegram_staging
            if environment is TelegramEnvironment.STAGING
            else self._settings.telegram_production
        )
        if (
            not config.enabled
            or config.bot_token is None
            or config.expected_bot_id is None
            or not config.allowed_recipients
        ):
            return None
        return config

    async def _validate(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        for attempt in range(TELEGRAM_VALIDATION_ATTEMPTS):
            try:
                return await operation()
            except TelegramValidationTimeout:
                if attempt + 1 == TELEGRAM_VALIDATION_ATTEMPTS:
                    raise
        raise AssertionError("unreachable")


def _is_bound_recipient(config: TelegramBotSettings, request: TelegramNotificationRequest) -> bool:
    return any(
        recipient.user_id == request.recipient_user_id
        and recipient.private_chat_id == request.recipient_private_chat_id
        for recipient in config.allowed_recipients
    )


def _render_notification(
    request: TelegramNotificationRequest,
    settings: Settings,
) -> tuple[str, TelegramOutboundAction] | None:
    try:
        verify_order_hash(request.proposal)
        expires_at = request.expires_at
    except (TypeError, ValueError):
        return None
    proposal = request.proposal
    risk = request.risk_decision
    if (
        risk.risk_decision_id != proposal.risk_decision_id
        or (risk.proposal_id is not None and risk.proposal_id != proposal.proposal_id)
        or (risk.candidate_id is not None and risk.candidate_id != proposal.candidate_id)
        or risk.outcome is not RiskOutcome.APPROVED
        or expires_at <= proposal.created_at
        or expires_at > proposal.expires_at
        or (
            request.category is TelegramNotificationCategory.PAPER
            and proposal.account_scope is not AccountScope.PAPER
        )
        or (
            request.category is TelegramNotificationCategory.LIVE
            and proposal.account_scope is not AccountScope.AGENTIC
        )
    ):
        return None
    dynamic_text = (
        proposal.proposal_id,
        proposal.instrument_id,
        proposal.symbol,
        proposal.strategy,
        proposal.strategy_version,
        risk.reason_code,
        risk.reason,
        *(item.rule_code for item in risk.violations),
        *(item.reason for item in risk.violations),
    )
    if any(_CONTROL_CHARACTER.search(value) for value in dynamic_text):
        return None

    reasons = [f"{risk.reason_code}: {risk.reason}"]
    reasons.extend(f"{item.rule_code}: {item.reason}" for item in risk.violations)
    lines = [
        f"{request.category.value} ORDER NOTIFICATION",
        f"Proposal: {proposal.proposal_id}",
        f"Instrument: {proposal.symbol} ({proposal.instrument_id})",
        f"Side: {proposal.side.value}",
        f"Quantity: {format_canonical_decimal(proposal.quantity)} shares",
        f"Order type: {proposal.order_type.value}",
        f"Limit price: {format_canonical_decimal(proposal.limit_price)} {proposal.currency}",
        "Maximum notional: "
        f"{format_canonical_decimal(proposal.maximum_notional)} {proposal.currency}",
        f"Time in force: {proposal.time_in_force.value}",
        f"Expires: {expires_at.isoformat().replace('+00:00', 'Z')}",
        f"Strategy: {proposal.strategy} v{proposal.strategy_version}",
        f"Risk: {risk.outcome.value}",
        "Risk reasons:",
        *(f"- {reason}" for reason in reasons),
    ]
    text = "\n".join(lines)
    if len(text) > TELEGRAM_MESSAGE_LIMIT:
        return None

    if request.category is TelegramNotificationCategory.PAPER:
        assert request.paper_nonce is not None
        action = TelegramOutboundAction(
            label="Approve PAPER",
            callback_data=request.paper_nonce.reveal(),
            url=None,
        )
    else:
        if request.live_approval_link is None or settings.webauthn.origin is None:
            return None
        link = request.live_approval_link.get_secret_value()
        if not _has_fixed_origin(link, settings.webauthn.origin):
            return None
        action = TelegramOutboundAction(
            label="Review LIVE approval",
            callback_data=None,
            url=link,
        )
    return text, action


def _has_fixed_origin(link: str, origin: str) -> bool:
    try:
        parsed = urlsplit(link)
        expected = urlsplit(origin)
        _ = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or not parsed.path.startswith("/")
        or parsed.fragment
    ):
        return False
    actual_port = parsed.port or 443
    expected_port = expected.port or 443
    return parsed.hostname.lower() == expected.hostname and actual_port == expected_port


def _failure(
    request: TelegramNotificationRequest, code: TelegramDeliveryCode
) -> TelegramNotificationOutcome:
    return TelegramNotificationOutcome(
        code=code,
        retryable=code is TelegramDeliveryCode.VALIDATION_TIMEOUT,
        environment=request.environment,
        intent_correlation_id=request.intent_correlation_id,
    )


def _telegram_modules() -> tuple[Any, Any]:
    try:
        telegram = importlib.import_module("telegram")
        error = importlib.import_module("telegram.error")
    except ImportError as exc:  # pragma: no cover - optional runtime dependency
        raise TelegramTransportRejected from exc
    return telegram, error


__all__ = [
    "TELEGRAM_MESSAGE_LIMIT",
    "TELEGRAM_VALIDATION_ATTEMPTS",
    "TelegramBotIdentity",
    "TelegramChatIdentity",
    "TelegramDeliveryCode",
    "TelegramDeliveryUnknown",
    "TelegramEnvironment",
    "TelegramHttpsTransport",
    "TelegramNotificationCategory",
    "TelegramNotificationOutcome",
    "TelegramNotificationRequest",
    "TelegramNotificationSender",
    "TelegramOutboundAction",
    "TelegramTransport",
    "TelegramTransportRejected",
    "TelegramValidationTimeout",
]
