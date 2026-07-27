"""Order and cancel command state machines (P02-T9).

Implements design.md §8 exactly: order-lifecycle edges, a separate correlated
cancel command machine, expected-current-state CAS, and atomic persist+audit
via an injected persistence port.

Hard rules:

- Terminal states cannot transition.
- ``SUBMIT_UNKNOWN`` may only move to ``RECONCILING`` (never another broker write).
- ``CANCEL_UNKNOWN`` may only move to ``CANCEL_RECONCILING`` (never re-cancel).
- Duplicate transitions to the current state are idempotent.
- Stale workers that present the wrong ``expected_current`` fail closed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal, Protocol, runtime_checkable

from ainvest.audit.envelope import ActorType, AuditEventType
from ainvest.audit.service import AuditService, record_state_change

MachineKind = Literal["order", "cancel"]


class OrderLifecycleState(StrEnum):
    """Order workflow states from design.md §8."""

    SIGNAL_CREATED = "SIGNAL_CREATED"
    RISK_REJECTED = "RISK_REJECTED"
    PROPOSAL_CREATED = "PROPOSAL_CREATED"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVED = "APPROVED"
    PRE_TRADE_REJECTED = "PRE_TRADE_REJECTED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    RECONCILING = "RECONCILING"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class CancelCommandState(StrEnum):
    """Independent cancel-command states (correlated to an order)."""

    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_CONFIRMED = "CANCEL_CONFIRMED"
    CANCEL_REJECTED = "CANCEL_REJECTED"
    CANCEL_UNKNOWN = "CANCEL_UNKNOWN"
    CANCEL_RECONCILING = "CANCEL_RECONCILING"
    CANCEL_NOT_APPLIED = "CANCEL_NOT_APPLIED"
    CANCEL_MANUAL_REVIEW = "CANCEL_MANUAL_REVIEW"


# Exact edges from design.md §8 mermaid diagrams.
ORDER_EDGES: Final[frozenset[tuple[OrderLifecycleState, OrderLifecycleState]]] = frozenset(
    {
        (OrderLifecycleState.SIGNAL_CREATED, OrderLifecycleState.RISK_REJECTED),
        (OrderLifecycleState.SIGNAL_CREATED, OrderLifecycleState.PROPOSAL_CREATED),
        (OrderLifecycleState.PROPOSAL_CREATED, OrderLifecycleState.APPROVAL_PENDING),
        (OrderLifecycleState.APPROVAL_PENDING, OrderLifecycleState.APPROVAL_REJECTED),
        (OrderLifecycleState.APPROVAL_PENDING, OrderLifecycleState.APPROVAL_EXPIRED),
        (OrderLifecycleState.APPROVAL_PENDING, OrderLifecycleState.APPROVED),
        (OrderLifecycleState.APPROVED, OrderLifecycleState.PRE_TRADE_REJECTED),
        (OrderLifecycleState.APPROVED, OrderLifecycleState.SUBMITTING),
        (OrderLifecycleState.SUBMITTING, OrderLifecycleState.SUBMITTED),
        (OrderLifecycleState.SUBMITTING, OrderLifecycleState.SUBMIT_UNKNOWN),
        (OrderLifecycleState.SUBMITTED, OrderLifecycleState.PARTIALLY_FILLED),
        (OrderLifecycleState.SUBMITTED, OrderLifecycleState.FILLED),
        (OrderLifecycleState.SUBMITTED, OrderLifecycleState.CANCELLED),
        (OrderLifecycleState.SUBMITTED, OrderLifecycleState.REJECTED),
        (OrderLifecycleState.PARTIALLY_FILLED, OrderLifecycleState.FILLED),
        (OrderLifecycleState.PARTIALLY_FILLED, OrderLifecycleState.CANCELLED),
        (OrderLifecycleState.SUBMIT_UNKNOWN, OrderLifecycleState.RECONCILING),
        (OrderLifecycleState.RECONCILING, OrderLifecycleState.SUBMITTED),
        (OrderLifecycleState.RECONCILING, OrderLifecycleState.MANUAL_REVIEW),
    }
)

CANCEL_EDGES: Final[frozenset[tuple[CancelCommandState, CancelCommandState]]] = frozenset(
    {
        (CancelCommandState.CANCEL_REQUESTED, CancelCommandState.CANCEL_CONFIRMED),
        (CancelCommandState.CANCEL_REQUESTED, CancelCommandState.CANCEL_REJECTED),
        (CancelCommandState.CANCEL_REQUESTED, CancelCommandState.CANCEL_UNKNOWN),
        (CancelCommandState.CANCEL_UNKNOWN, CancelCommandState.CANCEL_RECONCILING),
        (CancelCommandState.CANCEL_RECONCILING, CancelCommandState.CANCEL_CONFIRMED),
        (CancelCommandState.CANCEL_RECONCILING, CancelCommandState.CANCEL_NOT_APPLIED),
        (CancelCommandState.CANCEL_RECONCILING, CancelCommandState.CANCEL_MANUAL_REVIEW),
    }
)

ORDER_TERMINAL: Final[frozenset[OrderLifecycleState]] = frozenset(
    {
        OrderLifecycleState.RISK_REJECTED,
        OrderLifecycleState.APPROVAL_REJECTED,
        OrderLifecycleState.APPROVAL_EXPIRED,
        OrderLifecycleState.PRE_TRADE_REJECTED,
        OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCELLED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.MANUAL_REVIEW,
    }
)

CANCEL_TERMINAL: Final[frozenset[CancelCommandState]] = frozenset(
    {
        CancelCommandState.CANCEL_CONFIRMED,
        CancelCommandState.CANCEL_REJECTED,
        CancelCommandState.CANCEL_NOT_APPLIED,
        CancelCommandState.CANCEL_MANUAL_REVIEW,
    }
)

# Ambiguous broker writes may only enter reconciliation — never another write.
ORDER_RECOVERY_ONLY: Final[Mapping[OrderLifecycleState, frozenset[OrderLifecycleState]]] = {
    OrderLifecycleState.SUBMIT_UNKNOWN: frozenset({OrderLifecycleState.RECONCILING}),
}
CANCEL_RECOVERY_ONLY: Final[Mapping[CancelCommandState, frozenset[CancelCommandState]]] = {
    CancelCommandState.CANCEL_UNKNOWN: frozenset({CancelCommandState.CANCEL_RECONCILING}),
}


class IllegalTransitionError(ValueError):
    """Raised when a transition is not an allowed edge."""


class StaleStateError(ValueError):
    """Raised when ``expected_current`` does not match the live state."""


class PersistenceError(RuntimeError):
    """Raised when atomic persist+audit fails (transition must not apply)."""


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Outcome of a state transition attempt."""

    machine: MachineKind
    before: str
    after: str
    event_id: str
    idempotent: bool
    correlation_id: str | None = None
    causation_id: str | None = None


@runtime_checkable
class StatePersistencePort(Protocol):
    """Atomic business-state + audit persistence for one transition.

    Implementations must apply the business state change and append the audit
    event in one atomic unit. On failure, neither side may remain applied.
    """

    def persist_transition(
        self,
        *,
        machine: MachineKind,
        subject_id: str,
        before: str,
        after: str,
        event_id: str,
        correlation_id: str | None,
        causation_id: str | None,
        actor_type: ActorType,
        actor_id: str,
        payload: Mapping[str, str] | None,
        occurred_at: datetime,
    ) -> None: ...

    def has_event(self, event_id: str) -> bool:
        """Return True when ``event_id`` was already persisted (idempotency)."""
        ...


class InMemoryStatePersistence:
    """Test double: records transitions and enforces event_id uniqueness."""

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []
        self._events: set[str] = set()
        self.fail_next: bool = False

    def has_event(self, event_id: str) -> bool:
        return event_id in self._events

    def persist_transition(
        self,
        *,
        machine: MachineKind,
        subject_id: str,
        before: str,
        after: str,
        event_id: str,
        correlation_id: str | None,
        causation_id: str | None,
        actor_type: ActorType,
        actor_id: str,
        payload: Mapping[str, str] | None,
        occurred_at: datetime,
    ) -> None:
        if self.fail_next:
            self.fail_next = False
            raise PersistenceError("simulated persistence failure")
        if event_id in self._events:
            return
        self._events.add(event_id)
        self.records.append(
            {
                "machine": machine,
                "subject_id": subject_id,
                "before": before,
                "after": after,
                "event_id": event_id,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "payload": dict(payload or {}),
                "occurred_at": occurred_at,
            }
        )


class AuditBackedStatePersistence:
    """Persist via a single atomic commit callback + audit helpers.

    ``commit`` MUST apply the business state change and append the audit event
    in one atomic unit (typically a Unit of Work). This class only tracks
    ``event_id`` idempotency after ``commit`` returns successfully.
    """

    def __init__(
        self,
        *,
        commit: Callable[..., None],
        seen_event_ids: set[str] | None = None,
    ) -> None:
        self._commit = commit
        self._seen = seen_event_ids if seen_event_ids is not None else set()

    @classmethod
    def with_audit_service(
        cls,
        audit: AuditService,
        *,
        apply_business_state: Callable[[MachineKind, str, str, str], None],
        atomic: Callable[[Callable[[], None]], None] | None = None,
        seen_event_ids: set[str] | None = None,
    ) -> AuditBackedStatePersistence:
        """Build a port that audits through ``record_state_change``.

        ``atomic`` wraps the combined business+audit work. Default runs the
        unit directly (tests / single-process). Production must pass a UoW
        wrapper so a mid-flight failure rolls both sides back.
        """
        run_atomic = atomic or (lambda work: work())

        def commit(
            *,
            machine: MachineKind,
            subject_id: str,
            before: str,
            after: str,
            event_id: str,
            correlation_id: str | None,
            causation_id: str | None,
            actor_type: ActorType,
            actor_id: str,
            payload: Mapping[str, str] | None,
            occurred_at: datetime,
        ) -> None:
            subject_type = "order_lifecycle" if machine == "order" else "cancel_command"
            event_type = (
                AuditEventType.PROPOSAL_STATUS_CHANGED
                if machine == "order"
                else AuditEventType.BROKER_ORDER_STATUS_CHANGED
            )

            def unit() -> None:
                apply_business_state(machine, subject_id, before, after)
                record_state_change(
                    audit,
                    event_id=event_id,
                    event_type=event_type,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    before={"state": before},
                    after={"state": after},
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                    payload=dict(payload or {}),
                    occurred_at=occurred_at,
                )

            run_atomic(unit)

        return cls(commit=commit, seen_event_ids=seen_event_ids)

    def has_event(self, event_id: str) -> bool:
        return event_id in self._seen

    def persist_transition(
        self,
        *,
        machine: MachineKind,
        subject_id: str,
        before: str,
        after: str,
        event_id: str,
        correlation_id: str | None,
        causation_id: str | None,
        actor_type: ActorType,
        actor_id: str,
        payload: Mapping[str, str] | None,
        occurred_at: datetime,
    ) -> None:
        if event_id in self._seen:
            return
        self._commit(
            machine=machine,
            subject_id=subject_id,
            before=before,
            after=after,
            event_id=event_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            actor_type=actor_type,
            actor_id=actor_id,
            payload=payload,
            occurred_at=occurred_at,
        )
        self._seen.add(event_id)


def is_order_transition_allowed(current: OrderLifecycleState, target: OrderLifecycleState) -> bool:
    if current in ORDER_TERMINAL:
        return False
    allowed_recovery = ORDER_RECOVERY_ONLY.get(current)
    if allowed_recovery is not None and target not in allowed_recovery:
        return False
    return (current, target) in ORDER_EDGES


def is_cancel_transition_allowed(current: CancelCommandState, target: CancelCommandState) -> bool:
    if current in CANCEL_TERMINAL:
        return False
    allowed_recovery = CANCEL_RECOVERY_ONLY.get(current)
    if allowed_recovery is not None and target not in allowed_recovery:
        return False
    return (current, target) in CANCEL_EDGES


def legal_order_targets(current: OrderLifecycleState) -> frozenset[OrderLifecycleState]:
    if current in ORDER_TERMINAL:
        return frozenset()
    return frozenset(target for source, target in ORDER_EDGES if source is current)


def legal_cancel_targets(current: CancelCommandState) -> frozenset[CancelCommandState]:
    if current in CANCEL_TERMINAL:
        return frozenset()
    return frozenset(target for source, target in CANCEL_EDGES if source is current)


def transition_order(
    *,
    current: OrderLifecycleState,
    expected_current: OrderLifecycleState,
    target: OrderLifecycleState,
    subject_id: str,
    event_id: str,
    persistence: StatePersistencePort,
    actor_type: ActorType = ActorType.SYSTEM,
    actor_id: str = "state_machine",
    correlation_id: str | None = None,
    causation_id: str | None = None,
    payload: Mapping[str, str] | None = None,
    occurred_at: datetime | None = None,
) -> TransitionResult:
    """Apply one order-lifecycle transition with CAS + atomic persist."""
    return _transition(
        machine="order",
        current=current.value,
        expected_current=expected_current.value,
        target=target.value,
        subject_id=subject_id,
        event_id=event_id,
        persistence=persistence,
        actor_type=actor_type,
        actor_id=actor_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
        occurred_at=occurred_at,
        allow=lambda src, dst: is_order_transition_allowed(
            OrderLifecycleState(src), OrderLifecycleState(dst)
        ),
    )


def transition_cancel(
    *,
    current: CancelCommandState,
    expected_current: CancelCommandState,
    target: CancelCommandState,
    subject_id: str,
    event_id: str,
    persistence: StatePersistencePort,
    actor_type: ActorType = ActorType.SYSTEM,
    actor_id: str = "state_machine",
    correlation_id: str | None = None,
    causation_id: str | None = None,
    payload: Mapping[str, str] | None = None,
    occurred_at: datetime | None = None,
) -> TransitionResult:
    """Apply one cancel-command transition with CAS + atomic persist."""
    return _transition(
        machine="cancel",
        current=current.value,
        expected_current=expected_current.value,
        target=target.value,
        subject_id=subject_id,
        event_id=event_id,
        persistence=persistence,
        actor_type=actor_type,
        actor_id=actor_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
        occurred_at=occurred_at,
        allow=lambda src, dst: is_cancel_transition_allowed(
            CancelCommandState(src), CancelCommandState(dst)
        ),
    )


def _transition(
    *,
    machine: MachineKind,
    current: str,
    expected_current: str,
    target: str,
    subject_id: str,
    event_id: str,
    persistence: StatePersistencePort,
    actor_type: ActorType,
    actor_id: str,
    correlation_id: str | None,
    causation_id: str | None,
    payload: Mapping[str, str] | None,
    occurred_at: datetime | None,
    allow: Callable[[str, str], bool],
) -> TransitionResult:
    if not subject_id.strip():
        raise ValueError("subject_id is required")
    if not event_id.strip():
        raise ValueError("event_id is required")

    # Already-applied event: idempotent regardless of presented current.
    if persistence.has_event(event_id):
        return TransitionResult(
            machine=machine,
            before=current,
            after=current,
            event_id=event_id,
            idempotent=True,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    if current != expected_current:
        raise StaleStateError(f"stale transition: live={current!r} expected={expected_current!r}")

    clock = occurred_at or datetime.now(UTC)
    if clock.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware UTC")

    # Already in target: still bind event_id so it cannot be reused later.
    if current == target:
        try:
            persistence.persist_transition(
                machine=machine,
                subject_id=subject_id,
                before=current,
                after=current,
                event_id=event_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
                actor_type=actor_type,
                actor_id=actor_id,
                payload=payload,
                occurred_at=clock,
            )
        except PersistenceError:
            raise
        except Exception as exc:
            raise PersistenceError(str(exc)) from exc
        return TransitionResult(
            machine=machine,
            before=current,
            after=current,
            event_id=event_id,
            idempotent=True,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    if not allow(current, target):
        raise IllegalTransitionError(f"illegal {machine} transition: {current!r} -> {target!r}")

    try:
        persistence.persist_transition(
            machine=machine,
            subject_id=subject_id,
            before=current,
            after=target,
            event_id=event_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            actor_type=actor_type,
            actor_id=actor_id,
            payload=payload,
            occurred_at=clock,
        )
    except PersistenceError:
        raise
    except Exception as exc:
        raise PersistenceError(str(exc)) from exc

    return TransitionResult(
        machine=machine,
        before=current,
        after=target,
        event_id=event_id,
        idempotent=False,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


__all__ = [
    "CANCEL_EDGES",
    "CANCEL_RECOVERY_ONLY",
    "CANCEL_TERMINAL",
    "ORDER_EDGES",
    "ORDER_RECOVERY_ONLY",
    "ORDER_TERMINAL",
    "AuditBackedStatePersistence",
    "CancelCommandState",
    "IllegalTransitionError",
    "InMemoryStatePersistence",
    "OrderLifecycleState",
    "PersistenceError",
    "StaleStateError",
    "StatePersistencePort",
    "TransitionResult",
    "is_cancel_transition_allowed",
    "is_order_transition_allowed",
    "legal_cancel_targets",
    "legal_order_targets",
    "transition_cancel",
    "transition_order",
]
