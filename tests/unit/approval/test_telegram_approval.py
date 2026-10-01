"""P05-T1 Telegram Paper approval callback tests."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr
from risk.risk_fixtures import make_context
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.service import ApprovalService
from ainvest.approval.telegram import (
    TelegramDeliveryCode,
    TelegramEnvironment,
    TelegramNotificationCategory,
    TelegramNotificationOutcome,
    TelegramNotificationRequest,
)
from ainvest.approval.telegram_approval import (
    TelegramApprovalCode,
    TelegramPaperApprovalHandler,
    bind_telegram_approval_delivery,
)
from ainvest.approval.telegram_updates import (
    AuthorizedCallbackUpdate,
    AuthorizedTextUpdate,
    TelegramHandlerDisposition,
)
from ainvest.approval.tokens import OpaqueApprovalToken
from ainvest.data.calendar_port import FakeMarketCalendar
from ainvest.db.models import ApprovalEventRow, ApprovalOutboxRow, AuditEventRow
from ainvest.db.repositories import ApprovalRepository
from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.uow import UnitOfWork
from ainvest.risk.engine import evaluate_risk
from ainvest.risk.models import ExposureInputs, SectorAssignment
from ainvest.schemas.approval import ApprovalMethod, ApprovalScope
from ainvest.schemas.examples import candidate_order_example, portfolio_snapshot_example
from ainvest.schemas.orders import CandidateOrder
from ainvest.schemas.portfolio import PortfolioSnapshot

NOW = datetime(2026, 7, 24, 18, 30, 12, tzinfo=UTC)
TOKEN_VALUE = "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg"
TOKEN = OpaqueApprovalToken(TOKEN_VALUE)


def _factory(path: Path) -> sessionmaker[Session]:
    engine = create_db_engine(f"sqlite+pysqlite:///{path}", connect_args={"timeout": 10})
    create_all_tables(engine)
    return create_session_factory(engine)


def _issue(factory: sessionmaker[Session], *, now: datetime = NOW) -> None:
    payload = candidate_order_example()
    payload["account_scope"] = "paper"
    payload["created_at"] = now.isoformat()
    payload["expires_at"] = (now + timedelta(minutes=5)).isoformat()
    candidate = CandidateOrder.model_validate(payload)
    context = make_context(
        risk_decision_id="risk_01HZYTGAPPROVAL01",
        as_of=now,
        candidate=candidate,
        portfolio=PortfolioSnapshot.model_validate(portfolio_snapshot_example()),
        exposure_inputs=ExposureInputs(
            sectors=(SectorAssignment(instrument_id=candidate.instrument_id, sector="TECH"),),
            daily_turnover_to_date=Decimal("100"),
            daily_realized_pnl=Decimal("0"),
            daily_unrealized_pnl=Decimal("0"),
        ),
    )
    limits = context.config.exposure.model_copy(update={"max_symbol_weight": Decimal("0.60")})
    context = context.model_copy(
        update={"config": context.config.model_copy(update={"exposure": limits})}
    )
    risk = evaluate_risk(context, calendar=FakeMarketCalendar())
    with UnitOfWork(factory) as uow:
        issued = ApprovalService.from_uow(
            uow,
            clock=lambda: now,
            id_factory=lambda prefix: {
                "ordp": "ordp_01HZYTGAPPROVAL01",
                "apch": "apch_01HZYTGAPPROVAL01",
                "apev": "apev_unused",
            }[prefix],
            token_factory=lambda: TOKEN,
        ).create_proposal_and_challenge(
            candidate,
            risk,
            risk_context=context,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
        )
    with UnitOfWork(factory) as uow:
        request = TelegramNotificationRequest(
            environment=TelegramEnvironment.STAGING,
            category=TelegramNotificationCategory.PAPER,
            intent_correlation_id="notify_01HZYTGAPPROVAL01",
            recipient_user_id=101,
            recipient_private_chat_id=202,
            proposal=issued.proposal,
            risk_decision=risk.decision,
            expires_at=issued.challenge.expires_at,
            paper_nonce=TOKEN,
        )
        outcome = TelegramNotificationOutcome(
            code=TelegramDeliveryCode.SENT,
            retryable=False,
            environment=TelegramEnvironment.STAGING,
            intent_correlation_id=request.intent_correlation_id,
            telegram_message_id=303,
        )
        bind_telegram_approval_delivery(
            uow,
            challenge_id=issued.challenge.challenge_id,
            request=request,
            outcome=outcome,
            bound_at=now,
        )


def _ids(prefix: str) -> str:
    return {
        "apev": "apev_01HZYTGAPPROVAL01",
        "audit": "audit_01HZYTGAPPROVAL1",
        "apob": "apob_01HZYTGAPPROVAL01",
    }[prefix]


def _update(**changes: object) -> AuthorizedCallbackUpdate:
    values: dict[str, object] = {
        "environment": TelegramEnvironment.STAGING,
        "update_id": 404,
        "sender_user_id": 101,
        "chat_id": 202,
        "message_id": 303,
        "chat_type": "private",
        "forwarded": False,
        "callback_query_id": SecretStr("callback-01"),
        "callback_data": SecretStr(TOKEN_VALUE),
    }
    values.update(changes)
    return AuthorizedCallbackUpdate.model_validate(values)


@pytest.mark.unit
def test_valid_callback_atomically_creates_one_paper_event_audit_and_outbox(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path / "valid.db")
    _issue(factory)
    handler = TelegramPaperApprovalHandler(
        factory, clock=lambda: NOW + timedelta(seconds=30), id_factory=_ids
    )

    assert handler.process(_update()) is TelegramApprovalCode.APPROVED

    with factory() as session:
        event = session.scalar(select(ApprovalEventRow))
        outbox = session.scalar(select(ApprovalOutboxRow))
        audit = session.scalar(select(AuditEventRow))
        assert event is not None and outbox is not None and audit is not None
        assert (event.method, event.scope, event.outcome) == ("telegram", "paper", "APPROVED")
        assert event.approver_identity == "telegram:101"
        assert outbox.approval_event_id == event.event_id
        assert outbox.status == "PENDING"
        persisted = f"{event.payload_json}{outbox.payload_json}{audit.payload_json}"
        assert TOKEN_VALUE not in persisted


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("environment", TelegramEnvironment.PRODUCTION),
        ("sender_user_id", 999),
        ("chat_id", 999),
        ("message_id", 999),
        ("callback_data", SecretStr("eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXk")),
    ],
)
def test_binding_mismatch_or_tampered_nonce_creates_no_business_rows(
    tmp_path: Path, field: str, value: object
) -> None:
    factory = _factory(tmp_path / f"bad-{field}.db")
    _issue(factory)
    handler = TelegramPaperApprovalHandler(factory, clock=lambda: NOW + timedelta(seconds=30))

    assert handler.process(_update(**{field: value})) is TelegramApprovalCode.INVALID
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalEventRow)) == 0
        assert session.scalar(select(func.count()).select_from(ApprovalOutboxRow)) == 0
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 0


@pytest.mark.unit
def test_expired_callback_has_no_execution_outbox_and_replay_is_terminal(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "expired.db")
    _issue(factory)
    handler = TelegramPaperApprovalHandler(
        factory, clock=lambda: NOW + timedelta(minutes=3), id_factory=_ids
    )

    assert handler.process(_update()) is TelegramApprovalCode.EXPIRED
    assert handler.process(_update()) is TelegramApprovalCode.ALREADY_USED
    with factory() as session:
        event = session.scalar(select(ApprovalEventRow))
        assert event is not None and event.outcome == "EXPIRED"
        assert session.scalar(select(func.count()).select_from(ApprovalOutboxRow)) == 0


@pytest.mark.unit
def test_concurrent_double_click_has_exactly_one_approval(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "race.db")
    _issue(factory)

    def decide(suffix: str) -> TelegramApprovalCode:
        def ids(prefix: str) -> str:
            return f"{prefix}_01HZYTGAPPROVAL{suffix}"

        return TelegramPaperApprovalHandler(
            factory, clock=lambda: NOW + timedelta(seconds=30), id_factory=ids
        ).process(_update(update_id=404 + int(suffix)))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, ("1", "2")))
    assert results.count(TelegramApprovalCode.APPROVED) == 1
    assert results.count(TelegramApprovalCode.ALREADY_USED) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalEventRow)) == 1
        assert session.scalar(select(func.count()).select_from(ApprovalOutboxRow)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 1


@pytest.mark.unit
def test_plain_text_never_approves_and_answer_failure_is_terminal(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "handler.db")
    _issue(factory)

    class FailingAnswer:
        async def answer_callback_query(
            self, callback_query_id: str, text: str, *, timeout_seconds: float
        ) -> None:
            del callback_query_id, text, timeout_seconds
            raise RuntimeError("provider detail must be sanitized")

    handler = TelegramPaperApprovalHandler(
        factory,
        answer_transport=FailingAnswer(),
        clock=lambda: NOW + timedelta(seconds=30),
        id_factory=_ids,
    )
    text = AuthorizedTextUpdate(
        environment=TelegramEnvironment.STAGING,
        update_id=405,
        sender_user_id=101,
        chat_id=202,
        message_id=304,
        text=SecretStr("approve"),
    )
    assert asyncio.run(handler.handle(text)) is TelegramHandlerDisposition.TERMINAL_HANDLED
    assert asyncio.run(handler.handle(_update())) is TelegramHandlerDisposition.TERMINAL_HANDLED


@pytest.mark.unit
def test_outbox_failure_rolls_back_approval_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = _factory(tmp_path / "rollback.db")
    _issue(factory)

    def fail_outbox(self: ApprovalRepository, fields: dict[str, object]) -> object:
        del self, fields
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(ApprovalRepository, "add_outbox", fail_outbox)
    handler = TelegramPaperApprovalHandler(
        factory, clock=lambda: NOW + timedelta(seconds=30), id_factory=_ids
    )
    with pytest.raises(RuntimeError, match="injected persistence failure"):
        handler.process(_update())

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalEventRow)) == 0
        assert session.scalar(select(func.count()).select_from(ApprovalOutboxRow)) == 0
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 0
