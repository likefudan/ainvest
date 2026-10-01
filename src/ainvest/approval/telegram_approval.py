"""Fail-closed Telegram callback approval for Paper proposals only (P05-T1)."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.service import ApprovalService, ApprovalServiceError
from ainvest.approval.telegram import (
    TelegramDeliveryCode,
    TelegramNotificationCategory,
    TelegramNotificationOutcome,
    TelegramNotificationRequest,
)
from ainvest.approval.telegram_updates import (
    AuthorizedCallbackUpdate,
    AuthorizedTelegramUpdate,
    TelegramHandlerDisposition,
)
from ainvest.approval.tokens import OpaqueApprovalToken, hash_approval_token
from ainvest.audit.envelope import ActorType, AuditEventEnvelope, AuditEventType
from ainvest.audit.service import AuditService
from ainvest.db.errors import PersistenceError
from ainvest.db.uow import UnitOfWork
from ainvest.schemas.approval import ApprovalEventOutcome, ApprovalMethod, ApprovalScope
from ainvest.schemas.common import ensure_utc

type Clock = Callable[[], datetime]
type IdFactory = Callable[[str], str]


class TelegramApprovalCode(StrEnum):
    APPROVED = "approved"
    EXPIRED = "expired"
    ALREADY_USED = "already_used"
    INVALID = "invalid"


class TelegramCallbackAnswerTransport(Protocol):
    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str,
        *,
        timeout_seconds: float,
    ) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def bind_telegram_approval_delivery(
    uow: UnitOfWork,
    *,
    challenge_id: str,
    request: TelegramNotificationRequest,
    outcome: TelegramNotificationOutcome,
    bound_at: datetime,
) -> None:
    """Persist the exact trusted delivery target for one pending challenge."""
    if (
        request.category is not TelegramNotificationCategory.PAPER
        or request.paper_nonce is None
        or outcome.code is not TelegramDeliveryCode.SENT
        or outcome.telegram_message_id is None
        or outcome.environment is not request.environment
        or outcome.intent_correlation_id != request.intent_correlation_id
    ):
        raise ValueError("telegram approval delivery is not bindable")
    normalized_bound_at = ensure_utc(bound_at)
    challenge = uow.approvals_repo.get_challenge(challenge_id)
    proposal = uow.proposals_repo.get_by_proposal_id(request.proposal.proposal_id)
    if (
        challenge is None
        or proposal is None
        or challenge.status != "PENDING"
        or challenge.method != ApprovalMethod.TELEGRAM.value
        or challenge.scope != ApprovalScope.PAPER.value
        or challenge.proposal_id != request.proposal.proposal_id
        or challenge.order_hash != request.proposal.order_hash
        or proposal.order_hash != request.proposal.order_hash
        or challenge.token_hash != hash_approval_token(request.paper_nonce)
        or normalized_bound_at >= challenge.expires_at
        or normalized_bound_at >= proposal.expires_at
    ):
        raise ValueError("telegram approval challenge is not bindable")
    fields = {
        "challenge_id": challenge_id,
        "proposal_id": request.proposal.proposal_id,
        "order_hash": request.proposal.order_hash,
        "environment": request.environment.value,
        "user_id": request.recipient_user_id,
        "chat_id": request.recipient_private_chat_id,
        "message_id": outcome.telegram_message_id,
        "bound_at": normalized_bound_at,
    }
    try:
        stored, created = uow.approvals_repo.bind_telegram_message(fields)
    except PersistenceError as exc:
        raise ValueError("telegram approval binding conflicts with an existing message") from exc
    if not created and any(getattr(stored, key) != value for key, value in fields.items()):
        raise ValueError("telegram approval challenge is already bound differently")


class TelegramPaperApprovalHandler:
    """Validate one bound callback and atomically persist its Paper approval."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        answer_transport: TelegramCallbackAnswerTransport | None = None,
        clock: Clock = _utc_now,
        id_factory: IdFactory = _new_id,
        answer_timeout_seconds: float = 4.0,
    ) -> None:
        if answer_timeout_seconds <= 0:
            raise ValueError("callback answer timeout must be positive")
        self._session_factory = session_factory
        self._answer_transport = answer_transport
        self._clock = clock
        self._id_factory = id_factory
        self._answer_timeout_seconds = answer_timeout_seconds

    async def handle(self, update: AuthorizedTelegramUpdate) -> TelegramHandlerDisposition:
        if not isinstance(update, AuthorizedCallbackUpdate):
            return TelegramHandlerDisposition.TERMINAL_HANDLED
        code = self.process(update)
        await self._answer(update.callback_query_id, code)
        return TelegramHandlerDisposition.TERMINAL_HANDLED

    def process(self, update: AuthorizedCallbackUpdate) -> TelegramApprovalCode:
        """Run the complete durable decision transaction for one callback."""
        try:
            token = OpaqueApprovalToken(update.callback_data.get_secret_value())
            token_hash = hash_approval_token(token)
        except ValueError:
            return TelegramApprovalCode.INVALID

        now = ensure_utc(self._clock())
        with UnitOfWork(self._session_factory) as uow:
            uow.begin_atomic_write()
            challenge = uow.approvals_repo.get_challenge_by_token_hash(token_hash)
            if challenge is None:
                return TelegramApprovalCode.INVALID
            binding = uow.approvals_repo.get_telegram_binding(challenge.challenge_id)
            if binding is None or not _binding_matches(binding, challenge, update):
                return TelegramApprovalCode.INVALID
            if (
                challenge.method != ApprovalMethod.TELEGRAM.value
                or challenge.scope != ApprovalScope.PAPER.value
            ):
                return TelegramApprovalCode.INVALID

            service = ApprovalService.from_uow(
                uow,
                clock=lambda: now,
                id_factory=self._id_factory,
            )
            try:
                event = service.decide(
                    token,
                    approved=True,
                    approver_identity=f"telegram:{update.sender_user_id}",
                )
            except ApprovalServiceError as exc:
                if exc.code == "CHALLENGE_ALREADY_USED":
                    return TelegramApprovalCode.ALREADY_USED
                if exc.code in {
                    "CHALLENGE_NOT_FOUND",
                    "INVALID_APPROVAL_TOKEN",
                    "PROPOSAL_INTEGRITY_FAILED",
                    "CHALLENGE_INTEGRITY_FAILED",
                }:
                    return TelegramApprovalCode.INVALID
                raise

            audit = AuditService.from_uow(uow)
            audit.append(
                AuditEventEnvelope(
                    event_id=self._id_factory("audit"),
                    event_type=(
                        AuditEventType.APPROVAL_CONSUMED
                        if event.outcome is ApprovalEventOutcome.APPROVED
                        else AuditEventType.APPROVAL_DENIED
                    ),
                    occurred_at=now,
                    correlation_id=f"telegram-update:{update.environment.value}:{update.update_id}",
                    actor_type=ActorType.USER,
                    actor_id=event.approver_identity,
                    subject_type="order_proposal",
                    subject_id=event.proposal_id,
                    before_state={"approval_status": "PENDING"},
                    after_state={"approval_status": event.outcome.value},
                    payload={
                        "approval_event_id": event.event_id,
                        "approval_method": event.method.value,
                        "approval_scope": event.scope.value,
                        "order_hash": event.order_hash,
                    },
                )
            )
            if event.outcome is ApprovalEventOutcome.APPROVED:
                uow.approvals_repo.add_outbox(
                    {
                        "outbox_id": self._id_factory("apob"),
                        "approval_event_id": event.event_id,
                        "proposal_id": event.proposal_id,
                        "order_hash": event.order_hash,
                        "status": "PENDING",
                        "created_at": now,
                        "payload_json": {
                            "schema_version": "1.0",
                            "approval_event_id": event.event_id,
                            "proposal_id": event.proposal_id,
                            "order_hash": event.order_hash,
                            "approval_method": ApprovalMethod.TELEGRAM.value,
                            "approval_scope": ApprovalScope.PAPER.value,
                        },
                    }
                )
                return TelegramApprovalCode.APPROVED
            return TelegramApprovalCode.EXPIRED

    async def _answer(self, callback_query_id: SecretStr, code: TelegramApprovalCode) -> None:
        if self._answer_transport is None:
            return
        text = {
            TelegramApprovalCode.APPROVED: "Paper approval recorded.",
            TelegramApprovalCode.EXPIRED: "Approval expired.",
            TelegramApprovalCode.ALREADY_USED: "Approval already processed.",
            TelegramApprovalCode.INVALID: "Approval rejected.",
        }[code]
        try:
            await self._answer_transport.answer_callback_query(
                callback_query_id.get_secret_value(),
                text,
                timeout_seconds=self._answer_timeout_seconds,
            )
        except Exception:
            # The business decision is already durable. Provider acknowledgement
            # failure must not turn it into a replayable approval attempt.
            return


def _binding_matches(binding: object, challenge: object, update: AuthorizedCallbackUpdate) -> bool:
    return (
        getattr(binding, "environment", None) == update.environment.value
        and getattr(binding, "user_id", None) == update.sender_user_id
        and getattr(binding, "chat_id", None) == update.chat_id
        and getattr(binding, "message_id", None) == update.message_id
        and getattr(binding, "challenge_id", None) == getattr(challenge, "challenge_id", None)
        and getattr(binding, "proposal_id", None) == getattr(challenge, "proposal_id", None)
        and getattr(binding, "order_hash", None) == getattr(challenge, "order_hash", None)
    )


__all__ = [
    "TelegramApprovalCode",
    "TelegramCallbackAnswerTransport",
    "TelegramPaperApprovalHandler",
    "bind_telegram_approval_delivery",
]
