"""Domain result events corresponding to workflow commands (P02-T10).

Every event carries the same correlation and idempotency IDs as its causing
command, with ``causation_id`` set to that command's ``command_id``. One
correlation ID therefore connects the full workflow timeline.
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
from ainvest.schemas.risk import RiskOutcome
from ainvest.workflow.ids import (
    CausationId,
    CorrelationId,
    EventId,
    IdempotencyId,
    TraceContext,
    new_event_id,
)
from ainvest.workflow.semantics import CommandType

ReasonText = Annotated[str, StringConstraints(min_length=1, max_length=512)]
OutputDigest = Annotated[
    str,
    StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$", min_length=71, max_length=71),
]


class EventType(StrEnum):
    """Stable event type codes mirroring command outcomes."""

    STRATEGY_EVALUATED = "STRATEGY_EVALUATED"
    POSITION_SIZED = "POSITION_SIZED"
    RISK_EVALUATED = "RISK_EVALUATED"
    PROPOSAL_CREATED = "PROPOSAL_CREATED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_CONSUMED = "APPROVAL_CONSUMED"
    ORDER_EXECUTED = "ORDER_EXECUTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    MANUAL_REVIEW_RESOLVED = "MANUAL_REVIEW_RESOLVED"
    RECONCILED = "RECONCILED"
    COMMAND_REJECTED = "COMMAND_REJECTED"


class CommandOutcome(StrEnum):
    """Coarse business outcome for a dispatched command."""

    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNKNOWN = "UNKNOWN"
    # Broker write completed with uncertain broker acknowledgement.
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    CANCEL_UNKNOWN = "CANCEL_UNKNOWN"


COMMAND_TO_EVENT_TYPE: dict[CommandType, EventType] = {
    CommandType.EVALUATE_STRATEGY: EventType.STRATEGY_EVALUATED,
    CommandType.SIZE_POSITION: EventType.POSITION_SIZED,
    CommandType.EVALUATE_RISK: EventType.RISK_EVALUATED,
    CommandType.CREATE_PROPOSAL: EventType.PROPOSAL_CREATED,
    CommandType.REQUEST_APPROVAL: EventType.APPROVAL_REQUESTED,
    CommandType.CONSUME_APPROVAL: EventType.APPROVAL_CONSUMED,
    CommandType.EXECUTE_ORDER: EventType.ORDER_EXECUTED,
    CommandType.CANCEL_ORDER: EventType.ORDER_CANCELLED,
    CommandType.RESOLVE_MANUAL_REVIEW: EventType.MANUAL_REVIEW_RESOLVED,
    CommandType.RECONCILE: EventType.RECONCILED,
}


class EventEnvelope(DomainModel):
    """Shared header for every domain result event."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    event_id: EventId
    event_type: EventType
    correlation_id: CorrelationId
    causation_id: CausationId
    idempotency_id: IdempotencyId
    occurred_at: UtcDateTime
    outcome: CommandOutcome
    reason_code: MachineCode | None = None
    reason: ReasonText | None = None
    output_digest: OutputDigest | None = None

    @property
    def trace(self) -> TraceContext:
        return TraceContext(
            correlation_id=self.correlation_id,
            causation_id=self.causation_id,
            idempotency_id=self.idempotency_id,
        )


class StrategyEvaluatedEvent(EventEnvelope):
    event_type: Literal[EventType.STRATEGY_EVALUATED] = EventType.STRATEGY_EVALUATED
    strategy_run_id: StableId
    signal_ids: tuple[StableId, ...] = ()


class PositionSizedEvent(EventEnvelope):
    event_type: Literal[EventType.POSITION_SIZED] = EventType.POSITION_SIZED
    signal_id: StableId
    candidate_id: StableId | None = None


class RiskEvaluatedEvent(EventEnvelope):
    event_type: Literal[EventType.RISK_EVALUATED] = EventType.RISK_EVALUATED
    risk_decision_id: StableId
    risk_outcome: RiskOutcome
    candidate_id: StableId | None = None
    proposal_id: StableId | None = None


class ProposalCreatedEvent(EventEnvelope):
    event_type: Literal[EventType.PROPOSAL_CREATED] = EventType.PROPOSAL_CREATED
    proposal_id: StableId
    candidate_id: StableId
    risk_decision_id: StableId
    order_hash: Annotated[
        str, StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$", min_length=71, max_length=71)
    ]


class ApprovalRequestedEvent(EventEnvelope):
    event_type: Literal[EventType.APPROVAL_REQUESTED] = EventType.APPROVAL_REQUESTED
    challenge_id: StableId
    proposal_id: StableId


class ApprovalConsumedEvent(EventEnvelope):
    event_type: Literal[EventType.APPROVAL_CONSUMED] = EventType.APPROVAL_CONSUMED
    challenge_id: StableId
    proposal_id: StableId
    approval_event_id: StableId | None = None
    approved: bool


class OrderExecutedEvent(EventEnvelope):
    event_type: Literal[EventType.ORDER_EXECUTED] = EventType.ORDER_EXECUTED
    proposal_id: StableId
    client_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    broker_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)] | None = None


class OrderCancelledEvent(EventEnvelope):
    event_type: Literal[EventType.ORDER_CANCELLED] = EventType.ORDER_CANCELLED
    cancel_id: StableId
    proposal_id: StableId
    broker_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]


class ManualReviewResolvedEvent(EventEnvelope):
    event_type: Literal[EventType.MANUAL_REVIEW_RESOLVED] = EventType.MANUAL_REVIEW_RESOLVED
    subject_id: StableId
    resolution: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class ReconciledEvent(EventEnvelope):
    """Result stub for reconcile commands (full matching lives in P03-T15)."""

    event_type: Literal[EventType.RECONCILED] = EventType.RECONCILED
    reconciliation_id: StableId | None = None
    proposal_id: StableId | None = None
    broker_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)] | None = None
    cancel_id: StableId | None = None


class CommandRejectedEvent(EventEnvelope):
    """Generic rejection when a handler fails closed before producing a typed result."""

    event_type: Literal[EventType.COMMAND_REJECTED] = EventType.COMMAND_REJECTED
    command_type: CommandType
    subject_id: StableId | None = None

    @model_validator(mode="after")
    def _rejected_outcome(self) -> CommandRejectedEvent:
        if self.outcome is not CommandOutcome.REJECTED:
            raise ValueError("COMMAND_REJECTED events require outcome=REJECTED")
        if self.reason_code is None:
            raise ValueError("COMMAND_REJECTED events require reason_code")
        return self


WorkflowEvent = (
    StrategyEvaluatedEvent
    | PositionSizedEvent
    | RiskEvaluatedEvent
    | ProposalCreatedEvent
    | ApprovalRequestedEvent
    | ApprovalConsumedEvent
    | OrderExecutedEvent
    | OrderCancelledEvent
    | ManualReviewResolvedEvent
    | ReconciledEvent
    | CommandRejectedEvent
)


def allocate_event_id() -> str:
    """Public helper so callers share the same ID allocator as the package."""
    return new_event_id()


__all__ = [
    "COMMAND_TO_EVENT_TYPE",
    "ApprovalConsumedEvent",
    "ApprovalRequestedEvent",
    "CommandOutcome",
    "CommandRejectedEvent",
    "EventEnvelope",
    "EventType",
    "ManualReviewResolvedEvent",
    "OrderCancelledEvent",
    "OrderExecutedEvent",
    "PositionSizedEvent",
    "ProposalCreatedEvent",
    "ReconciledEvent",
    "RiskEvaluatedEvent",
    "StrategyEvaluatedEvent",
    "WorkflowEvent",
    "allocate_event_id",
]
