"""Domain commands for the trading workflow orchestrator (P02-T10).

Commands are frozen envelopes. They reference domain subjects by stable ID and
input digests — handlers load full schema objects. This package imports only
``ainvest.schemas`` (and local workflow helpers), never ORM or broker clients.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import StringConstraints, model_validator

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    DomainModel,
    MachineCode,
    SchemaVersion,
    StableId,
    UtcDateTime,
)
from ainvest.schemas.orders import OrderHashDigest
from ainvest.workflow.ids import (
    CausationId,
    CommandId,
    CorrelationId,
    IdempotencyId,
    TraceContext,
    new_command_id,
)
from ainvest.workflow.semantics import (
    CommandType,
    RetrySemantics,
    retry_semantics_for,
)

ActorId = Annotated[str, StringConstraints(min_length=1, max_length=128)]
InputDigest = Annotated[
    str,
    StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$", min_length=71, max_length=71),
]
ReasonText = Annotated[str, StringConstraints(min_length=1, max_length=512)]

_DEFAULT_SYSTEM_ACTOR_ID = "system"


class ActorKind(StrEnum):
    """Who issued the command."""

    SYSTEM = "system"
    STRATEGY = "strategy"
    USER = "user"
    OPERATOR = "operator"


class ManualReviewResolution(StrEnum):
    """Operator resolution for ``MANUAL_REVIEW`` order/cancel states."""

    CONFIRM_SUBMITTED = "CONFIRM_SUBMITTED"
    CONFIRM_CANCELLED = "CONFIRM_CANCELLED"
    CONFIRM_REJECTED = "CONFIRM_REJECTED"
    CONFIRM_NOT_APPLIED = "CONFIRM_NOT_APPLIED"
    KEEP_MANUAL_REVIEW = "KEEP_MANUAL_REVIEW"


def require_real_operator(*, actor_kind: ActorKind, actor_id: str) -> None:
    """Fail closed unless a privileged command names a real operator identity."""
    if actor_kind is not ActorKind.OPERATOR:
        raise ValueError("privileged command requires actor_kind=OPERATOR")
    normalized = actor_id.strip()
    if not normalized or normalized.casefold() == _DEFAULT_SYSTEM_ACTOR_ID:
        raise ValueError("privileged command requires a non-system operator actor_id")


class CommandEnvelope(DomainModel):
    """Shared header for every domain command.

    ``idempotency_id`` must be stable across retries of the same intended
    effect. Handlers and the dispatcher must not rely on hidden process-local
    business state to decide outcomes.
    """

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    command_id: CommandId
    command_type: CommandType
    correlation_id: CorrelationId
    causation_id: CausationId | None = None
    idempotency_id: IdempotencyId
    issued_at: UtcDateTime
    actor_kind: ActorKind = ActorKind.SYSTEM
    actor_id: ActorId = "system"
    input_digest: InputDigest | None = None

    @property
    def retry_semantics(self) -> RetrySemantics:
        return retry_semantics_for(self.command_type)

    @property
    def trace(self) -> TraceContext:
        return TraceContext(
            correlation_id=self.correlation_id,
            causation_id=self.causation_id,
            idempotency_id=self.idempotency_id,
        )


class EvaluateStrategyCommand(CommandEnvelope):
    """Run a strategy evaluation against a prepared context snapshot."""

    command_type: Literal[CommandType.EVALUATE_STRATEGY] = CommandType.EVALUATE_STRATEGY
    strategy_run_id: StableId
    strategy_name: Annotated[str, StringConstraints(min_length=2, max_length=64)]
    strategy_version: Annotated[str, StringConstraints(min_length=5, max_length=32)]
    context_digest: InputDigest


class SizePositionCommand(CommandEnvelope):
    """Turn a trade signal into a candidate order via the position sizer."""

    command_type: Literal[CommandType.SIZE_POSITION] = CommandType.SIZE_POSITION
    signal_id: StableId
    portfolio_snapshot_id: StableId | None = None
    sizing_config_digest: InputDigest | None = None


class EvaluateRiskCommand(CommandEnvelope):
    """Evaluate risk for a candidate (screening) or proposal (pre-trade)."""

    command_type: Literal[CommandType.EVALUATE_RISK] = CommandType.EVALUATE_RISK
    candidate_id: StableId | None = None
    proposal_id: StableId | None = None
    risk_config_digest: InputDigest | None = None

    @model_validator(mode="after")
    def _require_subject(self) -> EvaluateRiskCommand:
        if self.candidate_id is None and self.proposal_id is None:
            raise ValueError("risk evaluation requires candidate_id or proposal_id")
        return self


class CreateProposalCommand(CommandEnvelope):
    """Bind a risk-approved candidate into an order proposal + hash."""

    command_type: Literal[CommandType.CREATE_PROPOSAL] = CommandType.CREATE_PROPOSAL
    candidate_id: StableId
    risk_decision_id: StableId


class RequestApprovalCommand(CommandEnvelope):
    """Create an approval challenge for a proposal (Telegram paper / Passkey)."""

    command_type: Literal[CommandType.REQUEST_APPROVAL] = CommandType.REQUEST_APPROVAL
    proposal_id: StableId
    order_hash: OrderHashDigest


class ConsumeApprovalCommand(CommandEnvelope):
    """Consume a one-time approval challenge (approve or deny)."""

    command_type: Literal[CommandType.CONSUME_APPROVAL] = CommandType.CONSUME_APPROVAL
    challenge_id: StableId
    proposal_id: StableId
    order_hash: OrderHashDigest
    approved: bool


class ExecuteOrderCommand(CommandEnvelope):
    """Submit an approved proposal to the paper or live broker write path.

    Retry semantics are ``BROKER_WRITE``: never blind-retry. On uncertain
    outcomes, dispatch ``ReconcileCommand`` first.
    """

    command_type: Literal[CommandType.EXECUTE_ORDER] = CommandType.EXECUTE_ORDER
    proposal_id: StableId
    order_hash: OrderHashDigest
    client_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    approval_event_id: StableId


class CancelOrderCommand(CommandEnvelope):
    """Cancel a working broker order (separate cancel idempotency ID).

    Retry semantics are ``BROKER_WRITE``: never blind-retry an uncertain cancel.
    Cancellation is privileged (design §5.6): requires an authenticated operator.
    """

    command_type: Literal[CommandType.CANCEL_ORDER] = CommandType.CANCEL_ORDER
    cancel_id: StableId
    proposal_id: StableId
    broker_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    order_hash: OrderHashDigest
    reason_code: MachineCode
    reason: ReasonText | None = None
    # Operator identity is mandatory for privileged actions (design §1.2 #29 / §5.6).
    actor_kind: Literal[ActorKind.OPERATOR] = ActorKind.OPERATOR
    actor_id: ActorId  # required; must not default to "system"

    @model_validator(mode="after")
    def _require_operator(self) -> CancelOrderCommand:
        require_real_operator(actor_kind=self.actor_kind, actor_id=self.actor_id)
        return self


class ResolveManualReviewCommand(CommandEnvelope):
    """Privileged operator resolution of a ``MANUAL_REVIEW`` state."""

    command_type: Literal[CommandType.RESOLVE_MANUAL_REVIEW] = CommandType.RESOLVE_MANUAL_REVIEW
    subject_id: StableId
    resolution: ManualReviewResolution
    reason_code: MachineCode
    reason: ReasonText
    # Operator identity is mandatory for privileged actions (design §1.2 #29).
    actor_kind: Literal[ActorKind.OPERATOR] = ActorKind.OPERATOR
    actor_id: ActorId  # required; must not default to "system"

    @model_validator(mode="after")
    def _require_operator(self) -> ResolveManualReviewCommand:
        require_real_operator(actor_kind=self.actor_kind, actor_id=self.actor_id)
        return self


class ReconcileCommand(CommandEnvelope):
    """Reconcile local order/cancel state against broker truth.

    Typed stub for P03-T15: carries IDs and retry class only. Does not implement
    ledger or fill-matching logic.
    """

    command_type: Literal[CommandType.RECONCILE] = CommandType.RECONCILE
    proposal_id: StableId | None = None
    broker_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)] | None = None
    cancel_id: StableId | None = None
    client_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)] | None = None

    @model_validator(mode="after")
    def _require_subject(self) -> ReconcileCommand:
        if self.broker_order_id is None and self.cancel_id is None and self.client_order_id is None:
            raise ValueError("reconcile requires broker_order_id, cancel_id, or client_order_id")
        return self


WorkflowCommand = (
    EvaluateStrategyCommand
    | SizePositionCommand
    | EvaluateRiskCommand
    | CreateProposalCommand
    | RequestApprovalCommand
    | ConsumeApprovalCommand
    | ExecuteOrderCommand
    | CancelOrderCommand
    | ResolveManualReviewCommand
    | ReconcileCommand
)

COMMAND_TYPE_TO_MODEL: dict[CommandType, type[CommandEnvelope]] = {
    CommandType.EVALUATE_STRATEGY: EvaluateStrategyCommand,
    CommandType.SIZE_POSITION: SizePositionCommand,
    CommandType.EVALUATE_RISK: EvaluateRiskCommand,
    CommandType.CREATE_PROPOSAL: CreateProposalCommand,
    CommandType.REQUEST_APPROVAL: RequestApprovalCommand,
    CommandType.CONSUME_APPROVAL: ConsumeApprovalCommand,
    CommandType.EXECUTE_ORDER: ExecuteOrderCommand,
    CommandType.CANCEL_ORDER: CancelOrderCommand,
    CommandType.RESOLVE_MANUAL_REVIEW: ResolveManualReviewCommand,
    CommandType.RECONCILE: ReconcileCommand,
}


def allocate_command_id() -> str:
    """Public helper so callers share the same ID allocator as the package."""
    return new_command_id()


__all__ = [
    "COMMAND_TYPE_TO_MODEL",
    "ActorId",
    "ActorKind",
    "CancelOrderCommand",
    "CommandEnvelope",
    "ConsumeApprovalCommand",
    "CreateProposalCommand",
    "EvaluateRiskCommand",
    "EvaluateStrategyCommand",
    "ExecuteOrderCommand",
    "InputDigest",
    "ManualReviewResolution",
    "ReconcileCommand",
    "RequestApprovalCommand",
    "ResolveManualReviewCommand",
    "SizePositionCommand",
    "WorkflowCommand",
    "allocate_command_id",
    "require_real_operator",
]
