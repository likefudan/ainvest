"""P05-T8 Gate 3: Telegram may authorize one Paper fill and nothing Live."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import SecretStr
from risk.risk_fixtures import make_context
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.handoff import (
    ApprovalExecutionResult,
    ApprovalHandoffCode,
    ApprovalHandoffService,
    PaperExecutionHandoff,
)
from ainvest.approval.order_hash import parse_order_proposal
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
from ainvest.approval.telegram_updates import AuthorizedCallbackUpdate
from ainvest.approval.tokens import OpaqueApprovalToken
from ainvest.data.calendar_port import FakeMarketCalendar
from ainvest.db.models import ApprovalEventRow, ApprovalOutboxRow, AuditEventRow, OrderProposalRow
from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.uow import UnitOfWork
from ainvest.execution.broker import BrokerSubmitOutcome, BrokerSubmitRequest
from ainvest.execution.paper import PaperBroker, PaperCostModel, PaperMarketEvent
from ainvest.orchestrator.approval_handoff import WorkflowExecutionHandoff
from ainvest.risk.engine import evaluate_risk
from ainvest.risk.models import ExposureInputs, SectorAssignment
from ainvest.schemas.approval import ApprovalEvent, ApprovalMethod, ApprovalScope
from ainvest.schemas.broker import BrokerOrderStatus
from ainvest.schemas.examples import candidate_order_example, portfolio_snapshot_example
from ainvest.schemas.orders import CandidateOrder
from ainvest.schemas.portfolio import AccountScope, PortfolioSnapshot
from ainvest.workflow.commands import ExecuteOrderCommand, WorkflowCommand
from ainvest.workflow.dispatcher import InProcessCommandDispatcher
from ainvest.workflow.events import CommandOutcome, OrderExecutedEvent, WorkflowEvent
from ainvest.workflow.semantics import CommandType

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 24, 18, 30, 12, tzinfo=UTC)
TOKEN_VALUE = "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg"
TOKEN = OpaqueApprovalToken(TOKEN_VALUE)


def _factory(path: Path) -> sessionmaker[Session]:
    engine = create_db_engine(f"sqlite+pysqlite:///{path}", connect_args={"timeout": 10})
    create_all_tables(engine)
    return create_session_factory(engine)


def _issue(factory: sessionmaker[Session]) -> None:
    payload = candidate_order_example()
    payload.update(
        {
            "account_scope": "paper",
            "created_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        }
    )
    candidate = CandidateOrder.model_validate(payload)
    context = make_context(
        risk_decision_id="risk_01HZYGATE3APPROVAL",
        as_of=NOW,
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
            clock=lambda: NOW,
            id_factory=lambda prefix: {
                "ordp": "ordp_01HZYGATE3APPROVAL",
                "apch": "apch_01HZYGATE3APPROVAL",
                "apev": "apev_gate3_unused",
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
            intent_correlation_id="notify_01HZYGATE3APPROVAL",
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
            bound_at=NOW,
        )


def _ids(prefix: str) -> str:
    return {
        "apev": "apev_01HZYGATE3APPROVAL",
        "audit": "audit_01HZYGATE3APPROVAL",
        "apob": "apob_01HZYGATE3APPROVAL",
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
        "callback_query_id": SecretStr("gate3-callback-01"),
        "callback_data": SecretStr(TOKEN_VALUE),
    }
    values.update(changes)
    return AuthorizedCallbackUpdate.model_validate(values)


class _PaperFillHandler:
    """The Gate 3 execution boundary: exactly one injected Paper fill."""

    def __init__(self, *, proposal: object, approval: ApprovalEvent) -> None:
        self.proposal = parse_order_proposal(cast(dict[str, object], proposal))
        self.approval = approval
        self.calls = 0
        self.now = NOW + timedelta(seconds=40)
        self.broker = PaperBroker(
            cost_model=PaperCostModel(
                fee_bps=Decimal("0"),
                half_spread_bps=Decimal("0"),
                slippage_bps=Decimal("0"),
            ),
            clock=lambda: self.now,
            initial_cash=Decimal("100000"),
        )

    def __call__(self, command: WorkflowCommand) -> WorkflowEvent:
        execute = cast(ExecuteOrderCommand, command)
        self.calls += 1
        assert execute.proposal_id == self.proposal.proposal_id
        assert execute.order_hash == self.proposal.order_hash
        assert execute.approval_event_id == self.approval.event_id
        submitted = self.broker.submit(
            BrokerSubmitRequest(
                proposal=self.proposal,
                approval=self.approval,
                client_order_id=execute.client_order_id,
            )
        )
        assert submitted.outcome is BrokerSubmitOutcome.ACCEPTED
        assert submitted.broker_order is not None
        limit = self.proposal.limit_price
        self.now += timedelta(seconds=1)
        self.broker.inject_market_event(
            PaperMarketEvent(
                event_id="market_gate3_fill_01",
                instrument_id=self.proposal.instrument_id,
                bid=limit - Decimal("0.02"),
                ask=limit - Decimal("0.01"),
                last=limit - Decimal("0.01"),
                liquidity=self.proposal.quantity,
                observed_at=self.now,
            )
        )
        order = self.broker.get_orders(AccountScope.PAPER)[0]
        assert order.status is BrokerOrderStatus.FILLED
        return OrderExecutedEvent(
            event_id="evt_gate3_paper_fill_01",
            correlation_id=execute.correlation_id,
            causation_id=execute.command_id,
            idempotency_id=execute.idempotency_id,
            occurred_at=self.now,
            outcome=CommandOutcome.SUCCEEDED,
            proposal_id=execute.proposal_id,
            client_order_id=execute.client_order_id,
            broker_order_id=order.broker_order_id,
        )


def _trusted_rows(
    factory: sessionmaker[Session],
) -> tuple[dict[str, object], ApprovalEvent, str]:
    with factory() as session:
        proposal_row = session.scalar(select(OrderProposalRow))
        event_row = session.scalar(select(ApprovalEventRow))
        outbox_row = session.scalar(select(ApprovalOutboxRow))
        assert proposal_row is not None and event_row is not None and outbox_row is not None
        return (
            dict(proposal_row.payload_json),
            ApprovalEvent.model_validate(dict(event_row.payload_json)),
            outbox_row.outbox_id,
        )


@pytest.mark.integration
def test_private_callback_reaches_one_durable_paper_fill_and_replays_safely(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path / "gate3-paper-fill.db")
    _issue(factory)
    approval_handler = TelegramPaperApprovalHandler(
        factory,
        clock=lambda: NOW + timedelta(seconds=30),
        id_factory=_ids,
    )

    assert approval_handler.process(_update()) is TelegramApprovalCode.APPROVED
    proposal_payload, approval, outbox_id = _trusted_rows(factory)
    paper = _PaperFillHandler(proposal=proposal_payload, approval=approval)
    dispatcher = InProcessCommandDispatcher()
    dispatcher.register(CommandType.EXECUTE_ORDER, paper)
    handoff = ApprovalHandoffService(
        factory,
        WorkflowExecutionHandoff(dispatcher),
        clock=lambda: NOW + timedelta(seconds=40),
    )

    first = handoff.consume(outbox_id)
    replay = handoff.consume(outbox_id)

    assert first.code is ApprovalHandoffCode.DISPATCHED
    assert replay.code is ApprovalHandoffCode.ALREADY_CONSUMED
    assert paper.calls == 1
    assert len(paper.broker.get_orders(AccountScope.PAPER)) == 1
    assert len(paper.broker.get_fills(AccountScope.PAPER)) == 1
    with factory() as session:
        event = session.scalar(select(ApprovalEventRow))
        outbox = session.scalar(select(ApprovalOutboxRow))
        audits = tuple(session.scalars(select(AuditEventRow)))
        assert event is not None and outbox is not None
        assert (event.method, event.scope, event.outcome) == ("telegram", "paper", "APPROVED")
        assert outbox.status == "CONSUMED"
        persisted = (
            f"{event.payload_json}{outbox.payload_json}{[row.payload_json for row in audits]}"
        )
        assert TOKEN_VALUE not in persisted
        assert "synthetic-token" not in persisted


@pytest.mark.integration
def test_forged_telegram_live_scope_is_consumed_without_execution(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path / "gate3-live-forgery.db")
    _issue(factory)
    handler = TelegramPaperApprovalHandler(
        factory,
        clock=lambda: NOW + timedelta(seconds=30),
        id_factory=_ids,
    )
    assert handler.process(_update()) is TelegramApprovalCode.APPROVED
    _proposal_payload, _approval, outbox_id = _trusted_rows(factory)
    with factory.begin() as session:
        event = session.scalar(select(ApprovalEventRow))
        assert event is not None
        event.scope = "live"
        event.payload_json = {**event.payload_json, "scope": "live"}

    class _ForbiddenExecution:
        calls = 0

        def dispatch(self, request: PaperExecutionHandoff) -> ApprovalExecutionResult:
            del request
            self.calls += 1
            raise AssertionError("telegram+live reached execution")

    execution = _ForbiddenExecution()
    result = ApprovalHandoffService(
        factory,
        execution,
        clock=lambda: NOW + timedelta(seconds=40),
    ).consume(outbox_id)

    assert result.code is ApprovalHandoffCode.POLICY_REJECTED
    assert execution.calls == 0
    with factory() as session:
        assert session.scalar(select(ApprovalOutboxRow.status)) == "CONSUMED"
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 2


@pytest.mark.integration
def test_gate3_composition_has_no_public_route_or_robinhood_write_path() -> None:
    modules = (
        TelegramPaperApprovalHandler,
        ApprovalHandoffService,
        WorkflowExecutionHandoff,
    )
    source = "\n".join(inspect.getsource(module) for module in modules).casefold()
    forbidden = (
        "fastapi",
        "@app.post",
        "@router.post",
        "rh_mcp",
        "robinhood",
        "liveexecution",
    )

    assert all(term not in source for term in forbidden)
    paper_submit_source = inspect.getsource(PaperBroker.submit).casefold()
    assert "account_scope is not accountscope.paper" in paper_submit_source
    assert not (ROOT / "src/ainvest/approval/routes.py").exists()
