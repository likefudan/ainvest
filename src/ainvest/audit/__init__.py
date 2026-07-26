"""Append-only audit events for replayable control-flow transitions.

This package redacts secrets, digests large payloads, and appends audit
envelopes via repositories. It must not import execution, approval, agents,
strategies, or risk packages. ORM models are accessed only through
``ainvest.db.repositories`` / UnitOfWork — never imported here.
"""

from __future__ import annotations

from ainvest.audit.digests import digest_json, sha256_digest
from ainvest.audit.envelope import (
    MAX_AUDIT_PAYLOAD_BYTES,
    ActorType,
    AuditEventEnvelope,
    AuditEventType,
)
from ainvest.audit.redact import REDACTED, assert_no_plaintext_secrets, redact
from ainvest.audit.service import AuditService, record_state_change

__all__ = [
    "MAX_AUDIT_PAYLOAD_BYTES",
    "REDACTED",
    "ActorType",
    "AuditEventEnvelope",
    "AuditEventType",
    "AuditService",
    "assert_no_plaintext_secrets",
    "digest_json",
    "record_state_change",
    "redact",
    "sha256_digest",
]
