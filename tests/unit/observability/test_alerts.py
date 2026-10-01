"""Offline funds-safety fault matrix; no provider or broker credentials."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretBytes, SecretStr

from ainvest.observability.alerts import (
    AcknowledgeRequest,
    AlertChannel,
    AlertCheckpoint,
    AlertError,
    AlertKind,
    AlertNotification,
    AlertPolicy,
    AlertService,
    AlertState,
    DeliveryReceipt,
    FundsSafetyEvent,
    OwnerRole,
    redacted_reference,
)

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
REF = "ref_" + "a" * 64
EVIDENCE = "ref_" + "b" * 64
ACTOR = "ref_" + "c" * 64
pytestmark = pytest.mark.unit


class Journal:
    def __init__(self) -> None:
        self.state: AlertCheckpoint | None = None
        self.history: list[AlertCheckpoint] = []
        self.fail = False

    def load(self) -> AlertCheckpoint | None:
        return self.state

    def commit(self, checkpoint: AlertCheckpoint, *, expected_version: int) -> bool:
        if self.fail:
            raise RuntimeError("synthetic-private-provider-detail")
        if expected_version != (self.state.version if self.state else 0):
            return False
        self.state = checkpoint
        self.history.append(checkpoint)
        return True


class Channel:
    def __init__(self) -> None:
        self.messages: list[AlertNotification] = []
        self.fail = False

    def send(self, notification: AlertNotification) -> DeliveryReceipt:
        self.messages.append(notification)
        if self.fail:
            raise RuntimeError("synthetic-private-provider-detail")
        return DeliveryReceipt(accepted=True)


class Authorizer:
    allowed = True

    def authorize(self, request: AcknowledgeRequest, *, owner: OwnerRole) -> bool:
        return self.allowed


def event(
    kind: AlertKind = AlertKind.SUBMIT_UNKNOWN,
    state: AlertState = AlertState.ACTIVE,
    sequence: int = 1,
) -> FundsSafetyEvent:
    return FundsSafetyEvent(
        environment="staging",
        kind=kind,
        subject_ref=REF,
        state=state,
        sequence=sequence,
        observed_at=NOW,
        evidence_ref=EVIDENCE if state is AlertState.RECOVERED else None,
    )


def service(
    journal: Journal | None = None,
    channel: Channel | None = None,
) -> AlertService:
    return AlertService(
        journal=journal or Journal(),
        channels={AlertChannel.INDEPENDENT: channel or Channel()},
        policy=AlertPolicy(
            owner=OwnerRole.SAFETY_OPERATOR, escalation_owner=OwnerRole.INCIDENT_COMMANDER
        ),
        authorizer=Authorizer(),
        clock=lambda: NOW,
    )


@pytest.mark.parametrize("kind", list(AlertKind))
def test_fault_then_verified_recovery(kind: AlertKind) -> None:
    channel = Channel()
    engine = service(channel=channel)
    incident = engine.handle(event(kind))
    assert incident.state is AlertState.ACTIVE
    assert engine.dispatch_due() == 1
    assert channel.messages[0].kind is kind
    assert channel.messages[0].next_action
    recovered = engine.handle(event(kind, AlertState.RECOVERED, 2))
    assert recovered.state is AlertState.RECOVERED
    assert engine.dispatch_due() == 1
    assert channel.messages[-1].state is AlertState.RECOVERED
    assert channel.messages[-1].evidence_ref == EVIDENCE


def test_duplicate_storm_does_not_send_or_persist_duplicate_alerts() -> None:
    channel, journal = Channel(), Journal()
    engine = service(journal, channel)
    first = engine.handle(event())
    for _ in range(1000):
        assert engine.handle(event()) == first
    assert len(journal.history) == 1
    assert engine.dispatch_due() == 1
    for sequence in range(2, 30):
        engine.handle(event(sequence=sequence))
    assert engine.dispatch_due() == 0
    assert len(channel.messages) == 1


def test_acknowledgement_is_not_recovery_and_escalation_reopens_attention() -> None:
    engine = service()
    first = engine.handle(event())
    request = AcknowledgeRequest(
        incident_ref=first.incident_ref, revision=first.revision, operator_ref=ACTOR
    )
    acknowledged = engine.acknowledge(request)
    assert acknowledged.state is AlertState.ACTIVE
    assert acknowledged.acknowledgement is not None
    assert engine.acknowledge(request) == acknowledged
    escalated = engine.handle(event(state=AlertState.ESCALATED, sequence=2))
    assert escalated.acknowledgement is None
    assert escalated.owner is OwnerRole.INCIDENT_COMMANDER
    assert escalated.revision == first.revision + 1
    with pytest.raises(AlertError, match="revision_conflict"):
        engine.acknowledge(request)
    assert engine.handle(event(sequence=3)).state is AlertState.ESCALATED
    engine.handle(event(state=AlertState.RECOVERED, sequence=4))
    reopened = engine.handle(event(sequence=5))
    assert reopened.state is AlertState.ACTIVE
    assert reopened.acknowledgement is None


def test_stale_and_conflicting_events_cannot_clear_incident() -> None:
    engine = service()
    first = engine.handle(event(sequence=3))
    assert engine.handle(event(state=AlertState.RECOVERED, sequence=2)) == first
    with pytest.raises(AlertError, match="sequence_conflict"):
        engine.handle(event(state=AlertState.RECOVERED, sequence=3))
    with pytest.raises(AlertError, match="recovery_without_incident"):
        service().handle(event(state=AlertState.RECOVERED))


def test_failed_delivery_retries_after_cooldown_and_restart() -> None:
    journal, channel = Journal(), Channel()
    channel.fail = True
    engine = service(journal, channel)
    engine.handle(event())
    assert engine.dispatch_due() == 0
    assert engine.dispatch_due() == 0
    assert len(channel.messages) == 1
    restarted = AlertService(
        journal=journal,
        channels={AlertChannel.INDEPENDENT: channel},
        policy=engine.policy,
        clock=lambda: NOW + timedelta(seconds=31),
    )
    channel.fail = False
    assert restarted.dispatch_due() == 1
    assert len(channel.messages) == 2
    assert channel.messages[0].notification_ref == channel.messages[1].notification_ref
    assert "synthetic-private-provider-detail" not in restarted.snapshot().model_dump_json()


def test_journal_failure_blocks_send_and_requires_reload() -> None:
    journal, channel = Journal(), Channel()
    engine = service(journal, channel)
    journal.fail = True
    with pytest.raises(AlertError, match="journal_unavailable"):
        engine.handle(event())
    assert not channel.messages
    with pytest.raises(AlertError, match="reload_required"):
        engine.dispatch_due()


def test_two_writers_cannot_overwrite_checkpoint() -> None:
    journal = Journal()
    one, two = service(journal), service(journal)
    one.handle(event())
    with pytest.raises(AlertError, match="journal_conflict"):
        two.handle(event(kind=AlertKind.KILL_SWITCH))


def test_sensitive_values_are_keyed_references_only() -> None:
    key = SecretBytes(b"synthetic-unit-test-redaction-key-32")
    value = SecretStr("synthetic-private-account-and-token")
    ref = redacted_reference(value, key=key)
    assert ref.startswith("ref_")
    assert value.get_secret_value() not in ref
    assert ref == redacted_reference(value, key=key)
    assert ref != redacted_reference(value, key=SecretBytes(b"other-synthetic-unit-test-key-32xx"))
    with pytest.raises(AlertError, match="redaction_key_invalid"):
        redacted_reference(value, key=SecretBytes(b"short"))


def test_telegram_success_cannot_hide_independent_delivery_failure() -> None:
    independent, telegram, journal = Channel(), Channel(), Journal()
    independent.fail = True
    engine = AlertService(
        journal=journal,
        channels={AlertChannel.INDEPENDENT: independent, AlertChannel.TELEGRAM: telegram},
        policy=service().policy,
        clock=lambda: NOW,
    )
    engine.handle(event())
    assert engine.dispatch_due() == 1
    deliveries = engine.snapshot().deliveries
    assert not deliveries[0].delivered
    assert deliveries[1].delivered
    assert engine.dispatch_due() == 0
    assert len(independent.messages) == len(telegram.messages) == 1


@pytest.mark.parametrize("channels", [{}, {AlertChannel.TELEGRAM: Channel()}])
def test_independent_route_is_mandatory(channels: dict[AlertChannel, Channel]) -> None:
    with pytest.raises(AlertError, match="independent_channel_required"):
        AlertService(journal=Journal(), channels=channels, policy=service().policy)


def test_acknowledgement_default_is_denied() -> None:
    engine = AlertService(
        journal=Journal(),
        channels={AlertChannel.INDEPENDENT: Channel()},
        policy=service().policy,
        clock=lambda: NOW,
    )
    first = engine.handle(event())
    with pytest.raises(AlertError, match="acknowledgement_denied"):
        engine.acknowledge(
            AcknowledgeRequest(incident_ref=first.incident_ref, revision=1, operator_ref=ACTOR)
        )
    assert engine.snapshot().incidents[0].acknowledgement is None


def test_acknowledgement_authorizer_exception_is_redacted() -> None:
    class BrokenAuth:
        def authorize(self, request: AcknowledgeRequest, *, owner: OwnerRole) -> bool:
            raise RuntimeError("synthetic-private-provider-detail")

    engine = AlertService(
        journal=Journal(),
        channels={AlertChannel.INDEPENDENT: Channel()},
        policy=service().policy,
        authorizer=BrokenAuth(),
        clock=lambda: NOW,
    )
    first = engine.handle(event())
    with pytest.raises(AlertError, match=r"^acknowledgement_denied$"):
        engine.acknowledge(
            AcknowledgeRequest(incident_ref=first.incident_ref, revision=1, operator_ref=ACTOR)
        )


def test_escalation_bypasses_failed_delivery_cooldown() -> None:
    channel = Channel()
    channel.fail = True
    engine = service(channel=channel)
    engine.handle(event())
    engine.dispatch_due()
    engine.handle(event(state=AlertState.ESCALATED, sequence=2))
    channel.fail = False
    assert engine.dispatch_due() == 1
    assert channel.messages[-1].state is AlertState.ESCALATED
    assert engine.snapshot().deliveries[0].superseded
    assert engine.dispatch_due() == 0


def test_pending_old_fault_never_arrives_after_recovery() -> None:
    channel = Channel()
    engine = service(channel=channel)
    engine.handle(event())
    engine.handle(event(state=AlertState.RECOVERED, sequence=2))
    assert engine.dispatch_due() == 1
    assert channel.messages[0].previous_state is AlertState.ACTIVE
    assert channel.messages[0].state is AlertState.RECOVERED


@pytest.mark.parametrize("batch_size", [0, 101, True])
def test_batch_is_bounded(batch_size: int) -> None:
    with pytest.raises(AlertError, match="batch_size_invalid"):
        service().dispatch_due(batch_size=batch_size)


def test_capacity_backpressures_without_eviction() -> None:
    journal = Journal()
    engine = AlertService(
        journal=journal,
        channels={AlertChannel.INDEPENDENT: Channel()},
        policy=AlertPolicy(
            owner=OwnerRole.SAFETY_OPERATOR,
            escalation_owner=OwnerRole.INCIDENT_COMMANDER,
            capacity=1,
        ),
        clock=lambda: NOW,
    )
    first = engine.handle(event())
    with pytest.raises(AlertError, match="journal_capacity_exceeded"):
        engine.handle(event(kind=AlertKind.DUPLICATE_ORDER))
    assert engine.snapshot().incidents == (first,)


def test_changed_configuration_cannot_silently_reuse_checkpoint() -> None:
    journal = Journal()
    engine = service(journal)
    engine.handle(event())
    with pytest.raises(AlertError, match="journal_configuration_mismatch"):
        AlertService(
            journal=journal,
            channels={AlertChannel.INDEPENDENT: Channel(), AlertChannel.TELEGRAM: Channel()},
            policy=engine.policy,
        )


def test_journal_load_failure_is_not_treated_as_empty() -> None:
    class BrokenJournal(Journal):
        def load(self) -> AlertCheckpoint | None:
            raise RuntimeError("synthetic-private-provider-detail")

    with pytest.raises(AlertError, match=r"^journal_unavailable$"):
        service(BrokenJournal())


def test_recovery_must_be_fresh() -> None:
    journal = Journal()
    engine = service(journal)
    engine.handle(event())
    later = AlertService(
        journal=journal,
        channels={AlertChannel.INDEPENDENT: Channel()},
        policy=engine.policy,
        clock=lambda: NOW + timedelta(seconds=301),
    )
    with pytest.raises(AlertError, match="recovery_evidence_stale"):
        later.handle(event(state=AlertState.RECOVERED, sequence=2))
    assert later.snapshot().incidents[0].state is AlertState.ACTIVE


def test_future_event_and_regressed_clock_are_rejected() -> None:
    engine = service()
    with pytest.raises(AlertError, match="event_from_future"):
        engine.handle(event().model_copy(update={"observed_at": NOW + timedelta(seconds=1)}))
    engine.handle(event())
    with pytest.raises(AlertError, match="event_time_regressed"):
        engine.handle(
            event(sequence=2).model_copy(update={"observed_at": NOW - timedelta(seconds=1)})
        )


def test_failed_receipt_commit_does_not_claim_success_or_resend_immediately() -> None:
    journal = Journal()

    class ReceiptCrash(Channel):
        def send(self, notification: AlertNotification) -> DeliveryReceipt:
            journal.fail = True
            return super().send(notification)

    channel = ReceiptCrash()
    engine = service(journal, channel)
    engine.handle(event())
    with pytest.raises(AlertError, match="journal_unavailable"):
        engine.dispatch_due()
    assert journal.state is not None
    assert not journal.state.deliveries[0].delivered
    assert journal.state.deliveries[0].attempts == 1
    journal.fail = False
    assert service(journal, channel).dispatch_due() == 0


def test_delivery_attempt_is_persisted_before_calling_provider() -> None:
    journal, channel = Journal(), Channel()
    engine = service(journal, channel)
    engine.handle(event())
    journal.fail = True
    with pytest.raises(AlertError, match="journal_unavailable"):
        engine.dispatch_due()
    assert not channel.messages


@pytest.mark.parametrize(
    "damage", ["missing_delivery", "duplicate_incident", "wrong_state", "wrong_revision"]
)
def test_corrupt_checkpoint_fails_closed(damage: str) -> None:
    journal = Journal()
    engine = service(journal)
    engine.handle(event())
    saved = engine.snapshot()
    if damage == "missing_delivery":
        journal.state = saved.model_copy(update={"deliveries": ()})
    elif damage == "duplicate_incident":
        journal.state = saved.model_copy(update={"incidents": saved.incidents * 2})
    elif damage == "wrong_state":
        journal.state = saved.model_copy(
            update={
                "incidents": (
                    saved.incidents[0].model_copy(update={"state": AlertState.RECOVERED}),
                )
            }
        )
    else:
        delivery = saved.deliveries[0]
        journal.state = saved.model_copy(
            update={
                "deliveries": (
                    delivery.model_copy(
                        update={
                            "notification": delivery.notification.model_copy(
                                update={"revision": 100}
                            )
                        }
                    ),
                )
            }
        )
    with pytest.raises(AlertError, match="journal_unavailable"):
        service(journal)


def test_unvalidated_copy_cannot_smuggle_sensitive_fields_to_output() -> None:
    channel = Channel()
    engine = service(channel=channel)
    unsafe = event().model_copy(update={"subject_ref": "synthetic-private-account"})
    with pytest.raises(AlertError, match=r"^event_invalid$"):
        engine.handle(unsafe)
    assert not channel.messages
    assert not engine.snapshot().incidents


def test_backoff_is_capped_and_batch_never_exceeds_budget() -> None:
    now = NOW
    channel = Channel()
    channel.fail = True
    engine = AlertService(
        journal=Journal(),
        channels={AlertChannel.INDEPENDENT: channel},
        policy=service().policy,
        clock=lambda: now,
    )
    engine.handle(event())
    engine.handle(event(kind=AlertKind.DUPLICATE_ORDER))
    engine.dispatch_due(batch_size=1)
    assert len(channel.messages) == 1
    for _ in range(15):
        engine.dispatch_due()
        pending = engine.snapshot().deliveries
        assert all(d.due_at <= now + timedelta(hours=1) for d in pending)
        now = max(d.due_at for d in pending)
    assert engine.snapshot().deliveries[0].due_at - (now - timedelta(hours=1)) == timedelta(hours=1)


def test_invalid_clock_fails_at_startup() -> None:
    with pytest.raises(AlertError, match="clock_invalid"):
        AlertService(
            journal=Journal(),
            channels={AlertChannel.INDEPENDENT: Channel()},
            policy=service().policy,
            clock=lambda: datetime(2026, 10, 1),
        )


def test_acknowledgement_of_missing_or_recovered_incident_is_rejected() -> None:
    engine = service()
    with pytest.raises(AlertError, match="incident_missing"):
        engine.acknowledge(AcknowledgeRequest(incident_ref=REF, revision=1, operator_ref=ACTOR))
    engine.handle(event())
    recovered = engine.handle(event(state=AlertState.RECOVERED, sequence=2))
    with pytest.raises(AlertError, match="incident_recovered"):
        engine.acknowledge(
            AcknowledgeRequest(
                incident_ref=recovered.incident_ref, revision=recovered.revision, operator_ref=ACTOR
            )
        )
