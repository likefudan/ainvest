"""Real kill-switch events plus a durable TEST journal and fake notification ports.

The tiny SQLite adapter proves the journal contract; it is not deployment code.
P07-T2 owns real reconciliation event emission, which this test does not claim.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretBytes, SecretStr

from ainvest.observability.alerts import (
    AlertChannel,
    AlertCheckpoint,
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
from ainvest.risk.kill_switch import KillSwitch, KillSwitchAlertKind

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


class SQLiteTestJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS checkpoints "
                "(version INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def load(self) -> AlertCheckpoint | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT payload FROM checkpoints ORDER BY version DESC LIMIT 1"
            ).fetchone()
        return AlertCheckpoint.model_validate_json(row[0]) if row else None

    def commit(self, checkpoint: AlertCheckpoint, *, expected_version: int) -> bool:
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM checkpoints"
            ).fetchone()[0]
            if current != expected_version:
                return False
            connection.execute(
                "INSERT INTO checkpoints VALUES (?, ?)",
                (checkpoint.version, checkpoint.model_dump_json()),
            )
        return True


class FakeIndependentChannel:
    def __init__(self, *, accepted: bool) -> None:
        self.accepted = accepted
        self.messages: list[AlertNotification] = []

    def send(self, notification: AlertNotification) -> DeliveryReceipt:
        self.messages.append(notification)
        return DeliveryReceipt(accepted=self.accepted)


def test_kill_switch_block_alert_restart_and_recovery_without_cancel(tmp_path: Path) -> None:
    path = tmp_path / "test-alert-checkpoints.sqlite3"
    policy = AlertPolicy(
        owner=OwnerRole.SAFETY_OPERATOR, escalation_owner=OwnerRole.INCIDENT_COMMANDER
    )
    channel = FakeIndependentChannel(accepted=False)
    engine = AlertService(
        journal=SQLiteTestJournal(path),
        channels={AlertChannel.INDEPENDENT: channel},
        policy=policy,
        clock=lambda: NOW,
    )
    switch = KillSwitch()
    # Raw producer reason is deliberately sensitive-looking. The composition
    # bridge selects enums/references and never copies arbitrary provider text.
    private_reason = "synthetic-private-reason-not-for-notifications"
    switch.activate_operational(reason=private_reason, as_of=NOW)
    [activation] = switch.drain_alerts()
    assert activation.kind is KillSwitchAlertKind.ACTIVATED
    key = SecretBytes(b"synthetic-integration-redaction-key-32")
    subject = redacted_reference(SecretStr("synthetic-local-switch"), key=key)
    engine.handle(
        FundsSafetyEvent(
            environment="test",
            kind=AlertKind.KILL_SWITCH,
            subject_ref=subject,
            sequence=1,
            state=AlertState.ACTIVE,
            observed_at=activation.observed_at,
        )
    )
    assert switch.is_active()
    assert engine.dispatch_due() == 0
    assert switch.is_active()

    # A new service and new DB connection recover the pending retry, not an
    # in-memory list belonging to the old service.
    channel.accepted = True
    restarted = AlertService(
        journal=SQLiteTestJournal(path),
        channels={AlertChannel.INDEPENDENT: channel},
        policy=policy,
        clock=lambda: NOW + timedelta(seconds=31),
    )
    assert restarted.dispatch_due() == 1
    assert channel.messages[0].notification_ref == channel.messages[1].notification_ref
    assert switch.is_active()
    # Explicit external operator workflow clears the source. Alerts themselves
    # never deactivate a switch or cancel orders (DEC-008/019).
    switch.deactivate_operational(
        reason="synthetic-verified-recovery", as_of=NOW + timedelta(seconds=31)
    )
    [recovery] = switch.drain_alerts()
    assert recovery.kind is KillSwitchAlertKind.DEACTIVATED
    restarted.handle(
        FundsSafetyEvent(
            environment="test",
            kind=AlertKind.KILL_SWITCH,
            subject_ref=subject,
            sequence=2,
            state=AlertState.RECOVERED,
            observed_at=recovery.observed_at,
            evidence_ref=redacted_reference(SecretStr("synthetic-verified-evidence"), key=key),
        )
    )
    assert restarted.dispatch_due() == 1
    assert channel.messages[-1].state is AlertState.RECOVERED
    assert private_reason not in restarted.snapshot().model_dump_json()
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT payload FROM checkpoints").fetchall()
    assert len(rows) >= 6
    assert all(private_reason not in row[0] for row in rows)
    assert not switch.is_active()


def test_multithread_duplicate_burst_has_one_outbox_delivery(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    channel = FakeIndependentChannel(accepted=True)
    engine = AlertService(
        journal=SQLiteTestJournal(tmp_path / "duplicates.sqlite3"),
        channels={AlertChannel.INDEPENDENT: channel},
        policy=AlertPolicy(
            owner=OwnerRole.SAFETY_OPERATOR, escalation_owner=OwnerRole.INCIDENT_COMMANDER
        ),
        clock=lambda: NOW,
    )
    event = FundsSafetyEvent(
        environment="test",
        kind=AlertKind.SUBMIT_UNKNOWN,
        subject_ref="ref_" + "a" * 64,
        sequence=1,
        state=AlertState.ACTIVE,
        observed_at=NOW,
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(engine.handle, [event] * 100))
    assert all(result == results[0] for result in results)
    assert engine.snapshot().version == 1
    assert engine.dispatch_due() == 1
    assert len(channel.messages) == 1
