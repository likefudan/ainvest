"""Shared types and fixed identifiers for the paper orchestration loop (P03-T16)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from ainvest.audit.envelope import AuditEventEnvelope
from ainvest.execution.state_machine import OrderLifecycleState

# Weekday regular session (11:00 America/New_York) — aligned with risk fixtures.
DEFAULT_AS_OF = datetime(2026, 7, 23, 15, 0, 0, tzinfo=UTC)

# Fixed IDs so identical fixtures yield identical digests/decisions.
FIXED_CORRELATION_ID = "corr_01HZYD4APAPER0001"
FIXED_CANDIDATE_ID = "cand_01HZYD4APAPER0001"
FIXED_PROPOSAL_ID = "ordp_01HZYD4APAPER0001"
FIXED_PROPOSAL_RISK_ID = "risk_01HZYD4APROP00001"
FIXED_PRETRADE_RISK_ID = "risk_01HZYD4APRET00001"
FIXED_CHALLENGE_ID = "apch_01HZYD4APAPER0001"
FIXED_APPROVAL_EVENT_ID = "apev_01HZYD4APAPER0001"
FIXED_CLIENT_ORDER_ID = "client_ord_d4a_paper_1"
FIXED_RECONCILIATION_ID = "recon_01HZYD4APAPER0001"
FIXED_EXECUTE_IDEMPOTENCY_ID = "idem_01HZYD4AEXEC00001"
FIXED_RECONCILE_IDEMPOTENCY_ID = "idem_01HZYD4ARECON0001"
FIXED_EXECUTE_COMMAND_ID = "cmd_01HZYD4AEXEC000001"
FIXED_RECONCILE_COMMAND_ID = "cmd_01HZYD4ARECON00001"
FIXED_OPENING_CASH = Decimal("10000.00")
DEFAULT_APPROVAL_TTL = timedelta(seconds=120)
DEFAULT_CHALLENGE_NONCE_HASH = "a" * 64


class PaperFlowTerminal(StrEnum):
    """How the paper flow stopped."""

    APPROVAL_PENDING = "APPROVAL_PENDING"
    RISK_REJECTED = "RISK_REJECTED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    PRE_TRADE_REJECTED = "PRE_TRADE_REJECTED"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class StepRecord:
    """One audited orchestration step with digests for replay assertions."""

    name: str
    occurred_at: datetime
    lifecycle: OrderLifecycleState | None
    digests: dict[str, str] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PaperFlowResult:
    """Outcome of one deterministic paper-flow run."""

    terminal: PaperFlowTerminal
    lifecycle: OrderLifecycleState
    correlation_id: str
    steps: list[StepRecord]
    digests: dict[str, str]
    audit_events: list[AuditEventEnvelope] = field(default_factory=list)
    proposal_id: str | None = None
    order_hash: str | None = None
    challenge_id: str | None = None
    approval_event_id: str | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None
    fill_ids: tuple[str, ...] = ()
    filled_quantity: Decimal = Decimal("0")
    conservation_ok: bool | None = None
    error: str | None = None


__all__ = [
    "DEFAULT_APPROVAL_TTL",
    "DEFAULT_AS_OF",
    "DEFAULT_CHALLENGE_NONCE_HASH",
    "FIXED_APPROVAL_EVENT_ID",
    "FIXED_CANDIDATE_ID",
    "FIXED_CHALLENGE_ID",
    "FIXED_CLIENT_ORDER_ID",
    "FIXED_CORRELATION_ID",
    "FIXED_EXECUTE_COMMAND_ID",
    "FIXED_EXECUTE_IDEMPOTENCY_ID",
    "FIXED_OPENING_CASH",
    "FIXED_PRETRADE_RISK_ID",
    "FIXED_PROPOSAL_ID",
    "FIXED_PROPOSAL_RISK_ID",
    "FIXED_RECONCILE_COMMAND_ID",
    "FIXED_RECONCILE_IDEMPOTENCY_ID",
    "FIXED_RECONCILIATION_ID",
    "PaperFlowResult",
    "PaperFlowTerminal",
    "StepRecord",
]
