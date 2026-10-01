"""Funds-safety incident reducer and journal-first, provider-neutral delivery.

No production provider, operator authentication, journal adapter, or broker
action is implicitly enabled. Adapters must meet the incident runbook contract.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import Annotated, Literal, Protocol

from pydantic import Field, SecretBytes, SecretStr, StringConstraints, model_validator

from ainvest.schemas.common import DomainModel, UtcDateTime, ensure_utc

Reference = Annotated[str, StringConstraints(pattern=r"^ref_[a-f0-9]{64}$")]
PositiveCounter = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
Counter = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]


class AlertError(RuntimeError):
    """Bounded error code; adapter exception text must never cross this boundary."""


class AlertKind(StrEnum):
    SUBMIT_UNKNOWN = "submit_unknown"
    CANCEL_UNKNOWN = "cancel_unknown"
    ORDER_HASH_MISMATCH = "order_hash_mismatch"
    DUPLICATE_ORDER = "duplicate_order"
    ACCOUNT_MISMATCH = "account_mismatch"
    POSITION_MISMATCH = "position_mismatch"
    KILL_SWITCH = "kill_switch"
    UNEXPECTED_LIVE_START = "unexpected_live_start"


class AlertState(StrEnum):
    ACTIVE = "active"
    ESCALATED = "escalated"
    RECOVERED = "recovered"


class AlertChannel(StrEnum):
    INDEPENDENT = "independent"
    TELEGRAM = "telegram"


class OwnerRole(StrEnum):
    SAFETY_OPERATOR = "safety_operator"
    INCIDENT_COMMANDER = "incident_commander"


class NextAction(StrEnum):
    RECONCILE_SUBMISSION_NO_RESUBMIT = "reconcile_submission_no_resubmit"
    RECONCILE_CANCELLATION_NO_RETRY = "reconcile_cancellation_no_retry"
    QUARANTINE_APPROVAL = "quarantine_approval"
    RECONCILE_DUPLICATES_NO_RESUBMIT = "reconcile_duplicates_no_resubmit"
    RECONCILE_ACCOUNT = "reconcile_account"
    RECONCILE_POSITIONS = "reconcile_positions"
    KEEP_NEW_SUBMISSIONS_BLOCKED = "keep_new_submissions_blocked"
    ISOLATE_LIVE_COMPONENT = "isolate_live_component"
    REVIEW_RECOVERY_KEEP_TRADING_GATES = "review_recovery_keep_trading_gates"


_ACTIONS = {
    AlertKind.SUBMIT_UNKNOWN: NextAction.RECONCILE_SUBMISSION_NO_RESUBMIT,
    AlertKind.CANCEL_UNKNOWN: NextAction.RECONCILE_CANCELLATION_NO_RETRY,
    AlertKind.ORDER_HASH_MISMATCH: NextAction.QUARANTINE_APPROVAL,
    AlertKind.DUPLICATE_ORDER: NextAction.RECONCILE_DUPLICATES_NO_RESUBMIT,
    AlertKind.ACCOUNT_MISMATCH: NextAction.RECONCILE_ACCOUNT,
    AlertKind.POSITION_MISMATCH: NextAction.RECONCILE_POSITIONS,
    AlertKind.KILL_SWITCH: NextAction.KEEP_NEW_SUBMISSIONS_BLOCKED,
    AlertKind.UNEXPECTED_LIVE_START: NextAction.ISOLATE_LIVE_COMPONENT,
}


def redacted_reference(value: SecretStr, *, key: SecretBytes) -> str:
    """Keyed reference, not a guessable plain hash of an account/operator ID.

    Composition owns a stable dedicated secret key (at least 32 bytes), never a
    Telegram/broker token. Raw values and the key are not stored by this module.
    """
    material = key.get_secret_value()
    if len(material) < 32:
        raise AlertError("redaction_key_invalid")
    return (
        "ref_"
        + hmac.new(
            material,
            b"ainvest.funds-safety.v1\0" + value.get_secret_value().encode(),
            hashlib.sha256,
        ).hexdigest()
    )


def _reference(*parts: str) -> str:
    # Inputs here are already redacted references or closed enums/counters.
    return "ref_" + hashlib.sha256("\0".join(parts).encode()).hexdigest()


class FundsSafetyEvent(DomainModel):
    """Trusted producer event, never parsed from Telegram text or raw provider JSON.

    Sequence is monotonic per (environment, kind, subject), including recovery
    and reopening. Producers must retain this ordering across their restarts.
    """

    schema_version: Literal["1.0"] = "1.0"
    environment: Literal["test", "staging", "production"]
    kind: AlertKind
    subject_ref: Reference
    sequence: PositiveCounter
    observed_at: UtcDateTime
    state: AlertState
    evidence_ref: Reference | None = None

    @model_validator(mode="after")
    def _recovery_evidence(self) -> FundsSafetyEvent:
        if self.state is AlertState.RECOVERED and self.evidence_ref is None:
            raise ValueError("recovery_evidence_required")
        return self


class AlertPolicy(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    owner: OwnerRole
    escalation_owner: OwnerRole
    retry_seconds: Annotated[int, Field(strict=True, ge=1, le=3600)] = 30
    recovery_max_age_seconds: Annotated[int, Field(strict=True, ge=1, le=3600)] = 300
    capacity: Annotated[int, Field(strict=True, ge=1, le=10000)] = 1000


class AcknowledgeRequest(DomainModel):
    """A request to the injected operator authorization boundary, not identity proof."""

    schema_version: Literal["1.0"] = "1.0"
    incident_ref: Reference
    revision: PositiveCounter
    operator_ref: Reference
    reason: Literal["investigating"] = "investigating"


class Acknowledgement(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    request: AcknowledgeRequest
    acknowledged_at: UtcDateTime


class AlertIncident(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    incident_ref: Reference
    revision: PositiveCounter
    last_event: FundsSafetyEvent
    state: AlertState
    owner: OwnerRole
    acknowledgement: Acknowledgement | None = None


class AlertNotification(DomainModel):
    """Safe, fixed-field payload; no arbitrary message, exception, URL or account."""

    schema_version: Literal["1.0"] = "1.0"
    notification_ref: Reference
    incident_ref: Reference
    revision: PositiveCounter
    environment: Literal["test", "staging", "production"]
    channel: AlertChannel
    kind: AlertKind
    subject_ref: Reference
    state: AlertState
    previous_state: AlertState | None
    owner: OwnerRole
    severity: Literal["critical"] = "critical"
    observed_at: UtcDateTime
    next_action: NextAction
    evidence_ref: Reference | None


class DeliveryReceipt(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    accepted: Annotated[bool, Field(strict=True)]


class AlertDelivery(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    notification: AlertNotification
    attempts: Counter = 0
    due_at: UtcDateTime
    delivered: Annotated[bool, Field(strict=True)] = False
    superseded: Annotated[bool, Field(strict=True)] = False


class AlertCheckpoint(DomainModel):
    """Atomic incident/outbox checkpoint; journal must preserve append-only history."""

    schema_version: Literal["1.0"] = "1.0"
    version: Counter = 0
    policy: AlertPolicy
    channels: tuple[AlertChannel, ...]
    incidents: tuple[AlertIncident, ...] = ()
    deliveries: tuple[AlertDelivery, ...] = ()

    @model_validator(mode="after")
    def _integrity(self) -> AlertCheckpoint:
        if (
            len(set(self.channels)) != len(self.channels)
            or AlertChannel.INDEPENDENT not in self.channels
        ):
            raise ValueError("invalid_checkpoint_routes")
        incidents = {item.incident_ref: item for item in self.incidents}
        if len(incidents) != len(self.incidents):
            raise ValueError("duplicate_checkpoint_incident")
        seen: set[str] = set()
        for item in self.incidents:
            event = item.last_event
            if item.incident_ref != _reference(event.environment, event.kind, event.subject_ref):
                raise ValueError("invalid_checkpoint_identity")
            if item.state is not event.state and not (
                item.state is AlertState.ESCALATED and event.state is AlertState.ACTIVE
            ):
                raise ValueError("invalid_checkpoint_state")
            if item.acknowledgement is not None and (
                item.acknowledgement.request.incident_ref != item.incident_ref
                or item.acknowledgement.request.revision != item.revision
                or item.state is AlertState.RECOVERED
            ):
                raise ValueError("invalid_checkpoint_acknowledgement")
        for delivery in self.deliveries:
            notice = delivery.notification
            incident = incidents.get(notice.incident_ref)
            if (
                incident is None
                or notice.revision > incident.revision
                or notice.channel not in self.channels
                or notice.notification_ref in seen
                or notice.notification_ref
                != _reference(notice.incident_ref, str(notice.revision), notice.channel)
                or (
                    notice.revision < incident.revision
                    and not delivery.delivered
                    and not delivery.superseded
                )
            ):
                raise ValueError("invalid_checkpoint_delivery")
            seen.add(notice.notification_ref)
            if (
                notice.environment != incident.last_event.environment
                or notice.kind is not incident.last_event.kind
                or notice.subject_ref != incident.last_event.subject_ref
                or (notice.state is AlertState.RECOVERED and notice.evidence_ref is None)
                or (notice.revision == incident.revision and notice.state is not incident.state)
            ):
                raise ValueError("invalid_checkpoint_notice")
        for item in self.incidents:
            if any(
                _reference(item.incident_ref, str(item.revision), channel) not in seen
                for channel in self.channels
            ):
                raise ValueError("checkpoint_notice_missing")
        if (
            len(self.incidents) > self.policy.capacity
            or len(self.deliveries) > self.policy.capacity
        ):
            raise ValueError("checkpoint_capacity_exceeded")
        return self


class AlertPort(Protocol):
    def send(self, notification: AlertNotification) -> DeliveryReceipt:
        """Bounded-time delivery; deduplicate by notification_ref. Never log secrets."""
        ...


class AlertJournal(Protocol):
    """Production adapter must durably CAS commit plus append audit, atomically.

    None means an explicitly initialized empty journal, NOT an I/O error. Raise
    on unreadable/corrupt state. A service needs an exclusive writer lease for
    its complete lifetime, including delivery; CAS alone is not a send lease.
    """

    def load(self) -> AlertCheckpoint | None: ...

    def commit(self, checkpoint: AlertCheckpoint, *, expected_version: int) -> bool:
        """True only after durable commit; False on stale version; raise on failure."""
        ...


class AlertAuthorizer(Protocol):
    def authorize(self, request: AcknowledgeRequest, *, owner: OwnerRole) -> bool:
        """Authenticate independently and authorize this exact incident/revision/role."""
        ...


class AlertService:
    """Single-writer generic handlers; explicitly injected storage and delivery.

    Threads on one service are serialized. Ports must not call back into the
    service. No timer thread, network client, or in-memory production fallback
    is started. The scheduler calls dispatch_due with a bounded batch size.
    """

    def __init__(
        self,
        *,
        journal: AlertJournal,
        channels: Mapping[AlertChannel, AlertPort],
        policy: AlertPolicy,
        authorizer: AlertAuthorizer | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if AlertChannel.INDEPENDENT not in channels or any(
            type(k) is not AlertChannel for k in channels
        ):
            raise AlertError("independent_channel_required")
        self._policy = AlertPolicy.model_validate_json(policy.model_dump_json())
        self._channels = dict(channels)
        self._journal = journal
        self._authorizer = authorizer
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = Lock()
        self._poisoned = False
        routes = tuple(sorted(channels))
        try:
            stored = journal.load()
            checkpoint = (
                AlertCheckpoint.model_validate_json(stored.model_dump_json())
                if stored is not None
                else AlertCheckpoint(policy=policy, channels=routes)
            )
        except Exception:
            raise AlertError("journal_unavailable") from None
        if checkpoint.policy != policy or checkpoint.channels != routes:
            raise AlertError("journal_configuration_mismatch")
        self._checkpoint = checkpoint
        self._now()

    @property
    def policy(self) -> AlertPolicy:
        """Immutable policy; route/policy changes need an explicit journal migration."""
        return self._policy

    def _now(self) -> datetime:
        try:
            return ensure_utc(self._clock())
        except Exception:
            raise AlertError("clock_invalid") from None

    def _ready(self) -> None:
        if self._poisoned:
            raise AlertError("reload_required")

    def _commit(self, candidate: AlertCheckpoint) -> None:
        try:
            accepted = self._journal.commit(candidate, expected_version=self._checkpoint.version)
        except Exception:
            self._poisoned = True
            raise AlertError("journal_unavailable") from None
        if accepted is not True:
            self._poisoned = True
            raise AlertError("journal_conflict")
        self._checkpoint = candidate

    def snapshot(self) -> AlertCheckpoint:
        """Immutable redacted operational evidence, not trading readiness."""
        with self._lock:
            self._ready()
            return self._checkpoint

    def handle(self, event: FundsSafetyEvent) -> AlertIncident:
        """Persist a fault/recovery and its outbox before any external delivery."""
        try:
            event = FundsSafetyEvent.model_validate_json(event.model_dump_json())
        except Exception:
            raise AlertError("event_invalid") from None
        with self._lock:
            self._ready()
            now = self._now()
            if event.observed_at > now:
                raise AlertError("event_from_future")
            identity = _reference(event.environment, event.kind, event.subject_ref)
            previous = next(
                (i for i in self._checkpoint.incidents if i.incident_ref == identity), None
            )
            if previous is not None:
                if event.sequence < previous.last_event.sequence:
                    return previous
                if event.sequence == previous.last_event.sequence:
                    if event != previous.last_event:
                        raise AlertError("sequence_conflict")
                    return previous
                if event.observed_at < previous.last_event.observed_at:
                    raise AlertError("event_time_regressed")
            if event.state is AlertState.RECOVERED:
                if previous is None:
                    raise AlertError("recovery_without_incident")
                if now - event.observed_at > timedelta(
                    seconds=self.policy.recovery_max_age_seconds
                ):
                    raise AlertError("recovery_evidence_stale")
            state = event.state
            if (
                previous is not None
                and previous.state is AlertState.ESCALATED
                and state is AlertState.ACTIVE
            ):
                state = AlertState.ESCALATED
            changed = previous is None or previous.state is not state
            owner = (
                self.policy.escalation_owner
                if state is AlertState.ESCALATED
                else previous.owner
                if state is AlertState.RECOVERED and previous is not None
                else self.policy.owner
            )
            incident = AlertIncident(
                incident_ref=identity,
                revision=(previous.revision + int(changed)) if previous else 1,
                last_event=event,
                state=state,
                owner=owner,
                acknowledgement=previous.acknowledgement if previous and not changed else None,
            )
            incidents = (
                *(i for i in self._checkpoint.incidents if i.incident_ref != identity),
                incident,
            )
            deliveries = self._checkpoint.deliveries
            if changed:
                # Keep history but supersede unsent older states, never send an old
                # ACTIVE after a delivered ESCALATED/RECOVERED notification.
                deliveries = tuple(
                    d.model_copy(update={"superseded": True})
                    if d.notification.incident_ref == identity and not d.delivered
                    else d
                    for d in deliveries
                )
                for channel in self._checkpoint.channels:
                    notification = AlertNotification(
                        notification_ref=_reference(identity, str(incident.revision), channel),
                        incident_ref=identity,
                        revision=incident.revision,
                        environment=event.environment,
                        channel=channel,
                        kind=event.kind,
                        subject_ref=event.subject_ref,
                        state=state,
                        previous_state=previous.state if previous else None,
                        owner=owner,
                        observed_at=event.observed_at,
                        next_action=NextAction.REVIEW_RECOVERY_KEEP_TRADING_GATES
                        if state is AlertState.RECOVERED
                        else _ACTIONS[event.kind],
                        evidence_ref=event.evidence_ref,
                    )
                    deliveries += (AlertDelivery(notification=notification, due_at=now),)
            if len(incidents) > self.policy.capacity or len(deliveries) > self.policy.capacity:
                raise AlertError("journal_capacity_exceeded")
            self._commit(
                AlertCheckpoint(
                    version=self._checkpoint.version + 1,
                    policy=self.policy,
                    channels=self._checkpoint.channels,
                    incidents=incidents,
                    deliveries=deliveries,
                )
            )
            return incident

    def acknowledge(self, request: AcknowledgeRequest) -> AlertIncident:
        """Record investigation, never resolve risk or authorize a broker action."""
        try:
            request = AcknowledgeRequest.model_validate_json(request.model_dump_json())
        except Exception:
            raise AlertError("acknowledgement_invalid") from None
        with self._lock:
            self._ready()
            incident = next(
                (i for i in self._checkpoint.incidents if i.incident_ref == request.incident_ref),
                None,
            )
            if incident is None:
                raise AlertError("incident_missing")
            if request.revision != incident.revision:
                raise AlertError("revision_conflict")
            if incident.state is AlertState.RECOVERED:
                raise AlertError("incident_recovered")
            try:
                allowed = (
                    self._authorizer is not None
                    and self._authorizer.authorize(request, owner=incident.owner) is True
                )
            except Exception:
                raise AlertError("acknowledgement_denied") from None
            if not allowed:
                raise AlertError("acknowledgement_denied")
            if incident.acknowledgement is not None:
                if incident.acknowledgement.request != request:
                    raise AlertError("acknowledgement_conflict")
                return incident
            updated = incident.model_copy(
                update={
                    "acknowledgement": Acknowledgement(request=request, acknowledged_at=self._now())
                }
            )
            self._commit(
                self._checkpoint.model_copy(
                    update={
                        "version": self._checkpoint.version + 1,
                        "incidents": tuple(
                            updated if i == incident else i for i in self._checkpoint.incidents
                        ),
                    }
                )
            )
            return updated

    def dispatch_due(self, *, batch_size: int = 16) -> int:
        """Try at most batch_size due deliveries; failures retry with capped backoff.

        Transport acceptance is not human acknowledgement. A crash after send
        but before receipt commit is at-least-once, using the same delivery key.
        """
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise AlertError("batch_size_invalid")
        with self._lock:
            self._ready()
            now = self._now()
            due = [
                d
                for d in self._checkpoint.deliveries
                if not d.delivered and not d.superseded and d.due_at <= now
            ][:batch_size]
            accepted = 0
            for delivery in due:
                # Persist the attempt/cooldown first. On uncertain receipt, a
                # restarted worker cannot immediately storm the provider.
                delay = min(3600, self.policy.retry_seconds * 2 ** min(delivery.attempts, 10))
                attempted = delivery.model_copy(
                    update={
                        "attempts": delivery.attempts + 1,
                        "due_at": now + timedelta(seconds=delay),
                    }
                )
                self._replace_delivery(delivery, attempted)
                try:
                    receipt = self._channels[delivery.notification.channel].send(
                        delivery.notification
                    )
                    success = type(receipt) is DeliveryReceipt and receipt.accepted is True
                except Exception:
                    success = False
                if success:
                    self._replace_delivery(
                        attempted, attempted.model_copy(update={"delivered": True})
                    )
                    accepted += 1
            return accepted

    def _replace_delivery(self, old: AlertDelivery, new: AlertDelivery) -> None:
        self._commit(
            self._checkpoint.model_copy(
                update={
                    "version": self._checkpoint.version + 1,
                    "deliveries": tuple(
                        new if d == old else d for d in self._checkpoint.deliveries
                    ),
                }
            )
        )
