"""Append-only audit event envelope (P02-T8).

The envelope is a Pydantic domain object. Persistence uses audit repositories
via :class:`~ainvest.audit.service.AuditService` — this module never imports ORM.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, StringConstraints

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    DomainModel,
    SchemaVersion,
    UtcDateTime,
)

EventId = Annotated[str, StringConstraints(min_length=8, max_length=160)]
CorrelationId = Annotated[str, StringConstraints(min_length=1, max_length=128)]
ActorId = Annotated[str, StringConstraints(min_length=1, max_length=128)]
DigestHex = Annotated[
    str,
    StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$", min_length=71, max_length=71),
]

# Soft limit for in-row JSON payloads. Larger objects store digests only.
MAX_AUDIT_PAYLOAD_BYTES: int = 16_384


class ActorType(StrEnum):
    """Who triggered the audited action."""

    SYSTEM = "system"
    MODEL = "model"
    STRATEGY = "strategy"
    USER = "user"
    OPERATOR = "operator"


class AuditEventType(StrEnum):
    """Stable audit event type codes for critical control-flow transitions."""

    PROPOSAL_CREATED = "PROPOSAL_CREATED"
    PROPOSAL_STATUS_CHANGED = "PROPOSAL_STATUS_CHANGED"
    APPROVAL_CHALLENGE_CREATED = "APPROVAL_CHALLENGE_CREATED"
    APPROVAL_CONSUMED = "APPROVAL_CONSUMED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    BROKER_ORDER_CREATED = "BROKER_ORDER_CREATED"
    BROKER_ORDER_STATUS_CHANGED = "BROKER_ORDER_STATUS_CHANGED"
    BROKER_FILL_RECORDED = "BROKER_FILL_RECORDED"
    RISK_DECISION = "RISK_DECISION"
    OPERATOR_ACTION = "OPERATOR_ACTION"
    ERROR = "ERROR"
    GENERIC = "GENERIC"


class AuditEventEnvelope(DomainModel):
    """Append-only audit envelope (design.md §9).

    Sensitive fields must be redacted before persistence. Large external objects
    should be represented by digests rather than inlined payloads.
    """

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    event_id: EventId
    event_type: AuditEventType | str
    occurred_at: UtcDateTime
    correlation_id: CorrelationId | None = None
    causation_id: CorrelationId | None = None
    actor_type: ActorType
    actor_id: ActorId
    subject_type: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    subject_id: Annotated[str, StringConstraints(min_length=1, max_length=160)] | None = None
    code_version: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    config_version: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    input_digest: DigestHex | None = None
    output_digest: DigestHex | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    error_code: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    error_detail: Annotated[str, StringConstraints(min_length=1, max_length=2048)] | None = None
    retry_count: Annotated[int, Field(ge=0)] = 0
    payload: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "MAX_AUDIT_PAYLOAD_BYTES",
    "ActorType",
    "AuditEventEnvelope",
    "AuditEventType",
    "CorrelationId",
    "DigestHex",
    "EventId",
]
