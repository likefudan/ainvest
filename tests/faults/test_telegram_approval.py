"""Telegram approval faults across durable storage and execution handoff."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.handoff import (
    ApprovalExecutionOutcome,
    ApprovalExecutionResult,
    ApprovalHandoffCode,
    ApprovalHandoffService,
    PaperExecutionHandoff,
)
from ainvest.approval.order_hash import attach_order_hash
from ainvest.approval.service import _challenge_fields, _event_fields, _proposal_fields
from ainvest.approval.telegram import TelegramBotIdentity, TelegramEnvironment
from ainvest.approval.telegram_updates import (
    AsyncioTelegramPollingControl,
    AuthorizedTelegramUpdate,
    TelegramHandlerDisposition,
    TelegramLongPoller,
    TelegramProviderRateLimited,
    TelegramProviderUpdate,
    TelegramProviderUpdateKind,
)
from ainvest.config import Settings, TelegramBotSettings, TelegramRecipient
from ainvest.db.models import ApprovalOutboxRow, AuditEventRow
from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.uow import UnitOfWork
from ainvest.schemas.approval import ApprovalChallengeV1_1, ApprovalEvent
from ainvest.schemas.examples import approval_event_example, order_proposal_valid
from ainvest.schemas.orders import OrderProposal

from .conftest import FaultEvidence, assert_fault_evidence

NOW = datetime(2026, 7, 24, 18, 31, 10, tzinfo=UTC)
OUTBOX_ID = "apob_fault_matrix_0001"


def _factory(path: Path) -> sessionmaker[Session]:
    engine = create_db_engine(f"sqlite+pysqlite:///{path}", connect_args={"timeout": 10})
    create_all_tables(engine)
    return create_session_factory(engine)


def _seed(factory: sessionmaker[Session]) -> None:
    proposal_payload = dict(order_proposal_valid())
    proposal_payload.update(
        {
            "account_scope": "paper",
            "created_at": "2026-07-24T18:30:12Z",
            "expires_at": "2026-07-24T18:32:12Z",
        }
    )
    proposal = OrderProposal.model_validate(attach_order_hash(proposal_payload))
    event = ApprovalEvent.model_validate(
        {
            **approval_event_example(),
            "proposal_id": proposal.proposal_id,
            "order_hash": proposal.order_hash,
            "approved_at": "2026-07-24T18:31:00Z",
        }
    )
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
                "order_hash": proposal.order_hash,
                "status": "PENDING",
                "created_at": NOW,
                "payload_json": {"untrusted": "ignored"},
            }
        )


class _FundsBoundary:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.calls: list[PaperExecutionHandoff] = []
        self.effects = 0
        self._lock = Lock()

    def dispatch(self, request: PaperExecutionHandoff) -> ApprovalExecutionResult:
        with self._lock:
            self.calls.append(request)
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("injected worker crash before durable result")
            self.effects += 1
        return ApprovalExecutionResult(
            command_id=request.command_id,
            correlation_id=request.correlation_id,
            idempotency_id=request.idempotency_id,
            proposal_id=request.proposal_id,
            client_order_id=request.client_order_id,
            outcome=ApprovalExecutionOutcome.SUCCEEDED,
            event_id="evt_fault_handoff_0001",
        )


def _storage_evidence(factory: sessionmaker[Session]) -> tuple[str, int]:
    with factory() as session:
        status = session.scalar(
            select(ApprovalOutboxRow.status).where(ApprovalOutboxRow.outbox_id == OUTBOX_ID)
        )
        audits = session.scalar(select(func.count()).select_from(AuditEventRow))
    assert status is not None and audits is not None
    return status, audits


@pytest.mark.integration
def test_concurrent_duplicate_approval_delivery_has_one_funds_effect(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "approval-concurrent.db")
    _seed(factory)
    boundary = _FundsBoundary()
    service = ApprovalHandoffService(factory, boundary, clock=lambda: NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.consume(OUTBOX_ID), range(2)))

    status, audits = _storage_evidence(factory)
    evidence = FaultEvidence(
        final_state=status,
        audit_or_result=tuple(result.code.value for result in results),
        external_calls=len(boundary.calls),
        funds_effect=str(boundary.effects),
    )
    assert {result.code for result in results} == {
        ApprovalHandoffCode.DISPATCHED,
        ApprovalHandoffCode.ALREADY_CONSUMED,
    }
    assert audits == 1
    assert_fault_evidence(evidence, state="CONSUMED", calls=1, funds="1")


@pytest.mark.integration
def test_crash_rolls_back_outbox_and_retry_reuses_stable_identity(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "approval-recovery.db")
    _seed(factory)
    boundary = _FundsBoundary(fail_once=True)
    service = ApprovalHandoffService(factory, boundary, clock=lambda: NOW)

    with pytest.raises(RuntimeError, match="injected worker crash"):
        service.consume(OUTBOX_ID)
    assert _storage_evidence(factory) == ("PENDING", 0)

    result = service.consume(OUTBOX_ID)
    status, audits = _storage_evidence(factory)
    evidence = FaultEvidence(
        final_state=status,
        audit_or_result=(result.code.value, str(audits)),
        external_calls=len(boundary.calls),
        funds_effect=str(boundary.effects),
    )
    assert boundary.calls[0] == boundary.calls[1]
    assert result.code is ApprovalHandoffCode.DISPATCHED
    assert audits == 1
    assert_fault_evidence(evidence, state="CONSUMED", calls=2, funds="1")


@dataclass
class _TelegramIdentity:
    calls: int = 0

    async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity:
        assert token == "synthetic-token"
        assert timeout_seconds == 5.0
        self.calls += 1
        return TelegramBotIdentity(id=9001)


@dataclass
class _TelegramUpdates:
    outcomes: list[tuple[TelegramProviderUpdate, ...] | BaseException]
    stop: asyncio.Event | None = None
    calls: int = 0

    async def get_updates(self, token: str, **kwargs: object) -> tuple[TelegramProviderUpdate, ...]:
        del kwargs
        assert token == "synthetic-token"
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if not self.outcomes and self.stop is not None:
            self.stop.set()
        return outcome


@dataclass
class _TelegramHandler:
    updates: list[int] = field(default_factory=list)
    funds_effects: int = 0

    async def handle(self, update: AuthorizedTelegramUpdate) -> TelegramHandlerDisposition:
        self.updates.append(update.update_id)
        self.funds_effects += 1
        return TelegramHandlerDisposition.TERMINAL_HANDLED


class _StopOnDelay:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def is_set(self) -> bool:
        return False

    async def wait(self, timeout_seconds: float) -> bool:
        self.delays.append(timeout_seconds)
        return True


def _telegram_settings() -> Settings:
    return Settings(
        telegram_staging=TelegramBotSettings(
            enabled=True,
            bot_token=SecretStr("synthetic-token"),
            expected_bot_id=9001,
            allowed_recipients=(TelegramRecipient(user_id=101, private_chat_id=201),),
        )
    )


def _callback(update_id: int, callback_id: str = "same-callback") -> TelegramProviderUpdate:
    return TelegramProviderUpdate(
        update_id=update_id,
        kind=TelegramProviderUpdateKind.CALLBACK,
        sender_user_id=101,
        chat_id=201,
        message_id=301,
        chat_type="private",
        callback_query_id=SecretStr(callback_id),
        callback_data=SecretStr("opaque-callback"),
    )


@pytest.mark.integration
def test_telegram_rate_limit_never_reaches_approval_or_funds(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "telegram-rate-limit.db")
    identity = _TelegramIdentity()
    updates = _TelegramUpdates([TelegramProviderRateLimited(1_000)])
    handler = _TelegramHandler()
    control = _StopOnDelay()
    poller = TelegramLongPoller(
        settings=_telegram_settings(),
        environment=TelegramEnvironment.STAGING,
        session_factory=factory,
        identity_transport=identity,
        update_transport=updates,
        handler=handler,
        clock=lambda: NOW,
        random_value=lambda: 1,
        owner="fault-rate-limit",
    )

    asyncio.run(poller.run(control))
    with UnitOfWork(factory) as uow:
        state = uow.telegram_updates_repo.get_state("staging")
        assert state is not None
        evidence = FaultEvidence(
            final_state=f"offset:{state.next_offset}",
            audit_or_result=(f"backoff:{control.delays[0]}",),
            external_calls=updates.calls,
            funds_effect=str(handler.funds_effects),
        )

    assert control.delays == [60.0]
    assert_fault_evidence(evidence, state="offset:0", calls=1, funds="0")


@pytest.mark.integration
def test_repeated_out_of_order_telegram_updates_have_one_effect(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "telegram-out-of-order.db")
    stop = asyncio.Event()
    updates = _TelegramUpdates([(_callback(8), _callback(7)), ()], stop=stop)
    handler = _TelegramHandler()
    poller = TelegramLongPoller(
        settings=_telegram_settings(),
        environment=TelegramEnvironment.STAGING,
        session_factory=factory,
        identity_transport=_TelegramIdentity(),
        update_transport=updates,
        handler=handler,
        clock=lambda: NOW,
        random_value=lambda: 0,
        owner="fault-out-of-order",
    )

    asyncio.run(poller.run(AsyncioTelegramPollingControl(stop)))
    with UnitOfWork(factory) as uow:
        state = uow.telegram_updates_repo.get_state("staging")
        assert state is not None
        evidence = FaultEvidence(
            final_state=f"offset:{state.next_offset}",
            audit_or_result=tuple(str(update_id) for update_id in handler.updates),
            external_calls=updates.calls,
            funds_effect=str(handler.funds_effects),
        )

    assert handler.updates == [7]
    assert_fault_evidence(evidence, state="offset:9", calls=2, funds="1")
