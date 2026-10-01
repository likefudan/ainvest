"""One-shot staging Telegram -> durable approval -> Paper fill rehearsal.

This composition root deliberately has no Robinhood or live-execution imports.
It owns the staging poller for one bounded run, creates one synthetic Paper
proposal, waits for its exact private Telegram callback, and proves idempotent
handoff into an in-memory PaperBroker fill.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sqlite3
import stat
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final, Never, cast
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.handoff import ApprovalHandoffCode, ApprovalHandoffService
from ainvest.approval.order_hash import parse_order_proposal
from ainvest.approval.service import ApprovalService
from ainvest.approval.telegram import (
    TelegramDeliveryCode,
    TelegramEnvironment,
    TelegramHttpsTransport,
    TelegramNotificationCategory,
    TelegramNotificationRequest,
    TelegramNotificationSender,
    TelegramTransport,
)
from ainvest.approval.telegram_approval import (
    TelegramCallbackAnswerTransport,
    TelegramPaperApprovalHandler,
    bind_telegram_approval_delivery,
)
from ainvest.approval.telegram_updates import (
    AsyncioTelegramPollingControl,
    AuthorizedCallbackUpdate,
    AuthorizedTelegramUpdate,
    TelegramHandlerDisposition,
    TelegramHttpsUpdateTransport,
    TelegramIdentityTransport,
    TelegramLongPoller,
    TelegramUpdateTransport,
)
from ainvest.config import AinvestEnv, Settings, TradingMode, load_settings
from ainvest.config.errors import ConfigError
from ainvest.data.calendar_port import SessionStatus
from ainvest.db import UnitOfWork, create_db_engine, create_session_factory
from ainvest.db.models import ApprovalEventRow, ApprovalOutboxRow, OrderProposalRow
from ainvest.execution.broker import BrokerSubmitOutcome, BrokerSubmitRequest
from ainvest.execution.paper import PaperBroker, PaperCostModel, PaperMarketEvent
from ainvest.orchestrator.approval_handoff import WorkflowExecutionHandoff
from ainvest.orchestrator.fixtures import (
    make_cash_portfolio,
    make_exposure_inputs,
    make_instrument,
    make_quote,
    make_risk_config,
)
from ainvest.risk import (
    EvaluationPhase,
    KillSwitchSnapshot,
    RiskContext,
    RiskEngineOutput,
    evaluate_risk,
)
from ainvest.schemas.approval import ApprovalEvent, ApprovalMethod, ApprovalScope
from ainvest.schemas.broker import BrokerOrderStatus
from ainvest.schemas.common import ensure_utc
from ainvest.schemas.examples import candidate_order_example
from ainvest.schemas.orders import CandidateOrder, OrderProposal
from ainvest.schemas.portfolio import AccountScope
from ainvest.workflow.commands import ExecuteOrderCommand, WorkflowCommand
from ainvest.workflow.dispatcher import InProcessCommandDispatcher
from ainvest.workflow.events import CommandOutcome, OrderExecutedEvent, WorkflowEvent
from ainvest.workflow.semantics import CommandType

_ALEMBIC_HEAD: Final[str] = "5ce8169131f2"
_REQUIRED_TABLES: Final[frozenset[str]] = frozenset(
    {
        "alembic_version",
        "approval_challenges",
        "approval_events",
        "approval_outbox",
        "audit_events",
        "order_proposals",
        "risk_decisions",
        "telegram_approval_bindings",
        "telegram_poll_states",
        "telegram_processed_updates",
    }
)
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 100.0
_MIN_TIMEOUT_SECONDS: Final[float] = 60.0
_MAX_TIMEOUT_SECONDS: Final[float] = 110.0

Clock = Callable[[], datetime]


class Gate3RehearsalFailure(Exception):
    """Sanitized, fail-closed rehearsal failure."""


class _RunnerParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        raise Gate3RehearsalFailure("invalid_cli_input") from None


class _AlwaysOpenSyntheticCalendar:
    """Synthetic-only calendar used solely to approve the rehearsal fixture."""

    def session_status(self, moment: datetime, *, exchange: str = "XNYS") -> SessionStatus:
        del moment, exchange
        return SessionStatus.OPEN

    def is_regular_session_open(self, moment: datetime, *, exchange: str = "XNYS") -> bool:
        del moment, exchange
        return True


@dataclass(frozen=True, slots=True)
class Gate3RehearsalResult:
    proposal_id: str
    approval_event_id: str
    paper_broker_order_id: str
    replay_blocked: bool


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def _bounded_timeout(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError from None
    if not _MIN_TIMEOUT_SECONDS <= parsed <= _MAX_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = _RunnerParser(
        prog="ainvest-gate3-rehearsal",
        description="Run one bounded staging Telegram approval into a Paper-only fill.",
    )
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--secrets-dir", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--timeout-seconds",
        type=_bounded_timeout,
        default=_DEFAULT_TIMEOUT_SECONDS,
    )
    parser.add_argument("--confirm-poller-stopped", required=True, action="store_true")
    return parser


def _require_runtime(settings: Settings) -> None:
    config = settings.telegram_staging
    if settings.ainvest_env is not AinvestEnv.STAGING:
        raise Gate3RehearsalFailure("staging_environment_required")
    if settings.trading_mode is not TradingMode.PAPER or settings.live_trading_enabled:
        raise Gate3RehearsalFailure("paper_mode_required")
    if (
        not config.enabled
        or config.bot_token is None
        or config.expected_bot_id is None
        or len(config.allowed_recipients) != 1
    ):
        raise Gate3RehearsalFailure("telegram_configuration_incomplete_or_ambiguous")


def _require_migrated_sqlite(path: Path) -> None:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise Gate3RehearsalFailure("database_invalid")
        uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            revisions = {
                row[0] for row in connection.execute("SELECT version_num FROM alembic_version")
            }
    except Gate3RehearsalFailure:
        raise
    except Exception:
        raise Gate3RehearsalFailure("database_invalid") from None
    if not tables >= _REQUIRED_TABLES or revisions != {_ALEMBIC_HEAD}:
        raise Gate3RehearsalFailure("database_migration_required")


def _synthetic_risk(now: datetime) -> tuple[CandidateOrder, RiskEngineOutput, RiskContext]:
    payload = candidate_order_example()
    payload.update(
        {
            "candidate_id": _new_id("cand"),
            "signal_id": _new_id("sig"),
            "account_scope": AccountScope.PAPER.value,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
        }
    )
    candidate = CandidateOrder.model_validate(payload)
    timestamp = now.isoformat()
    quote = make_quote(
        instrument={
            "instrument_id": candidate.instrument_id,
            "symbol": candidate.symbol,
            "exchange": candidate.exchange,
            "currency": candidate.currency,
            "asset_type": candidate.asset_type.value,
            "identity_as_of": timestamp,
        },
        provenance={
            "source": "ainvest.gate3.synthetic",
            "observed_at": timestamp,
            "received_at": timestamp,
            "timezone": "UTC",
            "is_delayed": False,
            "quality_flags": [],
        },
    )
    context = RiskContext(
        risk_decision_id=_new_id("risk"),
        phase=EvaluationPhase.PROPOSAL,
        as_of=now,
        candidate=candidate,
        quote=quote,
        instrument=make_instrument(),
        config=make_risk_config(),
        portfolio=make_cash_portfolio(as_of=timestamp),
        short_term_volatility_bps=Decimal("0"),
        exposure_inputs=make_exposure_inputs(),
        kill_switch=KillSwitchSnapshot(),
    )
    risk = evaluate_risk(context, calendar=_AlwaysOpenSyntheticCalendar())
    return candidate, risk, context


class _StopOnExpectedApproval:
    def __init__(
        self,
        inner: TelegramPaperApprovalHandler,
        session_factory: sessionmaker[Session],
        proposal_id: str,
        stop: asyncio.Event,
    ) -> None:
        self._inner = inner
        self._session_factory = session_factory
        self._proposal_id = proposal_id
        self._stop = stop
        self.outbox_id: str | None = None
        self.approval_event_id: str | None = None

    async def handle(self, update: AuthorizedTelegramUpdate) -> TelegramHandlerDisposition:
        disposition = await self._inner.handle(update)
        if isinstance(update, AuthorizedCallbackUpdate):
            with self._session_factory() as session:
                outbox = session.scalar(
                    select(ApprovalOutboxRow).where(
                        ApprovalOutboxRow.proposal_id == self._proposal_id
                    )
                )
                if outbox is not None:
                    self.outbox_id = outbox.outbox_id
                    self.approval_event_id = outbox.approval_event_id
                    self._stop.set()
        return disposition


class _PaperFillHandler:
    """Exact execution boundary: one accepted order and one injected Paper fill."""

    def __init__(self, *, proposal: OrderProposal, approval: ApprovalEvent, now: datetime) -> None:
        self.proposal = proposal
        self.approval = approval
        self.calls = 0
        self._now = now
        self.broker = PaperBroker(
            cost_model=PaperCostModel(
                fee_bps=Decimal("0"),
                half_spread_bps=Decimal("0"),
                slippage_bps=Decimal("0"),
            ),
            clock=lambda: self._now,
            initial_cash=Decimal("100000"),
        )

    def __call__(self, command: WorkflowCommand) -> WorkflowEvent:
        execute = cast(ExecuteOrderCommand, command)
        self.calls += 1
        if (
            execute.proposal_id != self.proposal.proposal_id
            or execute.order_hash != self.proposal.order_hash
            or execute.approval_event_id != self.approval.event_id
        ):
            raise Gate3RehearsalFailure("paper_binding_failed")
        submitted = self.broker.submit(
            BrokerSubmitRequest(
                proposal=self.proposal,
                approval=self.approval,
                client_order_id=execute.client_order_id,
            )
        )
        if submitted.outcome is not BrokerSubmitOutcome.ACCEPTED:
            raise Gate3RehearsalFailure("paper_submit_failed")
        self._now += timedelta(seconds=1)
        limit = self.proposal.limit_price
        self.broker.inject_market_event(
            PaperMarketEvent(
                event_id=_new_id("market"),
                instrument_id=self.proposal.instrument_id,
                bid=limit - Decimal("0.02"),
                ask=limit - Decimal("0.01"),
                last=limit - Decimal("0.01"),
                liquidity=self.proposal.quantity,
                observed_at=self._now,
            )
        )
        order = self.broker.get_orders(AccountScope.PAPER)[0]
        if order.status is not BrokerOrderStatus.FILLED:
            raise Gate3RehearsalFailure("paper_fill_failed")
        return OrderExecutedEvent(
            event_id=_new_id("evt"),
            correlation_id=execute.correlation_id,
            causation_id=execute.command_id,
            idempotency_id=execute.idempotency_id,
            occurred_at=self._now,
            outcome=CommandOutcome.SUCCEEDED,
            proposal_id=execute.proposal_id,
            client_order_id=execute.client_order_id,
            broker_order_id=order.broker_order_id,
        )


def _load_trusted_execution_records(
    session_factory: sessionmaker[Session], proposal_id: str, approval_event_id: str
) -> tuple[OrderProposal, ApprovalEvent]:
    with session_factory() as session:
        proposal_row = session.scalar(
            select(OrderProposalRow).where(OrderProposalRow.proposal_id == proposal_id)
        )
        event_row = session.scalar(
            select(ApprovalEventRow).where(ApprovalEventRow.event_id == approval_event_id)
        )
    if proposal_row is None or event_row is None:
        raise Gate3RehearsalFailure("trusted_record_missing")
    return (
        parse_order_proposal(dict(proposal_row.payload_json)),
        ApprovalEvent.model_validate(dict(event_row.payload_json)),
    )


async def run_gate3_rehearsal(
    *,
    settings: Settings,
    session_factory: sessionmaker[Session],
    notification_transport: TelegramTransport,
    identity_transport: TelegramIdentityTransport,
    update_transport: TelegramUpdateTransport,
    answer_transport: TelegramCallbackAnswerTransport,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    clock: Clock = _utc_now,
) -> Gate3RehearsalResult:
    """Run one real callback through the approved Paper-only composition."""
    _require_runtime(settings)
    if not _MIN_TIMEOUT_SECONDS <= timeout_seconds <= _MAX_TIMEOUT_SECONDS:
        raise Gate3RehearsalFailure("timeout_invalid")
    now = ensure_utc(clock())
    candidate, risk, risk_context = _synthetic_risk(now)
    with UnitOfWork(session_factory) as uow:
        issued = ApprovalService.from_uow(uow, clock=lambda: now).create_proposal_and_challenge(
            candidate,
            risk,
            risk_context=risk_context,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
            ttl=timedelta(seconds=timeout_seconds),
        )

    recipient = settings.telegram_staging.allowed_recipients[0]
    request = TelegramNotificationRequest(
        environment=TelegramEnvironment.STAGING,
        category=TelegramNotificationCategory.PAPER,
        intent_correlation_id=_new_id("notify"),
        recipient_user_id=recipient.user_id,
        recipient_private_chat_id=recipient.private_chat_id,
        proposal=issued.proposal,
        risk_decision=risk.decision,
        expires_at=issued.challenge.expires_at,
        paper_nonce=issued.token,
    )
    outcome = await TelegramNotificationSender(settings, notification_transport).send(request)
    if outcome.code is not TelegramDeliveryCode.SENT:
        raise Gate3RehearsalFailure("telegram_delivery_failed")
    with UnitOfWork(session_factory) as uow:
        bind_telegram_approval_delivery(
            uow,
            challenge_id=issued.challenge.challenge_id,
            request=request,
            outcome=outcome,
            bound_at=now,
        )

    stop = asyncio.Event()
    handler = _StopOnExpectedApproval(
        TelegramPaperApprovalHandler(
            session_factory,
            answer_transport=answer_transport,
            clock=clock,
        ),
        session_factory,
        issued.proposal.proposal_id,
        stop,
    )
    poller = TelegramLongPoller(
        settings=settings,
        environment=TelegramEnvironment.STAGING,
        session_factory=session_factory,
        identity_transport=identity_transport,
        update_transport=update_transport,
        handler=handler,
        owner=_new_id("gate3")[:64],
    )
    task = asyncio.create_task(poller.run(AsyncioTelegramPollingControl(stop)))
    stopped = asyncio.create_task(stop.wait())
    try:
        done, _pending = await asyncio.wait(
            {task, stopped},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise Gate3RehearsalFailure("approval_timeout")
        if task in done:
            try:
                await task
            except asyncio.CancelledError:
                raise
            except Exception:
                raise Gate3RehearsalFailure("poller_failed") from None
            if not stop.is_set():
                raise Gate3RehearsalFailure("poller_stopped_before_approval")
        else:
            try:
                await task
            except asyncio.CancelledError:
                raise
            except Exception:
                raise Gate3RehearsalFailure("poller_failed") from None
    finally:
        stop.set()
        if not stopped.done():
            stopped.cancel()
            await asyncio.gather(stopped, return_exceptions=True)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    if handler.outbox_id is None or handler.approval_event_id is None:
        raise Gate3RehearsalFailure("approval_not_recorded")
    proposal, approval = _load_trusted_execution_records(
        session_factory, issued.proposal.proposal_id, handler.approval_event_id
    )
    paper = _PaperFillHandler(proposal=proposal, approval=approval, now=ensure_utc(clock()))
    dispatcher = InProcessCommandDispatcher()
    dispatcher.register(CommandType.EXECUTE_ORDER, paper)
    handoff = ApprovalHandoffService(
        session_factory,
        WorkflowExecutionHandoff(dispatcher),
        clock=clock,
    )
    first = handoff.consume(handler.outbox_id)
    replay = handoff.consume(handler.outbox_id)
    orders = paper.broker.get_orders(AccountScope.PAPER)
    fills = paper.broker.get_fills(AccountScope.PAPER)
    if (
        first.code is not ApprovalHandoffCode.DISPATCHED
        or replay.code is not ApprovalHandoffCode.ALREADY_CONSUMED
        or paper.calls != 1
        or len(orders) != 1
        or len(fills) != 1
        or orders[0].status is not BrokerOrderStatus.FILLED
    ):
        raise Gate3RehearsalFailure("idempotency_proof_failed")
    return Gate3RehearsalResult(
        proposal_id=proposal.proposal_id,
        approval_event_id=approval.event_id,
        paper_broker_order_id=orders[0].broker_order_id,
        replay_blocked=True,
    )


async def _run_cli(namespace: argparse.Namespace) -> Gate3RehearsalResult:
    try:
        settings = load_settings(env_file=namespace.env_file, secrets_dir=namespace.secrets_dir)
    except ConfigError:
        raise Gate3RehearsalFailure("configuration_invalid") from None
    _require_runtime(settings)
    _require_migrated_sqlite(namespace.database)
    config = settings.telegram_staging
    assert config.bot_token is not None
    token = config.bot_token.get_secret_value()
    engine = create_db_engine(f"sqlite+pysqlite:///{namespace.database.resolve()}")
    session_factory = create_session_factory(engine)
    try:
        async with TelegramHttpsTransport(token) as transport:
            return await run_gate3_rehearsal(
                settings=settings,
                session_factory=session_factory,
                notification_transport=transport,
                identity_transport=transport,
                update_transport=TelegramHttpsUpdateTransport(transport),
                answer_transport=transport,
                timeout_seconds=namespace.timeout_seconds,
            )
    finally:
        token = ""
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        namespace = build_parser().parse_args(raw)
        result = asyncio.run(_run_cli(namespace))
        sys.stdout.write(
            json.dumps(
                {
                    "command": "gate3_rehearsal",
                    "environment": "staging",
                    "status": "ok",
                    "proposal_id": result.proposal_id,
                    "approval_event_id": result.approval_event_id,
                    "paper_broker_order_id": result.paper_broker_order_id,
                    "terminal": "FILLED",
                    "replay_blocked": result.replay_blocked,
                },
                separators=(",", ":"),
            )
            + "\n"
        )
    except (Gate3RehearsalFailure, KeyboardInterrupt):
        sys.stderr.write('{"code":"gate3_rehearsal_failed","status":"error"}\n')
        return 1
    except Exception:
        sys.stderr.write('{"code":"gate3_rehearsal_failed","status":"error"}\n')
        return 1
    return 0


__all__ = [
    "Gate3RehearsalFailure",
    "Gate3RehearsalResult",
    "build_parser",
    "main",
    "run_gate3_rehearsal",
]
