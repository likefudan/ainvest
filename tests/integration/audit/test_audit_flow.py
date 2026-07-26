"""Integration tests for append-only audit persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ainvest.audit import (
    ActorType,
    AuditEventEnvelope,
    AuditEventType,
    AuditService,
    assert_no_plaintext_secrets,
)
from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.uow import UnitOfWork

SECRET_CORPUS = [
    "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
    "raw-approval-token-plaintext",
    "Bearer robinhood-oauth-should-not-persist",
    "cookie-session-value-xyz",
]


@pytest.mark.integration
def test_audit_timeline_and_secret_corpus(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'audit.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)

    with UnitOfWork(factory) as uow:
        audit = AuditService.from_uow(uow)
        audit.append(
            AuditEventEnvelope(
                event_id="aud_01HZYINT00000001",
                event_type=AuditEventType.PROPOSAL_CREATED,
                occurred_at=datetime(2026, 7, 24, 18, 30, 1, tzinfo=UTC),
                actor_type=ActorType.SYSTEM,
                actor_id="sizer",
                subject_type="order_proposal",
                subject_id="ordp_01HZYINT00000001",
                correlation_id="corr_int_1",
                after_state={"status": "PENDING_APPROVAL"},
                payload={
                    "bot_token": SECRET_CORPUS[0],
                    "approval_token": SECRET_CORPUS[1],
                    "authorization": SECRET_CORPUS[2],
                    "cookie": SECRET_CORPUS[3],
                },
            )
        )
        audit.append(
            AuditEventEnvelope(
                event_id="aud_01HZYINT00000002",
                event_type=AuditEventType.APPROVAL_CONSUMED,
                occurred_at=datetime(2026, 7, 24, 18, 30, 2, tzinfo=UTC),
                actor_type=ActorType.USER,
                actor_id="tg:99",
                subject_type="order_proposal",
                subject_id="ordp_01HZYINT00000001",
                correlation_id="corr_int_1",
                causation_id="aud_01HZYINT00000001",
                before_state={"status": "PENDING_APPROVAL"},
                after_state={"status": "APPROVED"},
            )
        )

    with UnitOfWork(factory) as uow:
        audit = AuditService.from_uow(uow)
        timeline = audit.timeline_for_proposal("ordp_01HZYINT00000001")
        assert len(timeline) == 2
        assert timeline[0].event_type == "PROPOSAL_CREATED"
        assert timeline[1].event_type == "APPROVAL_CONSUMED"
        assert timeline[1].causation_id == "aud_01HZYINT00000001"
        for event in timeline:
            assert_no_plaintext_secrets(event.payload_json, SECRET_CORPUS)
            assert_no_plaintext_secrets(event.before_state, SECRET_CORPUS)
            assert_no_plaintext_secrets(event.after_state, SECRET_CORPUS)

    engine.dispose()
