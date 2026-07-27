"""Deterministic full paper flow orchestration (P03-T16).

Composition root: strategy worker → aggregate → size → risk → proposal →
explicit approval stub → pre-trade → PaperBroker submit → fill → reconcile.
Never auto-approves; broker writes go through ``InProcessCommandDispatcher``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from ainvest.approval import attach_order_hash, parse_order_proposal
from ainvest.audit.digests import digest_json
from ainvest.data import FakeMarketCalendar
from ainvest.execution import (
    BrokerSubmitOutcome,
    BrokerSubmitRequest,
    BrokerSubmitResult,
    BrokerUnknownOutcomeError,
    BrokerWritePort,
    InMemoryStatePersistence,
    LocalOrderExpectation,
    OrderLifecycleState,
    OrderReconciler,
    PaperBroker,
    PaperCostModel,
    PaperMarketEvent,
    transition_order,
)
from ainvest.execution.paper import as_write_port
from ainvest.orchestrator.approval_stub import (
    ApprovalStubStore,
    challenge_fingerprint,
    consume_challenge,
    create_challenge,
)
from ainvest.orchestrator.types import (
    DEFAULT_APPROVAL_TTL,
    DEFAULT_AS_OF,
    FIXED_APPROVAL_EVENT_ID,
    FIXED_CANDIDATE_ID,
    FIXED_CHALLENGE_ID,
    FIXED_CLIENT_ORDER_ID,
    FIXED_CORRELATION_ID,
    FIXED_EXECUTE_COMMAND_ID,
    FIXED_EXECUTE_IDEMPOTENCY_ID,
    FIXED_OPENING_CASH,
    FIXED_PRETRADE_RISK_ID,
    FIXED_PROPOSAL_ID,
    FIXED_PROPOSAL_RISK_ID,
    FIXED_RECONCILE_COMMAND_ID,
    FIXED_RECONCILE_IDEMPOTENCY_ID,
    FIXED_RECONCILIATION_ID,
    PaperFlowResult,
    PaperFlowTerminal,
    StepRecord,
)
from ainvest.portfolio import (
    PortfolioLedger,
    SizingConfig,
    aggregate_signals,
    selected_signals,
    size_position,
)
from ainvest.risk import (
    EvaluationPhase,
    ExposureInputs,
    InstrumentMetadata,
    KillSwitchSnapshot,
    PretradeRequest,
    RiskContext,
    RiskRuleConfig,
    SectorAssignment,
    evaluate_pretrade,
    evaluate_risk,
)
from ainvest.schemas.approval import ApprovalEventOutcome
from ainvest.schemas.broker import BrokerFill, BrokerOrderStatus, ReconciliationOutcome
from ainvest.schemas.common import InstrumentIdentity, canonicalize_decimal, ensure_utc
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.orders import CandidateOrder, OrderProposal
from ainvest.schemas.portfolio import AccountScope, PortfolioSnapshot
from ainvest.schemas.risk import RiskOutcome
from ainvest.schemas.strategy import StrategyContext, TradeSignal
from ainvest.strategies import StrategyDefinition, evaluate_in_worker
from ainvest.strategies.reference.moving_average.plugin import METADATA as MA_METADATA
from ainvest.strategies.reference.moving_average.strategy import (
    MovingAverageParams,
    MovingAverageStrategy,
)
from ainvest.workflow import (
    BlindBrokerRetryError,
    CommandOutcome,
    CommandType,
    ExecuteOrderCommand,
    InProcessCommandDispatcher,
    OrderExecutedEvent,
    ReconcileCommand,
    ReconciledEvent,
    WorkflowCommand,
    ensure_not_blind_broker_retry,
)

BPS_DENOM = Decimal("10000")
_MONEY_QUANT = Decimal("0.000001")


@dataclass(slots=True)
class PaperFlowConfig:
    """Inputs and knobs for one replayable paper-flow run."""

    context: StrategyContext
    quote: MarketQuote
    portfolio: PortfolioSnapshot
    risk_config: RiskRuleConfig
    sizing_config: SizingConfig
    instrument: InstrumentMetadata
    as_of: datetime = DEFAULT_AS_OF
    inject_approval: bool = False
    expire_approval: bool = False
    approval_ttl: timedelta = DEFAULT_APPROVAL_TTL
    market_liquidity: Decimal = Decimal("100")
    opening_cash: Decimal = FIXED_OPENING_CASH
    cost_model: PaperCostModel | None = None
    exposure_inputs: ExposureInputs | None = None
    short_term_volatility_bps: Decimal = Decimal("10")
    write_port: BrokerWritePort | None = None
    raise_unknown_on_submit: bool = False
    strategy_params: Mapping[str, Any] | None = None
    correlation_id: str = FIXED_CORRELATION_ID
    candidate_id: str = FIXED_CANDIDATE_ID
    proposal_id: str = FIXED_PROPOSAL_ID
    proposal_risk_id: str = FIXED_PROPOSAL_RISK_ID
    pretrade_risk_id: str = FIXED_PRETRADE_RISK_ID
    challenge_id: str = FIXED_CHALLENGE_ID
    approval_event_id: str = FIXED_APPROVAL_EVENT_ID
    client_order_id: str = FIXED_CLIENT_ORDER_ID
    reconciliation_id: str = FIXED_RECONCILIATION_ID


@dataclass
class _FlowState:
    steps: list[StepRecord] = field(default_factory=list)
    digests: dict[str, str] = field(default_factory=dict)
    lifecycle: OrderLifecycleState = OrderLifecycleState.SIGNAL_CREATED
    persistence: InMemoryStatePersistence = field(default_factory=InMemoryStatePersistence)
    event_seq: int = 0
    prior_broker_outcome: CommandOutcome | None = None


class _FixedPretradeMarketData:
    def __init__(self, *, quote: MarketQuote, portfolio: PortfolioSnapshot) -> None:
        self._quote = quote
        self._portfolio = portfolio

    def fetch_quote(self, instrument_id: str, *, as_of: datetime) -> MarketQuote:
        del instrument_id, as_of
        return self._quote

    def fetch_portfolio(self, *, as_of: datetime) -> PortfolioSnapshot:
        del as_of
        return self._portfolio


class _UnknownWritePort:
    """Test double that returns UNKNOWN without calling the real broker."""

    def __init__(self, *, raise_error: bool = False) -> None:
        self.raise_error = raise_error
        self.submit_calls = 0

    def submit(self, request: BrokerSubmitRequest) -> BrokerSubmitResult:
        self.submit_calls += 1
        if self.raise_error:
            raise BrokerUnknownOutcomeError(
                "simulated ambiguous submit",
                reason_code="SIMULATED_UNKNOWN",
                operation="submit",
                idempotency_key=request.client_order_id,
            )
        return BrokerSubmitResult(
            outcome=BrokerSubmitOutcome.UNKNOWN,
            client_order_id=request.client_order_id,
            observed_at=request.proposal.created_at,
            reason_code="SIMULATED_UNKNOWN",
        )

    def cancel(self, command: object) -> object:
        raise NotImplementedError("cancel is out of scope for paper-flow unknown port")


def _fee_for_fill(fill: BrokerFill, costs: PaperCostModel) -> Decimal:
    notional = canonicalize_decimal(fill.quantity) * canonicalize_decimal(fill.price)
    return canonicalize_decimal((notional * costs.fee_bps / BPS_DENOM).quantize(_MONEY_QUANT))


def _transition(
    state: _FlowState,
    *,
    target: OrderLifecycleState,
    subject_id: str,
    correlation_id: str,
    as_of: datetime,
    payload: Mapping[str, str] | None = None,
) -> None:
    state.event_seq += 1
    transition_order(
        current=state.lifecycle,
        expected_current=state.lifecycle,
        target=target,
        subject_id=subject_id,
        event_id=f"evt_01HZYD4ASM{state.event_seq:08d}",
        persistence=state.persistence,
        correlation_id=correlation_id,
        occurred_at=as_of,
        payload=dict(payload or {}),
    )
    state.lifecycle = target


def _record(
    state: _FlowState,
    *,
    name: str,
    as_of: datetime,
    digests: Mapping[str, str] | None = None,
    payload: Mapping[str, Any] | None = None,
) -> None:
    step_digests = dict(digests or {})
    state.digests.update(step_digests)
    state.steps.append(
        StepRecord(
            name=name,
            occurred_at=as_of,
            lifecycle=state.lifecycle,
            digests=step_digests,
            payload=dict(payload or {}),
        )
    )


def _candidate_to_proposal(
    candidate: CandidateOrder,
    *,
    proposal_id: str,
    risk_decision_id: str,
) -> OrderProposal:
    payload = candidate.model_dump(mode="python")
    payload.pop("reason_codes", None)
    payload["proposal_id"] = proposal_id
    payload["candidate_id"] = candidate.candidate_id
    payload["risk_decision_id"] = risk_decision_id
    return parse_order_proposal(attach_order_hash(payload))


def _ma_definition() -> StrategyDefinition:
    return StrategyDefinition.from_type(MovingAverageStrategy, metadata=MA_METADATA)


def _default_exposure(instrument_id: str) -> ExposureInputs:
    return ExposureInputs(
        sectors=(SectorAssignment(instrument_id=instrument_id, sector="TECH"),),
        daily_turnover_to_date=Decimal("0"),
        daily_realized_pnl=Decimal("0"),
        daily_unrealized_pnl=Decimal("0"),
    )


def _instrument_identity(proposal: OrderProposal, *, as_of: datetime) -> InstrumentIdentity:
    return InstrumentIdentity(
        instrument_id=proposal.instrument_id,
        symbol=proposal.symbol,
        exchange=proposal.exchange,
        currency=proposal.currency,
        asset_type=proposal.asset_type,
        identity_as_of=as_of,
    )


def _result(
    state: _FlowState,
    *,
    terminal: PaperFlowTerminal,
    correlation_id: str,
    proposal: OrderProposal | None = None,
    challenge_id: str | None = None,
    approval_event_id: str | None = None,
    client_order_id: str | None = None,
    broker_order_id: str | None = None,
    fill_ids: tuple[str, ...] = (),
    filled_quantity: Decimal = Decimal("0"),
    conservation_ok: bool | None = None,
    error: str | None = None,
) -> PaperFlowResult:
    return PaperFlowResult(
        terminal=terminal,
        lifecycle=state.lifecycle,
        correlation_id=correlation_id,
        steps=list(state.steps),
        digests=dict(state.digests),
        proposal_id=proposal.proposal_id if proposal is not None else None,
        order_hash=proposal.order_hash if proposal is not None else None,
        challenge_id=challenge_id,
        approval_event_id=approval_event_id,
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
        fill_ids=fill_ids,
        filled_quantity=filled_quantity,
        conservation_ok=conservation_ok,
        error=error,
    )


def run_paper_flow(config: PaperFlowConfig) -> PaperFlowResult:
    """Run the fixed ResearchPacket → paper fill loop once."""
    as_of = ensure_utc(config.as_of)
    state = _FlowState()
    calendar = FakeMarketCalendar()
    approval_store = ApprovalStubStore()
    costs = config.cost_model or PaperCostModel(
        fee_bps=Decimal("10"),
        half_spread_bps=Decimal("5"),
        slippage_bps=Decimal("5"),
    )
    exposure = config.exposure_inputs or _default_exposure(config.instrument.instrument_id)
    params = MovingAverageParams.model_validate(dict(config.strategy_params or {}))

    # 1. Strategy evaluation in isolated worker.
    run = evaluate_in_worker(
        _ma_definition(),
        params=params,
        context=config.context,
        run_id="srun_01HZYD4APAPER0001",
    )
    if run.result is None or not run.result.signals:
        _record(
            state,
            name="evaluate_strategy",
            as_of=as_of,
            payload={
                "status": run.status.value,
                "error": run.failure_message,
            },
        )
        return _result(
            state,
            terminal=PaperFlowTerminal.FAILED,
            correlation_id=config.correlation_id,
            error=run.failure_message or "strategy produced no signals",
        )
    signals: tuple[TradeSignal, ...] = run.result.signals
    signal_digest = digest_json(
        [s.model_dump(mode="json") for s in signals],
    )
    _record(
        state,
        name="evaluate_strategy",
        as_of=as_of,
        digests={"signals": signal_digest},
        payload={"signal_ids": [s.signal_id for s in signals]},
    )

    # 2-3. Aggregate + select.
    agg = aggregate_signals(signals, as_of=as_of)
    chosen = selected_signals(agg)
    agg_digest = digest_json([r.model_dump(mode="json") for r in agg])
    _record(
        state,
        name="aggregate_signals",
        as_of=as_of,
        digests={"aggregation": agg_digest},
        payload={"selected_count": len(chosen)},
    )
    if not chosen:
        return _result(
            state,
            terminal=PaperFlowTerminal.FAILED,
            correlation_id=config.correlation_id,
            error="no selected signals after aggregation",
        )
    signal = chosen[0]

    # 4. Size.
    sizing = size_position(
        signal=signal,
        quote=config.quote,
        portfolio=config.portfolio,
        config=config.sizing_config,
        as_of=as_of,
        candidate_id=config.candidate_id,
    )
    sizing_digest = digest_json(sizing.model_dump(mode="json"))
    _record(
        state,
        name="size_position",
        as_of=as_of,
        digests={"sizing": sizing_digest},
        payload={"reason_code": sizing.reason_code},
    )
    if sizing.candidate is None:
        return _result(
            state,
            terminal=PaperFlowTerminal.FAILED,
            correlation_id=config.correlation_id,
            error=f"sizing produced no candidate: {sizing.reason_code}",
        )
    candidate = sizing.candidate

    # 5. Proposal-time risk.
    risk_ctx = RiskContext(
        risk_decision_id=config.proposal_risk_id,
        phase=EvaluationPhase.PROPOSAL,
        as_of=as_of,
        candidate=candidate,
        quote=config.quote,
        instrument=config.instrument,
        config=config.risk_config,
        portfolio=config.portfolio,
        short_term_volatility_bps=config.short_term_volatility_bps,
        exposure_inputs=exposure,
        kill_switch=KillSwitchSnapshot(),
    )
    risk_out = evaluate_risk(risk_ctx, calendar=calendar)
    _record(
        state,
        name="evaluate_risk",
        as_of=as_of,
        digests={
            "risk_input": risk_out.input_digest,
            "risk_config": risk_out.config_digest,
        },
        payload={
            "outcome": risk_out.decision.outcome.value,
            "reason_code": risk_out.decision.reason_code,
        },
    )
    if risk_out.decision.outcome is not RiskOutcome.APPROVED:
        _transition(
            state,
            target=OrderLifecycleState.RISK_REJECTED,
            subject_id=candidate.candidate_id,
            correlation_id=config.correlation_id,
            as_of=as_of,
        )
        _record(state, name="risk_rejected", as_of=as_of)
        return _result(
            state,
            terminal=PaperFlowTerminal.RISK_REJECTED,
            correlation_id=config.correlation_id,
        )

    # 6. Proposal + approval challenge (never auto-approve).
    proposal = _candidate_to_proposal(
        candidate,
        proposal_id=config.proposal_id,
        risk_decision_id=config.proposal_risk_id,
    )
    state.digests["order_hash"] = proposal.order_hash
    _transition(
        state,
        target=OrderLifecycleState.PROPOSAL_CREATED,
        subject_id=proposal.proposal_id,
        correlation_id=config.correlation_id,
        as_of=as_of,
        payload={"order_hash": proposal.order_hash},
    )
    _record(
        state,
        name="create_proposal",
        as_of=as_of,
        digests={"order_hash": proposal.order_hash},
        payload={"proposal_id": proposal.proposal_id},
    )

    challenge = create_challenge(
        proposal,
        as_of=as_of,
        store=approval_store,
        challenge_id=config.challenge_id,
        ttl=config.approval_ttl,
    )
    _transition(
        state,
        target=OrderLifecycleState.APPROVAL_PENDING,
        subject_id=proposal.proposal_id,
        correlation_id=config.correlation_id,
        as_of=as_of,
    )
    _record(
        state,
        name="create_challenge",
        as_of=as_of,
        digests={"challenge": challenge_fingerprint(challenge)},
        payload={"challenge_id": challenge.challenge_id},
    )

    if not config.inject_approval:
        return _result(
            state,
            terminal=PaperFlowTerminal.APPROVAL_PENDING,
            correlation_id=config.correlation_id,
            proposal=proposal,
            challenge_id=challenge.challenge_id,
        )

    # 7. Explicit consume (optional expiry).
    consume_at = as_of
    if config.expire_approval:
        consume_at = ensure_utc(challenge.expires_at + timedelta(seconds=1))
    approval = consume_challenge(
        challenge.challenge_id,
        as_of=consume_at,
        store=approval_store,
        approved=True,
        event_id=config.approval_event_id,
    )
    if approval.outcome is ApprovalEventOutcome.EXPIRED:
        _transition(
            state,
            target=OrderLifecycleState.APPROVAL_EXPIRED,
            subject_id=proposal.proposal_id,
            correlation_id=config.correlation_id,
            as_of=consume_at,
        )
        _record(
            state,
            name="consume_challenge",
            as_of=consume_at,
            payload={"outcome": approval.outcome.value},
        )
        return _result(
            state,
            terminal=PaperFlowTerminal.APPROVAL_EXPIRED,
            correlation_id=config.correlation_id,
            proposal=proposal,
            challenge_id=challenge.challenge_id,
            approval_event_id=approval.event_id,
        )
    if approval.outcome is not ApprovalEventOutcome.APPROVED:
        return _result(
            state,
            terminal=PaperFlowTerminal.FAILED,
            correlation_id=config.correlation_id,
            proposal=proposal,
            challenge_id=challenge.challenge_id,
            approval_event_id=approval.event_id,
            error=f"unexpected approval outcome: {approval.outcome.value}",
        )
    _transition(
        state,
        target=OrderLifecycleState.APPROVED,
        subject_id=proposal.proposal_id,
        correlation_id=config.correlation_id,
        as_of=consume_at,
    )
    _record(
        state,
        name="consume_challenge",
        as_of=consume_at,
        payload={"outcome": approval.outcome.value, "event_id": approval.event_id},
    )

    # 8. Pre-trade re-evaluation (fresh decision id).
    pretrade = evaluate_pretrade(
        PretradeRequest(
            risk_decision_id=config.pretrade_risk_id,
            as_of=as_of,
            candidate=candidate,
            instrument=config.instrument,
            config=config.risk_config,
            client_order_id=config.client_order_id,
            proposal_order_hash=proposal.order_hash,
            prior_proposal_decision_id=config.proposal_risk_id,
            recent_submissions=(),
            exposure_inputs=exposure,
            short_term_volatility_bps=config.short_term_volatility_bps,
        ),
        market_data=_FixedPretradeMarketData(quote=config.quote, portfolio=config.portfolio),
        kill_switch=KillSwitchSnapshot(),
        calendar=calendar,
        prior_decision=risk_out.decision,
    )
    _record(
        state,
        name="evaluate_pretrade",
        as_of=as_of,
        digests={
            "pretrade_input": pretrade.input_digest,
            "pretrade_config": pretrade.config_digest,
        },
        payload={"outcome": pretrade.decision.outcome.value},
    )
    if pretrade.decision.outcome is not RiskOutcome.APPROVED:
        _transition(
            state,
            target=OrderLifecycleState.PRE_TRADE_REJECTED,
            subject_id=proposal.proposal_id,
            correlation_id=config.correlation_id,
            as_of=as_of,
        )
        _record(state, name="pretrade_rejected", as_of=as_of)
        return _result(
            state,
            terminal=PaperFlowTerminal.PRE_TRADE_REJECTED,
            correlation_id=config.correlation_id,
            proposal=proposal,
            challenge_id=challenge.challenge_id,
            approval_event_id=approval.event_id,
        )

    # 9. Broker submit via dispatcher (idempotent; no blind retry).
    clock_moment = as_of
    broker = PaperBroker(
        cost_model=costs,
        clock=lambda: clock_moment,
        initial_cash=config.opening_cash,
    )
    write_port: BrokerWritePort
    if config.write_port is not None:
        write_port = config.write_port
    elif config.raise_unknown_on_submit:
        write_port = _UnknownWritePort(raise_error=True)  # type: ignore[assignment]
    else:
        write_port = as_write_port(broker)

    dispatcher = InProcessCommandDispatcher()

    def _handle_execute(command: WorkflowCommand) -> OrderExecutedEvent:
        exec_cmd = cast(ExecuteOrderCommand, command)
        ensure_not_blind_broker_retry(
            CommandType.EXECUTE_ORDER,
            prior_outcome=state.prior_broker_outcome,
        )
        request = BrokerSubmitRequest(
            proposal=proposal,
            approval=approval,
            client_order_id=exec_cmd.client_order_id,
        )
        try:
            result = write_port.submit(request)
        except BrokerUnknownOutcomeError:
            state.prior_broker_outcome = CommandOutcome.SUBMIT_UNKNOWN
            return OrderExecutedEvent(
                event_id="evt_01HZYD4AEXECUNKN01",
                correlation_id=exec_cmd.correlation_id,
                causation_id=exec_cmd.command_id,
                idempotency_id=exec_cmd.idempotency_id,
                occurred_at=as_of,
                outcome=CommandOutcome.SUBMIT_UNKNOWN,
                reason_code="BROKER_UNKNOWN_OUTCOME",
                proposal_id=proposal.proposal_id,
                client_order_id=exec_cmd.client_order_id,
                broker_order_id=None,
            )
        if result.outcome is BrokerSubmitOutcome.UNKNOWN:
            state.prior_broker_outcome = CommandOutcome.SUBMIT_UNKNOWN
            return OrderExecutedEvent(
                event_id="evt_01HZYD4AEXECUNKN02",
                correlation_id=exec_cmd.correlation_id,
                causation_id=exec_cmd.command_id,
                idempotency_id=exec_cmd.idempotency_id,
                occurred_at=result.observed_at,
                outcome=CommandOutcome.SUBMIT_UNKNOWN,
                reason_code=result.reason_code,
                proposal_id=proposal.proposal_id,
                client_order_id=exec_cmd.client_order_id,
                broker_order_id=(
                    result.broker_order.broker_order_id if result.broker_order is not None else None
                ),
            )
        if result.outcome is BrokerSubmitOutcome.REJECTED:
            state.prior_broker_outcome = CommandOutcome.REJECTED
            return OrderExecutedEvent(
                event_id="evt_01HZYD4AEXECREJ001",
                correlation_id=exec_cmd.correlation_id,
                causation_id=exec_cmd.command_id,
                idempotency_id=exec_cmd.idempotency_id,
                occurred_at=result.observed_at,
                outcome=CommandOutcome.REJECTED,
                reason_code=result.reason_code,
                proposal_id=proposal.proposal_id,
                client_order_id=exec_cmd.client_order_id,
                broker_order_id=None,
            )
        assert result.broker_order is not None
        state.prior_broker_outcome = CommandOutcome.SUCCEEDED
        return OrderExecutedEvent(
            event_id="evt_01HZYD4AEXECOK0001",
            correlation_id=exec_cmd.correlation_id,
            causation_id=exec_cmd.command_id,
            idempotency_id=exec_cmd.idempotency_id,
            occurred_at=result.observed_at,
            outcome=CommandOutcome.SUCCEEDED,
            proposal_id=proposal.proposal_id,
            client_order_id=exec_cmd.client_order_id,
            broker_order_id=result.broker_order.broker_order_id,
        )

    def _handle_reconcile(command: WorkflowCommand) -> ReconciledEvent:
        recon = cast(ReconcileCommand, command)
        return ReconciledEvent(
            event_id="evt_01HZYD4ARECONOK001",
            correlation_id=recon.correlation_id,
            causation_id=recon.command_id,
            idempotency_id=recon.idempotency_id,
            occurred_at=as_of,
            outcome=CommandOutcome.SUCCEEDED,
            reconciliation_id=config.reconciliation_id,
            proposal_id=recon.proposal_id or proposal.proposal_id,
            broker_order_id=recon.broker_order_id,
        )

    dispatcher.register(CommandType.EXECUTE_ORDER, _handle_execute)
    dispatcher.register(CommandType.RECONCILE, _handle_reconcile)

    _transition(
        state,
        target=OrderLifecycleState.SUBMITTING,
        subject_id=proposal.proposal_id,
        correlation_id=config.correlation_id,
        as_of=as_of,
    )
    execute_cmd = ExecuteOrderCommand(
        command_id=FIXED_EXECUTE_COMMAND_ID,
        correlation_id=config.correlation_id,
        causation_id=config.correlation_id,
        idempotency_id=FIXED_EXECUTE_IDEMPOTENCY_ID,
        issued_at=as_of,
        proposal_id=proposal.proposal_id,
        order_hash=proposal.order_hash,
        client_order_id=config.client_order_id,
        approval_event_id=approval.event_id,
    )
    exec_event = dispatcher.dispatch(execute_cmd)
    assert isinstance(exec_event, OrderExecutedEvent)

    if exec_event.outcome is CommandOutcome.SUBMIT_UNKNOWN:
        _transition(
            state,
            target=OrderLifecycleState.SUBMIT_UNKNOWN,
            subject_id=proposal.proposal_id,
            correlation_id=config.correlation_id,
            as_of=as_of,
        )
        _record(
            state,
            name="execute_order",
            as_of=as_of,
            payload={"outcome": "SUBMIT_UNKNOWN"},
        )
        # Must reconcile before any new ExecuteOrder.
        _transition(
            state,
            target=OrderLifecycleState.RECONCILING,
            subject_id=proposal.proposal_id,
            correlation_id=config.correlation_id,
            as_of=as_of,
        )
        recon_cmd = ReconcileCommand(
            command_id=FIXED_RECONCILE_COMMAND_ID,
            correlation_id=config.correlation_id,
            causation_id=execute_cmd.command_id,
            idempotency_id=FIXED_RECONCILE_IDEMPOTENCY_ID,
            issued_at=as_of,
            proposal_id=proposal.proposal_id,
            client_order_id=config.client_order_id,
        )
        dispatcher.dispatch(recon_cmd)
        _record(state, name="reconcile_after_unknown", as_of=as_of)

        # Blind retry with a *new* idempotency id must fail.
        blind_error: str | None = None
        try:
            ensure_not_blind_broker_retry(
                CommandType.EXECUTE_ORDER,
                prior_outcome=CommandOutcome.SUBMIT_UNKNOWN,
            )
            raise AssertionError("expected BlindBrokerRetryError")
        except BlindBrokerRetryError as exc:
            blind_error = str(exc)
        _record(
            state,
            name="blind_retry_blocked",
            as_of=as_of,
            payload={"error": blind_error},
        )
        return _result(
            state,
            terminal=PaperFlowTerminal.SUBMIT_UNKNOWN,
            correlation_id=config.correlation_id,
            proposal=proposal,
            challenge_id=challenge.challenge_id,
            approval_event_id=approval.event_id,
            client_order_id=config.client_order_id,
            error=blind_error,
        )

    if exec_event.outcome is not CommandOutcome.SUCCEEDED or exec_event.broker_order_id is None:
        return _result(
            state,
            terminal=PaperFlowTerminal.FAILED,
            correlation_id=config.correlation_id,
            proposal=proposal,
            challenge_id=challenge.challenge_id,
            approval_event_id=approval.event_id,
            client_order_id=config.client_order_id,
            error=f"submit failed: {exec_event.outcome.value}",
        )

    broker_order_id = exec_event.broker_order_id
    _transition(
        state,
        target=OrderLifecycleState.SUBMITTED,
        subject_id=proposal.proposal_id,
        correlation_id=config.correlation_id,
        as_of=as_of,
    )
    _record(
        state,
        name="execute_order",
        as_of=as_of,
        payload={"broker_order_id": broker_order_id, "outcome": "SUBMITTED"},
    )

    # 10. Inject market event → fill(s).
    clock_moment = as_of + timedelta(seconds=30)
    fills = broker.inject_market_event(
        PaperMarketEvent(
            event_id="mevt_01HZYD4APAPER0001",
            instrument_id=proposal.instrument_id,
            bid=canonicalize_decimal(config.quote.bid or config.quote.last_price),
            ask=canonicalize_decimal(config.quote.ask or config.quote.last_price),
            last=canonicalize_decimal(config.quote.last_price),
            liquidity=config.market_liquidity,
            observed_at=clock_moment,
        )
    )
    fill_ids = tuple(f.fill_id for f in fills)
    filled_qty = sum((f.quantity for f in fills), Decimal("0"))
    orders = broker.get_orders(AccountScope.PAPER)
    broker_order = next(o for o in orders if o.broker_order_id == broker_order_id)
    if broker_order.status is BrokerOrderStatus.FILLED:
        fill_lifecycle = OrderLifecycleState.FILLED
        terminal = PaperFlowTerminal.FILLED
    elif broker_order.status is BrokerOrderStatus.PARTIALLY_FILLED:
        fill_lifecycle = OrderLifecycleState.PARTIALLY_FILLED
        terminal = PaperFlowTerminal.PARTIALLY_FILLED
    else:
        fill_lifecycle = OrderLifecycleState.SUBMITTED
        terminal = PaperFlowTerminal.FAILED

    if fill_lifecycle is not OrderLifecycleState.SUBMITTED:
        _transition(
            state,
            target=fill_lifecycle,
            subject_id=proposal.proposal_id,
            correlation_id=config.correlation_id,
            as_of=clock_moment,
        )
    _record(
        state,
        name="inject_market_event",
        as_of=clock_moment,
        digests={"fills": digest_json([f.model_dump(mode="json") for f in fills])},
        payload={
            "fill_ids": list(fill_ids),
            "filled_quantity": str(filled_qty),
            "broker_status": broker_order.status.value,
        },
    )

    # 11. Reconcile + ledger conservation.
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency=proposal.currency,
        opening_cash=config.opening_cash,
        as_of=as_of,
    )
    fee_map = {f.fill_id: _fee_for_fill(f, costs) for f in fills}
    local = LocalOrderExpectation(
        client_order_id=config.client_order_id,
        proposal_id=proposal.proposal_id,
        side=proposal.side,
        expected_quantity=proposal.quantity,
        expected_limit_price=proposal.limit_price,
        instrument=_instrument_identity(proposal, as_of=as_of),
        local_lifecycle=state.lifecycle,
        broker_order_id=broker_order_id,
    )
    report = OrderReconciler().reconcile(
        local,
        broker_orders=(broker_order,),
        broker_fills=fills,
        observed_at=clock_moment,
        reconciliation_id=config.reconciliation_id,
        ledger=ledger,
        fill_fees=fee_map,
    )
    conservation_ok = None
    if report.outcome is ReconciliationOutcome.MATCHED:
        conservation_ok = ledger.assert_conservation().holds
    _record(
        state,
        name="reconcile",
        as_of=clock_moment,
        digests={"reconciliation": digest_json(report.model_dump(mode="json"))},
        payload={
            "outcome": report.outcome.value,
            "conservation_ok": conservation_ok,
        },
    )
    if terminal is PaperFlowTerminal.FAILED:
        return _result(
            state,
            terminal=terminal,
            correlation_id=config.correlation_id,
            proposal=proposal,
            challenge_id=challenge.challenge_id,
            approval_event_id=approval.event_id,
            client_order_id=config.client_order_id,
            broker_order_id=broker_order_id,
            fill_ids=fill_ids,
            filled_quantity=filled_qty,
            conservation_ok=conservation_ok,
            error=f"unexpected broker status after fill: {broker_order.status.value}",
        )
    return _result(
        state,
        terminal=terminal,
        correlation_id=config.correlation_id,
        proposal=proposal,
        challenge_id=challenge.challenge_id,
        approval_event_id=approval.event_id,
        client_order_id=config.client_order_id,
        broker_order_id=broker_order_id,
        fill_ids=fill_ids,
        filled_quantity=filled_qty,
        conservation_ok=conservation_ok,
    )


__all__ = [
    "PaperFlowConfig",
    "run_paper_flow",
]
