"""Append-only audit service API (P02-T8).

Business code receives only append / list operations. There is no update or
delete API for audit events. This module must not import ``ainvest.db.models``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from ainvest.audit.digests import digest_json, utf8_size
from ainvest.audit.envelope import (
    MAX_AUDIT_PAYLOAD_BYTES,
    ActorType,
    AuditEventEnvelope,
    AuditEventType,
)
from ainvest.audit.redact import redact
from ainvest.db.base import utc_now
from ainvest.db.repositories import AuditRepository
from ainvest.db.uow import UnitOfWork


def _as_event_type(value: AuditEventType | str) -> str:
    if isinstance(value, AuditEventType):
        return value.value
    return str(value)


def _truncate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool, str | None]:
    """Return ``(payload_or_stub, truncated, digest)`` when over size limit."""
    size = utf8_size(payload)
    if size <= MAX_AUDIT_PAYLOAD_BYTES:
        return payload, False, None
    digest = digest_json(payload)
    stub = {
        "truncated": True,
        "original_size_bytes": size,
        "digest": digest,
    }
    return stub, True, digest


def envelope_to_fields(
    envelope: AuditEventEnvelope,
    *,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    """Convert a redacted envelope into column fields for the audit repository."""
    safe_payload = redact(dict(envelope.payload))
    if not isinstance(safe_payload, dict):
        safe_payload = {"value": safe_payload}

    safe_before = redact(envelope.before_state) if envelope.before_state is not None else None
    safe_after = redact(envelope.after_state) if envelope.after_state is not None else None
    if safe_before is not None and not isinstance(safe_before, dict):
        safe_before = {"value": safe_before}
    if safe_after is not None and not isinstance(safe_after, dict):
        safe_after = {"value": safe_after}

    payload, truncated, overflow_digest = _truncate_payload(safe_payload)
    input_digest = envelope.input_digest
    output_digest = envelope.output_digest or overflow_digest

    return {
        "event_id": envelope.event_id,
        "event_type": _as_event_type(envelope.event_type),
        "occurred_at": envelope.occurred_at,
        "recorded_at": recorded_at or utc_now(),
        "correlation_id": envelope.correlation_id,
        "causation_id": envelope.causation_id,
        "actor_type": (
            envelope.actor_type.value
            if isinstance(envelope.actor_type, ActorType)
            else str(envelope.actor_type)
        ),
        "actor_id": envelope.actor_id,
        "subject_type": envelope.subject_type,
        "subject_id": envelope.subject_id,
        "schema_version": envelope.schema_version,
        "code_version": envelope.code_version,
        "config_version": envelope.config_version,
        "input_digest": input_digest,
        "output_digest": output_digest,
        "before_state": safe_before,
        "after_state": safe_after,
        "error_code": envelope.error_code,
        "error_detail": (None if envelope.error_detail is None else redact(envelope.error_detail)),
        "retry_count": envelope.retry_count,
        "payload_truncated": truncated,
        "payload_json": payload,
    }


class AuditService:
    """Append-only audit API bound to a Unit of Work or repository."""

    def __init__(self, audit_repo: AuditRepository) -> None:
        self._repo = audit_repo

    @classmethod
    def from_uow(cls, uow: UnitOfWork) -> AuditService:
        return cls(uow.audit_repo)

    def append(self, envelope: AuditEventEnvelope) -> Any:
        """Persist a new audit event. No update/delete is exposed."""
        return self._repo.append_fields(envelope_to_fields(envelope))

    def append_idempotent(self, envelope: AuditEventEnvelope) -> tuple[Any, bool]:
        """Append; on duplicate ``event_id``, return the existing row."""
        return self._repo.append_fields_idempotent(
            envelope_to_fields(envelope),
            event_id=envelope.event_id,
        )

    def list_for_subject(self, subject_type: str, subject_id: str) -> Sequence[Any]:
        return self._repo.list_for_subject(subject_type, subject_id)

    def list_by_correlation(self, correlation_id: str) -> Sequence[Any]:
        return self._repo.list_by_correlation(correlation_id)

    def timeline_for_proposal(self, proposal_id: str) -> Sequence[Any]:
        """Reconstruct a proposal's audit timeline by subject id."""
        return self.list_for_subject("order_proposal", proposal_id)


def record_state_change(
    audit: AuditService,
    *,
    event_id: str,
    event_type: AuditEventType | str,
    actor_type: ActorType,
    actor_id: str,
    subject_type: str,
    subject_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    code_version: str | None = None,
    config_version: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> Any:
    """Helper for critical state changes that must produce an audit event."""
    envelope = AuditEventEnvelope(
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at or datetime.now(UTC),
        correlation_id=correlation_id,
        causation_id=causation_id,
        actor_type=actor_type,
        actor_id=actor_id,
        subject_type=subject_type,
        subject_id=subject_id,
        code_version=code_version,
        config_version=config_version,
        before_state=before,
        after_state=after,
        payload=payload or {},
    )
    return audit.append(envelope)


__all__ = [
    "AuditService",
    "envelope_to_fields",
    "record_state_change",
]
