"""Unit tests for append-only audit service."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from ainvest.audit import (
    ActorType,
    AuditEventEnvelope,
    AuditEventType,
    AuditService,
    assert_no_plaintext_secrets,
    record_state_change,
)
from ainvest.audit.envelope import MAX_AUDIT_PAYLOAD_BYTES
from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.uow import UnitOfWork


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.mark.unit
def test_critical_state_change_creates_audit_event(
    session_factory: sessionmaker[Session],
) -> None:
    with UnitOfWork(session_factory) as uow:
        audit = AuditService.from_uow(uow)
        row = record_state_change(
            audit,
            event_id="aud_01HZYTEST0000001",
            event_type=AuditEventType.PROPOSAL_STATUS_CHANGED,
            actor_type=ActorType.SYSTEM,
            actor_id="risk-engine",
            subject_type="order_proposal",
            subject_id="ordp_01HZYTEST0000001",
            before={"status": "PENDING_APPROVAL"},
            after={"status": "APPROVED"},
            correlation_id="corr_001",
            payload={"bot_token": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"},
        )
        assert row.event_id == "aud_01HZYTEST0000001"
        assert row.payload_json["bot_token"] == "***REDACTED***"
        assert_no_plaintext_secrets(
            row.payload_json,
            ["123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"],
        )


@pytest.mark.unit
def test_error_detail_is_redacted(session_factory: sessionmaker[Session]) -> None:
    token = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    with UnitOfWork(session_factory) as uow:
        audit = AuditService.from_uow(uow)
        row = audit.append(
            AuditEventEnvelope(
                event_id="aud_01HZYTEST0000099",
                event_type=AuditEventType.GENERIC,
                occurred_at=datetime.now(UTC),
                actor_type=ActorType.SYSTEM,
                actor_id="tester",
                subject_type="order_proposal",
                subject_id="ordp_01HZYTEST0000001",
                error_code="BROKER_AUTH",
                error_detail=f"upstream failed with bot token {token}",
                payload={},
            )
        )
        assert token not in (row.error_detail or "")
        assert "***REDACTED***" in (row.error_detail or "")


@pytest.mark.unit
def test_audit_has_no_update_delete_api() -> None:
    assert not hasattr(AuditService, "update")
    assert not hasattr(AuditService, "delete")
    assert not hasattr(AuditService, "remove")


@pytest.mark.unit
def test_large_payload_truncated_to_digest(session_factory: sessionmaker[Session]) -> None:
    huge = {"blob": "x" * (MAX_AUDIT_PAYLOAD_BYTES + 1000)}
    with UnitOfWork(session_factory) as uow:
        audit = AuditService.from_uow(uow)
        envelope = AuditEventEnvelope(
            event_id="aud_01HZYTEST0000002",
            event_type=AuditEventType.GENERIC,
            occurred_at=datetime.now(UTC),
            actor_type=ActorType.SYSTEM,
            actor_id="tester",
            subject_type="order_proposal",
            subject_id="ordp_01HZYTEST0000001",
            payload=huge,
        )
        row = audit.append(envelope)
        assert row.payload_truncated is True
        assert row.payload_json["truncated"] is True
        assert str(row.output_digest).startswith("sha256:")


@pytest.mark.unit
def test_proposal_timeline_reconstructable(session_factory: sessionmaker[Session]) -> None:
    with UnitOfWork(session_factory) as uow:
        audit = AuditService.from_uow(uow)
        for index, event_type in enumerate(
            (
                AuditEventType.PROPOSAL_CREATED,
                AuditEventType.APPROVAL_CHALLENGE_CREATED,
                AuditEventType.APPROVAL_CONSUMED,
            ),
            start=1,
        ):
            audit.append(
                AuditEventEnvelope(
                    event_id=f"aud_01HZYTEST00000{index:02d}",
                    event_type=event_type,
                    occurred_at=datetime(2026, 7, 24, 18, 30, index, tzinfo=UTC),
                    actor_type=ActorType.SYSTEM,
                    actor_id="pipeline",
                    subject_type="order_proposal",
                    subject_id="ordp_01HZYTEST0000001",
                    correlation_id="corr_timeline",
                    before_state={"step": index - 1},
                    after_state={"step": index},
                )
            )

        timeline = audit.timeline_for_proposal("ordp_01HZYTEST0000001")
        assert [item.event_type for item in timeline] == [
            "PROPOSAL_CREATED",
            "APPROVAL_CHALLENGE_CREATED",
            "APPROVAL_CONSUMED",
        ]
        assert [item.after_state["step"] for item in timeline] == [1, 2, 3]
