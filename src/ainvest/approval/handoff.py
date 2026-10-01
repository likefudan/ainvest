"""Durable, fail-closed approval-to-execution handoff (P05-T6)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.order_hash import parse_order_proposal
from ainvest.audit.digests import digest_json
from ainvest.audit.envelope import ActorType, AuditEventEnvelope, AuditEventType
from ainvest.audit.service import AuditService
from ainvest.db.errors import ConcurrentModificationError
from ainvest.db.uow import UnitOfWork
from ainvest.schemas.approval import (
    ApprovalEvent,
    ApprovalEventOutcome,
    ApprovalMethod,
    ApprovalScope,
)
from ainvest.schemas.common import ensure_utc
from ainvest.schemas.orders import OrderProposal
from ainvest.schemas.portfolio import AccountScope

type Clock = Callable[[], datetime]


class StoredApprovalOutbox(Protocol):
    outbox_id: str
    approval_event_id: str
    proposal_id: str
    order_hash: str
    status: str
    created_at: datetime


class StoredApprovalEvent(Protocol):
    event_id: str
    proposal_id: str
    order_hash: str
    method: str
    scope: str
    outcome: str
    payload_json: dict[str, object]


class StoredOrderProposal(Protocol):
    proposal_id: str
    order_hash: str
    account_scope: str
    expires_at: datetime
    payload_json: dict[str, object]


class ApprovalHandoffCode(StrEnum):
    DISPATCHED = "dispatched"
    EXECUTION_REJECTED = "execution_rejected"
    POLICY_REJECTED = "policy_rejected"
    RETRY_LATER = "retry_later"
    ALREADY_CONSUMED = "already_consumed"
    NOT_FOUND = "not_found"


class ApprovalExecutionOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    RETRY_LATER = "RETRY_LATER"


@dataclass(frozen=True, slots=True)
class PaperExecutionHandoff:
    command_id: str
    correlation_id: str
    idempotency_id: str
    issued_at: datetime
    input_digest: str
    proposal_id: str
    order_hash: str
    client_order_id: str
    approval_event_id: str


@dataclass(frozen=True, slots=True)
class ApprovalExecutionResult:
    command_id: str
    correlation_id: str
    idempotency_id: str
    proposal_id: str
    client_order_id: str
    outcome: ApprovalExecutionOutcome
    event_id: str | None = None
    reason_code: str | None = None
    output_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalHandoffResult:
    code: ApprovalHandoffCode
    request: PaperExecutionHandoff | None = None
    execution_result: ApprovalExecutionResult | None = None


class ExecutionHandoffPort(Protocol):
    """Neutral Paper boundary; the orchestrator retains workflow/execution."""

    def dispatch(self, request: PaperExecutionHandoff) -> ApprovalExecutionResult: ...


class ApprovalHandoffContractError(RuntimeError):
    """Raised when the composition bridge violates the handoff contract."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ApprovalHandoffService:
    """Consume one durable approval outbox record with exactly-once effect."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        execution: ExecutionHandoffPort,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._execution = execution
        self._clock = clock

    def consume(self, outbox_id: str) -> ApprovalHandoffResult:
        now = ensure_utc(self._clock())
        with UnitOfWork(self._session_factory) as uow:
            uow.begin_atomic_write()
            outbox = uow.approvals_repo.get_outbox(outbox_id, for_update=True)
            if outbox is None:
                return ApprovalHandoffResult(ApprovalHandoffCode.NOT_FOUND)
            if outbox.status == "CONSUMED":
                return ApprovalHandoffResult(ApprovalHandoffCode.ALREADY_CONSUMED)
            if outbox.status != "PENDING":
                return self._reject(uow, outbox, now=now, reason="INVALID_OUTBOX_STATUS")

            event_row = uow.approvals_repo.get_event(outbox.approval_event_id)
            proposal_row = uow.proposals_repo.get_by_proposal_id(outbox.proposal_id)
            if event_row is None or proposal_row is None:
                return self._reject(uow, outbox, now=now, reason="INVALID_TRUSTED_RECORD")
            try:
                approval = ApprovalEvent.model_validate(dict(event_row.payload_json))
                proposal = parse_order_proposal(dict(proposal_row.payload_json))
            except (ValidationError, ValueError, TypeError):
                return self._reject(uow, outbox, now=now, reason="INVALID_TRUSTED_RECORD")

            rejection = _policy_rejection(
                outbox=outbox,
                event_row=event_row,
                proposal_row=proposal_row,
                approval=approval,
                proposal=proposal,
                now=now,
            )
            if rejection is not None:
                return self._reject(uow, outbox, now=now, reason=rejection)

            request = _request_for(outbox, approval)
            dispatched = self._execution.dispatch(request)
            _validate_dispatch_result(request, dispatched)
            if dispatched.outcome is ApprovalExecutionOutcome.RETRY_LATER:
                return ApprovalHandoffResult(
                    ApprovalHandoffCode.RETRY_LATER,
                    request=request,
                    execution_result=dispatched,
                )

            try:
                uow.approvals_repo.consume_outbox_once(outbox.outbox_id)
            except ConcurrentModificationError:
                return ApprovalHandoffResult(ApprovalHandoffCode.ALREADY_CONSUMED)
            code = (
                ApprovalHandoffCode.DISPATCHED
                if dispatched.outcome is ApprovalExecutionOutcome.SUCCEEDED
                else ApprovalHandoffCode.EXECUTION_REJECTED
            )
            _append_audit(
                uow,
                outbox=outbox,
                occurred_at=now,
                request=request,
                result=dispatched,
                code=code,
            )
            return ApprovalHandoffResult(
                code,
                request=request,
                execution_result=dispatched,
            )

    def _reject(
        self,
        uow: UnitOfWork,
        outbox: StoredApprovalOutbox,
        *,
        now: datetime,
        reason: str,
    ) -> ApprovalHandoffResult:
        try:
            uow.approvals_repo.consume_outbox_once(outbox.outbox_id)
        except ConcurrentModificationError:
            return ApprovalHandoffResult(ApprovalHandoffCode.ALREADY_CONSUMED)
        _append_policy_rejection(uow, outbox=outbox, occurred_at=now, reason=reason)
        return ApprovalHandoffResult(ApprovalHandoffCode.POLICY_REJECTED)


def _policy_rejection(
    *,
    outbox: StoredApprovalOutbox,
    event_row: StoredApprovalEvent,
    proposal_row: StoredOrderProposal,
    approval: ApprovalEvent,
    proposal: OrderProposal,
    now: datetime,
) -> str | None:
    if (
        approval.event_id != outbox.approval_event_id
        or approval.event_id != event_row.event_id
        or approval.proposal_id != outbox.proposal_id
        or approval.proposal_id != event_row.proposal_id
        or approval.proposal_id != proposal_row.proposal_id
        or approval.order_hash != outbox.order_hash
        or approval.order_hash != event_row.order_hash
        or approval.order_hash != proposal_row.order_hash
        or approval.order_hash != proposal.order_hash
        or approval.method.value != event_row.method
        or approval.scope.value != event_row.scope
        or approval.outcome.value != event_row.outcome
        or proposal.account_scope.value != proposal_row.account_scope
        or proposal.expires_at != proposal_row.expires_at
    ):
        return "TRUSTED_BINDING_MISMATCH"
    if approval.outcome is not ApprovalEventOutcome.APPROVED:
        return "APPROVAL_NOT_APPROVED"
    if (
        approval.method is not ApprovalMethod.TELEGRAM
        or approval.scope is not ApprovalScope.PAPER
        or proposal.account_scope is not AccountScope.PAPER
    ):
        return "FORBIDDEN_METHOD_SCOPE"
    if now >= proposal.expires_at:
        return "PROPOSAL_EXPIRED"
    return None


def _stable_suffix(domain: str, outbox_id: str, approval_event_id: str) -> str:
    material = f"ainvest:{domain}:v1:{outbox_id}:{approval_event_id}".encode()
    return hashlib.sha256(material).hexdigest()[:40]


def _request_for(outbox: StoredApprovalOutbox, approval: ApprovalEvent) -> PaperExecutionHandoff:
    suffix = _stable_suffix("paper-handoff", outbox.outbox_id, approval.event_id)
    return PaperExecutionHandoff(
        command_id=f"cmd_{suffix}",
        correlation_id=f"corr_{suffix}",
        idempotency_id=f"idem_{suffix}",
        issued_at=ensure_utc(outbox.created_at),
        input_digest=approval.order_hash,
        proposal_id=approval.proposal_id,
        order_hash=approval.order_hash,
        client_order_id=f"paper_{suffix}",
        approval_event_id=approval.event_id,
    )


def _validate_dispatch_result(
    request: PaperExecutionHandoff, result: ApprovalExecutionResult
) -> None:
    if (
        result.command_id != request.command_id
        or result.correlation_id != request.correlation_id
        or result.idempotency_id != request.idempotency_id
    ):
        raise ApprovalHandoffContractError("execution result trace does not match request")
    if (
        result.proposal_id != request.proposal_id
        or result.client_order_id != request.client_order_id
    ):
        raise ApprovalHandoffContractError("execution result subject does not match request")


def _append_audit(
    uow: UnitOfWork,
    *,
    outbox: StoredApprovalOutbox,
    occurred_at: datetime,
    request: PaperExecutionHandoff,
    result: ApprovalExecutionResult,
    code: ApprovalHandoffCode,
) -> None:
    suffix = _stable_suffix("handoff-audit", outbox.outbox_id, request.approval_event_id)
    AuditService.from_uow(uow).append(
        AuditEventEnvelope(
            event_id=f"audit_handoff_{suffix}",
            event_type=AuditEventType.GENERIC,
            occurred_at=occurred_at,
            correlation_id=request.correlation_id,
            causation_id=request.command_id,
            actor_type=ActorType.SYSTEM,
            actor_id="approval-handoff",
            subject_type="order_proposal",
            subject_id=request.proposal_id,
            input_digest=request.order_hash,
            output_digest=result.output_digest or digest_json(asdict(result)),
            before_state={"outbox_status": "PENDING"},
            after_state={"outbox_status": "CONSUMED", "handoff": code.value},
            payload={"approval_event_id": request.approval_event_id},
        )
    )


def _append_policy_rejection(
    uow: UnitOfWork,
    *,
    outbox: StoredApprovalOutbox,
    occurred_at: datetime,
    reason: str,
) -> None:
    suffix = _stable_suffix("handoff-rejection-audit", outbox.outbox_id, outbox.approval_event_id)
    AuditService.from_uow(uow).append(
        AuditEventEnvelope(
            event_id=f"audit_handoff_{suffix}",
            event_type=AuditEventType.ERROR,
            occurred_at=occurred_at,
            correlation_id=(
                "corr_"
                + _stable_suffix("handoff-rejection", outbox.outbox_id, outbox.approval_event_id)
            ),
            actor_type=ActorType.SYSTEM,
            actor_id="approval-handoff",
            subject_type="order_proposal",
            subject_id=outbox.proposal_id,
            error_code=reason,
            before_state={"outbox_status": "PENDING"},
            after_state={"outbox_status": "CONSUMED", "handoff": "policy_rejected"},
            payload={"approval_event_id": outbox.approval_event_id},
        )
    )


__all__ = [
    "ApprovalExecutionOutcome",
    "ApprovalExecutionResult",
    "ApprovalHandoffCode",
    "ApprovalHandoffContractError",
    "ApprovalHandoffResult",
    "ApprovalHandoffService",
    "ExecutionHandoffPort",
    "PaperExecutionHandoff",
]
