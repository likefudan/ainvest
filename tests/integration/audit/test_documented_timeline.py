"""The documentation's query example is guarded and intentionally lossy."""

from __future__ import annotations

import json
import runpy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import SecretBytes

from ainvest.audit import ActorType, AuditEventEnvelope, AuditEventType, AuditService
from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.uow import UnitOfWork

ROOT = Path(__file__).resolve().parents[3]
query = runpy.run_path(str(ROOT / "docs/api/audit_query_example.py"))["query_timeline"]
KEY = SecretBytes(b"synthetic-documentation-key-only-32-bytes")
pytestmark = pytest.mark.integration


def test_denied_query_never_reads_database() -> None:
    audit = Mock(spec=AuditService)
    gate = Mock(side_effect=PermissionError("denied"))
    with pytest.raises(PermissionError):
        query(audit, selector="proposal", identifier="synthetic-id", authorize=gate, key=KEY)
    gate.assert_called_once_with("proposal", "synthetic-id")
    assert not audit.mock_calls


@pytest.mark.parametrize("decision", [False, None, "true", 1])
def test_gate_requires_explicit_boolean_success(decision: object) -> None:
    audit = Mock(spec=AuditService)
    with pytest.raises(PermissionError):
        query(
            audit,
            selector="proposal",
            identifier="id",
            authorize=Mock(return_value=decision),
            key=KEY,
        )
    assert not audit.mock_calls


@pytest.mark.parametrize("selector,identifier", [("unknown", "id"), ("proposal", "")])
def test_invalid_selector_never_queries(selector: str, identifier: str) -> None:
    audit = Mock(spec=AuditService)
    with pytest.raises(ValueError):
        query(audit, selector=selector, identifier=identifier, authorize=Mock(), key=KEY)
    assert not audit.mock_calls


def test_short_redaction_key_never_queries() -> None:
    audit = Mock(spec=AuditService)
    with pytest.raises(ValueError):
        query(audit, selector="proposal", identifier="id", authorize=Mock(), key=SecretBytes(b"x"))
    assert not audit.mock_calls


def test_documented_proposal_and_correlation_queries(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'audit.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    try:
        with UnitOfWork(factory) as uow:
            audit = AuditService.from_uow(uow)
            for index, subject in enumerate(("proposal_one", "proposal_two")):
                audit.append(
                    AuditEventEnvelope(
                        event_id=f"synthetic_event_{index}",
                        event_type=AuditEventType.PROPOSAL_CREATED
                        if index == 0
                        else "private-text",
                        occurred_at=datetime(2026, 10, 1, 12, index, tzinfo=UTC),
                        actor_type=ActorType.USER,
                        actor_id="private-actor",
                        subject_type="order_proposal",
                        subject_id=subject,
                        correlation_id="synthetic-correlation",
                        causation_id="synthetic_event_0" if index else None,
                        payload={"unrecognized_key": "private-free-text"},
                        before_state={"status": "private-state"},
                        after_state={"status": "APPROVED", "account": "private-account"},
                        error_detail="private-error",
                    )
                )
        with UnitOfWork(factory) as uow:
            audit = AuditService.from_uow(uow)
            gate = Mock(return_value=True)  # Synthetic gate, never a production authenticator.
            proposal = query(
                audit, selector="proposal", identifier="proposal_one", authorize=gate, key=KEY
            )
            timeline = query(
                audit,
                selector="correlation",
                identifier="synthetic-correlation",
                authorize=gate,
                key=KEY,
            )
            assert len(proposal) == 1 and len(timeline) == 2
            assert proposal[0] == timeline[0]
            assert timeline[1]["causation_ref"] == timeline[0]["event_ref"]
            assert timeline[0]["event_type"] == "PROPOSAL_CREATED"
            assert timeline[1]["event_type"] == "OTHER"
            assert timeline[0]["before"] is None and timeline[0]["after"] == "APPROVED"
            assert gate.call_count == 2
            text = json.dumps(timeline)
            for raw in ("private-", "synthetic_event", "synthetic-correlation", "proposal_one"):
                assert raw not in text
            assert all(row["event_ref"].startswith("ref_") for row in timeline)
            assert (
                query(audit, selector="proposal", identifier="missing", authorize=gate, key=KEY)
                == []
            )
    finally:
        engine.dispose()
