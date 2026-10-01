"""P05-T6 durable approval handoff tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.handoff import (
    ApprovalExecutionOutcome,
    ApprovalExecutionResult,
    ApprovalHandoffCode,
    ApprovalHandoffContractError,
    ApprovalHandoffService,
    PaperExecutionHandoff,
)
from ainvest.approval.order_hash import attach_order_hash
from ainvest.approval.service import _challenge_fields, _event_fields, _proposal_fields
from ainvest.audit.service import AuditService
from ainvest.db.models import ApprovalEventRow, ApprovalOutboxRow, AuditEventRow
from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.uow import UnitOfWork
from ainvest.schemas.approval import (
    ApprovalChallengeV1_1,
    ApprovalEvent,
    ApprovalEventOutcome,
    ApprovalMethod,
    ApprovalScope,
)
from ainvest.schemas.examples import approval_event_example, order_proposal_valid
from ainvest.schemas.orders import OrderProposal

NOW = datetime(2026, 7, 24, 18, 31, 10, tzinfo=UTC)
OUTBOX_ID = "apob_01HZYHANDOFF00001"


def _factory(path: Path) -> sessionmaker[Session]:
    engine = create_db_engine(f"sqlite+pysqlite:///{path}", connect_args={"timeout": 10})
    create_all_tables(engine)
    return create_session_factory(engine)


def _seed(
    factory: sessionmaker[Session],
    *,
    proposal_payload: dict[str, object] | None = None,
    event_payload: dict[str, object] | None = None,
    outbox_order_hash: str | None = None,
) -> tuple[OrderProposal, ApprovalEvent]:
    proposal_data = dict(order_proposal_valid())
    proposal_data.update(
        {
            "account_scope": "paper",
            "created_at": "2026-07-24T18:30:12Z",
            "expires_at": "2026-07-24T18:32:12Z",
        }
    )
    if proposal_payload:
        proposal_data.update(proposal_payload)
    proposal = OrderProposal.model_validate(attach_order_hash(proposal_data))
    event_data = {
        **approval_event_example(),
        "proposal_id": proposal.proposal_id,
        "order_hash": proposal.order_hash,
        "approved_at": "2026-07-24T18:31:00Z",
    }
    if event_payload:
        event_data.update(event_payload)
    event = ApprovalEvent.model_validate(event_data)
    challenge = ApprovalChallengeV1_1(
        challenge_id=event.challenge_id,
        proposal_id=proposal.proposal_id,
        order_hash=proposal.order_hash,
        method=event.method,
        scope=event.scope,
        nonce_hash="a" * 64,
        created_at=proposal.created_at,
        expires_at=proposal.expires_at,
    )
    with UnitOfWork(factory) as uow:
        uow.proposals_repo.add_fields(_proposal_fields(proposal))
        uow.approvals_repo.add_challenge_fields(_challenge_fields(challenge))
        uow.approvals_repo.add_event_fields(_event_fields(event))
        uow.approvals_repo.add_outbox(
            {
                "outbox_id": OUTBOX_ID,
                "approval_event_id": event.event_id,
                "proposal_id": proposal.proposal_id,
                "order_hash": outbox_order_hash or proposal.order_hash,
                "status": "PENDING",
                "created_at": NOW,
                "payload_json": {"untrusted": "ignored"},
            }
        )
    return proposal, event


class RecordingExecution:
    def __init__(
        self, outcome: ApprovalExecutionOutcome = ApprovalExecutionOutcome.SUCCEEDED
    ) -> None:
        self.outcome = outcome
        self.commands: list[PaperExecutionHandoff] = []
        self._lock = Lock()

    def dispatch(self, request: PaperExecutionHandoff) -> ApprovalExecutionResult:
        with self._lock:
            self.commands.append(request)
        return ApprovalExecutionResult(
            command_id=request.command_id,
            correlation_id=request.correlation_id,
            idempotency_id=request.idempotency_id,
            proposal_id=request.proposal_id,
            client_order_id=request.client_order_id,
            outcome=self.outcome,
            event_id="evt_01HZYHANDOFF00001",
            reason_code=(
                "PAPER_REJECTED" if self.outcome is ApprovalExecutionOutcome.REJECTED else None
            ),
        )


def _counts(factory: sessionmaker[Session]) -> tuple[str, int]:
    with factory() as session:
        status = session.scalar(select(ApprovalOutboxRow.status))
        audits = session.scalar(select(func.count()).select_from(AuditEventRow))
    assert status is not None and audits is not None
    return status, audits


@pytest.mark.unit
def test_valid_paper_handoff_dispatches_once_and_audits(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "valid.db")
    proposal, event = _seed(factory)
    execution = RecordingExecution()
    service = ApprovalHandoffService(factory, execution, clock=lambda: NOW)

    result = service.consume(OUTBOX_ID)

    assert result.code is ApprovalHandoffCode.DISPATCHED
    assert len(execution.commands) == 1
    command = execution.commands[0]
    assert command.proposal_id == proposal.proposal_id
    assert command.order_hash == proposal.order_hash
    assert command.approval_event_id == event.event_id
    assert command.input_digest == proposal.order_hash
    assert command.client_order_id.startswith("paper_")
    assert _counts(factory) == ("CONSUMED", 1)

    replay = service.consume(OUTBOX_ID)
    assert replay.code is ApprovalHandoffCode.ALREADY_CONSUMED
    assert len(execution.commands) == 1


@pytest.mark.unit
def test_two_consumers_create_one_execution_attempt(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "concurrent.db")
    _seed(factory)
    execution = RecordingExecution()
    service = ApprovalHandoffService(factory, execution, clock=lambda: NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.consume(OUTBOX_ID), range(2)))

    assert [result.code for result in results].count(ApprovalHandoffCode.DISPATCHED) == 1
    assert [result.code for result in results].count(ApprovalHandoffCode.ALREADY_CONSUMED) == 1
    assert len(execution.commands) == 1


@pytest.mark.unit
def test_dispatch_failure_rolls_back_and_retry_reuses_exact_command(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "recovery.db")
    _seed(factory)

    class FailOnce(RecordingExecution):
        def dispatch(self, request: PaperExecutionHandoff) -> ApprovalExecutionResult:
            if not self.commands:
                self.commands.append(request)
                raise RuntimeError("injected crash")
            return super().dispatch(request)

    execution = FailOnce()
    service = ApprovalHandoffService(factory, execution, clock=lambda: NOW)
    with pytest.raises(RuntimeError, match="injected crash"):
        service.consume(OUTBOX_ID)
    assert _counts(factory) == ("PENDING", 0)

    result = service.consume(OUTBOX_ID)
    assert result.code is ApprovalHandoffCode.DISPATCHED
    assert len(execution.commands) == 2
    assert execution.commands[0] == execution.commands[1]
    assert _counts(factory) == ("CONSUMED", 1)


@pytest.mark.unit
def test_unknown_outcome_stays_recoverable_with_stable_identity(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "unknown.db")
    _seed(factory)
    execution = RecordingExecution(ApprovalExecutionOutcome.RETRY_LATER)
    service = ApprovalHandoffService(factory, execution, clock=lambda: NOW)

    first = service.consume(OUTBOX_ID)
    second = service.consume(OUTBOX_ID)

    assert first.code is second.code is ApprovalHandoffCode.RETRY_LATER
    assert execution.commands[0] == execution.commands[1]
    assert _counts(factory) == ("PENDING", 0)


@pytest.mark.unit
def test_execution_rejection_is_terminal_and_consumed(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "execution-rejected.db")
    _seed(factory)
    execution = RecordingExecution(ApprovalExecutionOutcome.REJECTED)

    result = ApprovalHandoffService(factory, execution, clock=lambda: NOW).consume(OUTBOX_ID)

    assert result.code is ApprovalHandoffCode.EXECUTION_REJECTED
    assert len(execution.commands) == 1
    assert _counts(factory) == ("CONSUMED", 1)


@pytest.mark.unit
def test_audit_failure_rolls_back_consumed_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = _factory(tmp_path / "audit-rollback.db")
    _seed(factory)
    execution = RecordingExecution()

    def fail_audit(self: AuditService, envelope: object) -> object:
        del self, envelope
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(AuditService, "append", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        ApprovalHandoffService(factory, execution, clock=lambda: NOW).consume(OUTBOX_ID)

    assert len(execution.commands) == 1
    assert _counts(factory) == ("PENDING", 0)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("proposal_payload", "event_payload", "outbox_order_hash"),
    [
        ({"expires_at": "2026-07-24T18:31:05Z"}, None, None),
        (None, {"outcome": ApprovalEventOutcome.DENIED.value}, None),
        (None, None, "sha256:" + ("f" * 64)),
        (
            {"account_scope": "agentic"},
            {
                "method": ApprovalMethod.WEBAUTHN.value,
                "scope": ApprovalScope.LIVE.value,
                "approver_identity": "passkey-test-user",
            },
            None,
        ),
    ],
)
def test_expired_denied_tampered_and_live_records_fail_closed(
    tmp_path: Path,
    proposal_payload: dict[str, object] | None,
    event_payload: dict[str, object] | None,
    outbox_order_hash: str | None,
) -> None:
    factory = _factory(tmp_path / "rejected.db")
    _seed(
        factory,
        proposal_payload=proposal_payload,
        event_payload=event_payload,
        outbox_order_hash=outbox_order_hash,
    )
    execution = RecordingExecution()

    result = ApprovalHandoffService(factory, execution, clock=lambda: NOW).consume(OUTBOX_ID)

    assert result.code is ApprovalHandoffCode.POLICY_REJECTED
    assert execution.commands == []
    assert _counts(factory) == ("CONSUMED", 1)


@pytest.mark.unit
def test_execution_contract_violation_rolls_back(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "contract.db")
    _seed(factory)

    class BadExecution(RecordingExecution):
        def dispatch(self, request: PaperExecutionHandoff) -> ApprovalExecutionResult:
            result = super().dispatch(request)
            return replace(result, correlation_id="corr_wrong000")

    with pytest.raises(ApprovalHandoffContractError, match="trace"):
        ApprovalHandoffService(factory, BadExecution(), clock=lambda: NOW).consume(OUTBOX_ID)
    assert _counts(factory) == ("PENDING", 0)


@pytest.mark.unit
def test_indexed_event_policy_columns_must_match_validated_payload(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "column-tamper.db")
    _seed(factory)
    with factory.begin() as session:
        row = session.scalar(select(ApprovalOutboxRow))
        assert row is not None
        event = row.approval_event_id
        stored = session.scalar(select(ApprovalEventRow).where(ApprovalEventRow.event_id == event))
        assert stored is not None
        stored.scope = "live"
    execution = RecordingExecution()

    result = ApprovalHandoffService(factory, execution, clock=lambda: NOW).consume(OUTBOX_ID)

    assert result.code is ApprovalHandoffCode.POLICY_REJECTED
    assert execution.commands == []
    assert _counts(factory) == ("CONSUMED", 1)


@pytest.mark.unit
def test_missing_outbox_is_terminal_without_dispatch(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "missing.db")
    execution = RecordingExecution()
    result = ApprovalHandoffService(factory, execution, clock=lambda: NOW).consume("apob_missing")
    assert result.code is ApprovalHandoffCode.NOT_FOUND
    assert execution.commands == []
