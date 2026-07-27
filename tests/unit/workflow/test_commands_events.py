"""Unit tests for workflow IDs, commands, events, and dispatcher (P02-T10)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import ValidationError

from ainvest.schemas.risk import RiskOutcome
from ainvest.workflow import (
    COMMAND_RETRY_SEMANTICS,
    COMMAND_TYPE_TO_MODEL,
    ActorKind,
    BlindBrokerRetryError,
    CancelOrderCommand,
    CommandOutcome,
    CommandRejectedEvent,
    CommandType,
    CreateProposalCommand,
    DuplicateCommandError,
    EvaluateRiskCommand,
    EvaluateStrategyCommand,
    EventType,
    ExecuteOrderCommand,
    InMemoryIdempotencyStore,
    InProcessCommandDispatcher,
    ManualReviewResolution,
    ManualReviewResolvedEvent,
    OrderExecutedEvent,
    PositionSizedEvent,
    ReconcileCommand,
    ReconciledEvent,
    ResolveManualReviewCommand,
    RetrySemantics,
    RiskEvaluatedEvent,
    SizePositionCommand,
    StrategyEvaluatedEvent,
    UnknownCommandHandlerError,
    WorkflowCommand,
    WorkflowEvent,
    allows_blind_retry,
    command_digest,
    continue_trace,
    ensure_not_blind_broker_retry,
    is_broker_write,
    new_command_id,
    new_event_id,
    new_idempotency_id,
    retry_semantics_for,
    start_trace,
)

AS_OF = datetime(2026, 7, 27, 14, 0, 0, tzinfo=UTC)
DIGEST = "sha256:" + ("ab" * 32)
ORDER_HASH = "sha256:" + ("cd" * 32)


def _trace_ids() -> tuple[str, str, str]:
    trace = start_trace()
    return trace.correlation_id, new_command_id(), trace.idempotency_id


@pytest.mark.unit
def test_every_command_type_has_retry_semantics_and_model() -> None:
    assert set(COMMAND_RETRY_SEMANTICS) == set(CommandType)
    assert set(COMMAND_TYPE_TO_MODEL) == set(CommandType)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("command_type", "expected"),
    [
        (CommandType.EVALUATE_STRATEGY, RetrySemantics.PURE_RETRYABLE),
        (CommandType.SIZE_POSITION, RetrySemantics.PURE_RETRYABLE),
        (CommandType.EVALUATE_RISK, RetrySemantics.PURE_RETRYABLE),
        (CommandType.CREATE_PROPOSAL, RetrySemantics.PURE_RETRYABLE),
        (CommandType.REQUEST_APPROVAL, RetrySemantics.PURE_RETRYABLE),
        (CommandType.CONSUME_APPROVAL, RetrySemantics.PURE_RETRYABLE),
        (CommandType.RESOLVE_MANUAL_REVIEW, RetrySemantics.PURE_RETRYABLE),
        (CommandType.RECONCILE, RetrySemantics.READ_ONLY_EXTERNAL),
        (CommandType.EXECUTE_ORDER, RetrySemantics.BROKER_WRITE),
        (CommandType.CANCEL_ORDER, RetrySemantics.BROKER_WRITE),
    ],
)
def test_retry_semantics_classification(
    command_type: CommandType, expected: RetrySemantics
) -> None:
    assert retry_semantics_for(command_type) is expected
    assert allows_blind_retry(command_type) is (expected is not RetrySemantics.BROKER_WRITE)
    assert is_broker_write(command_type) is (expected is RetrySemantics.BROKER_WRITE)


@pytest.mark.unit
def test_continue_trace_preserves_correlation() -> None:
    root = start_trace(idempotency_id=new_idempotency_id())
    parent_cmd = new_command_id()
    child = continue_trace(root, parent_id=parent_cmd)
    assert child.correlation_id == root.correlation_id
    assert child.causation_id == parent_cmd
    assert child.idempotency_id != root.idempotency_id


@pytest.mark.unit
def test_evaluate_risk_requires_subject() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    with pytest.raises(ValueError, match="candidate_id or proposal_id"):
        EvaluateRiskCommand(
            command_id=command_id,
            correlation_id=correlation_id,
            idempotency_id=idempotency_id,
            issued_at=AS_OF,
        )


@pytest.mark.unit
def test_reconcile_requires_subject() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    with pytest.raises(ValueError, match="broker_order_id"):
        ReconcileCommand(
            command_id=command_id,
            correlation_id=correlation_id,
            idempotency_id=idempotency_id,
            issued_at=AS_OF,
            proposal_id="ordp_01HZYRECONCILE0001",
        )


@pytest.mark.unit
def test_manual_review_requires_operator_actor() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    cmd = ResolveManualReviewCommand(
        command_id=command_id,
        correlation_id=correlation_id,
        idempotency_id=idempotency_id,
        issued_at=AS_OF,
        actor_id="op_01",
        subject_id="ordp_01HZYMANUALREVIEW01",
        resolution=ManualReviewResolution.CONFIRM_SUBMITTED,
        reason_code="OPERATOR_CONFIRMED",
        reason="broker fill observed offline",
    )
    assert cmd.actor_kind is ActorKind.OPERATOR
    assert cmd.retry_semantics is RetrySemantics.PURE_RETRYABLE


@pytest.mark.unit
def test_manual_review_rejects_system_actor_id() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    with pytest.raises(ValueError, match="non-system operator actor_id"):
        ResolveManualReviewCommand(
            command_id=command_id,
            correlation_id=correlation_id,
            idempotency_id=idempotency_id,
            issued_at=AS_OF,
            actor_id="system",
            subject_id="ordp_01HZYMANUALREVIEW01",
            resolution=ManualReviewResolution.CONFIRM_SUBMITTED,
            reason_code="OPERATOR_CONFIRMED",
            reason="broker fill observed offline",
        )


@pytest.mark.unit
def test_cancel_requires_operator_and_rejects_system() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    with pytest.raises(ValidationError):
        CancelOrderCommand(  # type: ignore[call-arg]
            command_id=command_id,
            correlation_id=correlation_id,
            idempotency_id=idempotency_id,
            issued_at=AS_OF,
            cancel_id="canc_01HZYCANCELORDER001",
            proposal_id="ordp_01HZYCANCELORDER001",
            broker_order_id="broker_ord_1",
            order_hash=ORDER_HASH,
            reason_code="OPERATOR_REQUEST",
        )
    with pytest.raises(ValueError, match="non-system operator actor_id"):
        CancelOrderCommand(
            command_id=command_id,
            correlation_id=correlation_id,
            idempotency_id=idempotency_id,
            issued_at=AS_OF,
            actor_id="system",
            cancel_id="canc_01HZYCANCELORDER001",
            proposal_id="ordp_01HZYCANCELORDER001",
            broker_order_id="broker_ord_1",
            order_hash=ORDER_HASH,
            reason_code="OPERATOR_REQUEST",
        )
    with pytest.raises(ValidationError):
        CancelOrderCommand(
            command_id=command_id,
            correlation_id=correlation_id,
            idempotency_id=idempotency_id,
            issued_at=AS_OF,
            actor_kind=ActorKind.SYSTEM,  # type: ignore[arg-type]
            actor_id="op_01",
            cancel_id="canc_01HZYCANCELORDER001",
            proposal_id="ordp_01HZYCANCELORDER001",
            broker_order_id="broker_ord_1",
            order_hash=ORDER_HASH,
            reason_code="OPERATOR_REQUEST",
        )
    cancel = CancelOrderCommand(
        command_id=command_id,
        correlation_id=correlation_id,
        idempotency_id=idempotency_id,
        issued_at=AS_OF,
        actor_id="op_99",
        cancel_id="canc_01HZYCANCELORDER001",
        proposal_id="ordp_01HZYCANCELORDER001",
        broker_order_id="broker_ord_1",
        order_hash=ORDER_HASH,
        reason_code="OPERATOR_REQUEST",
    )
    assert cancel.actor_kind is ActorKind.OPERATOR
    assert cancel.actor_id == "op_99"


@pytest.mark.unit
def test_dispatch_idempotent_replay_returns_same_event() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    command = SizePositionCommand(
        command_id=command_id,
        correlation_id=correlation_id,
        idempotency_id=idempotency_id,
        issued_at=AS_OF,
        signal_id="sig_01HZYSIZEPOSITION01",
        input_digest=DIGEST,
    )
    calls = {"n": 0}

    def handler(cmd: WorkflowCommand) -> WorkflowEvent:
        calls["n"] += 1
        sized = cast(SizePositionCommand, cmd)
        return PositionSizedEvent(
            event_id=new_event_id(),
            correlation_id=sized.correlation_id,
            causation_id=sized.command_id,
            idempotency_id=sized.idempotency_id,
            occurred_at=AS_OF,
            outcome=CommandOutcome.SUCCEEDED,
            signal_id=sized.signal_id,
            candidate_id="cand_01HZYSIZEPOSITION01",
        )

    bus = InProcessCommandDispatcher()
    bus.register(CommandType.SIZE_POSITION, handler)

    first = bus.dispatch(command)
    second = bus.dispatch(command)

    assert calls["n"] == 1
    assert first == second
    assert first.correlation_id == correlation_id
    assert first.causation_id == command_id
    assert first.idempotency_id == idempotency_id


@pytest.mark.unit
def test_dispatch_rejects_idempotency_conflict() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    first = SizePositionCommand(
        command_id=command_id,
        correlation_id=correlation_id,
        idempotency_id=idempotency_id,
        issued_at=AS_OF,
        signal_id="sig_01HZYSIZEPOSITION01",
    )
    conflicting = SizePositionCommand(
        command_id=new_command_id(),
        correlation_id=correlation_id,
        idempotency_id=idempotency_id,
        issued_at=AS_OF,
        signal_id="sig_01HZYSIZEPOSITION02",
    )

    def handler(cmd: WorkflowCommand) -> WorkflowEvent:
        sized = cast(SizePositionCommand, cmd)
        return PositionSizedEvent(
            event_id=new_event_id(),
            correlation_id=sized.correlation_id,
            causation_id=sized.command_id,
            idempotency_id=sized.idempotency_id,
            occurred_at=AS_OF,
            outcome=CommandOutcome.SUCCEEDED,
            signal_id=sized.signal_id,
        )

    bus = InProcessCommandDispatcher(store=InMemoryIdempotencyStore())
    bus.register(CommandType.SIZE_POSITION, handler)
    bus.dispatch(first)
    with pytest.raises(DuplicateCommandError):
        bus.dispatch(conflicting)


@pytest.mark.unit
def test_dispatch_unknown_handler() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    command = CreateProposalCommand(
        command_id=command_id,
        correlation_id=correlation_id,
        idempotency_id=idempotency_id,
        issued_at=AS_OF,
        candidate_id="cand_01HZYPROPOSAL00001",
        risk_decision_id="risk_01HZYPROPOSAL00001",
    )
    bus = InProcessCommandDispatcher()
    with pytest.raises(UnknownCommandHandlerError):
        bus.dispatch(command)


@pytest.mark.unit
def test_handler_must_preserve_trace_ids() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    command = EvaluateStrategyCommand(
        command_id=command_id,
        correlation_id=correlation_id,
        idempotency_id=idempotency_id,
        issued_at=AS_OF,
        strategy_run_id="srun_01HZYSTRATEGY0001",
        strategy_name="moving_average",
        strategy_version="1.0.0",
        context_digest=DIGEST,
    )

    def bad_handler(cmd: WorkflowCommand) -> WorkflowEvent:
        return StrategyEvaluatedEvent(
            event_id=new_event_id(),
            correlation_id=start_trace().correlation_id,
            causation_id=cmd.command_id,
            idempotency_id=cmd.idempotency_id,
            occurred_at=AS_OF,
            outcome=CommandOutcome.SUCCEEDED,
            strategy_run_id="srun_01HZYSTRATEGY0001",
        )

    bus = InProcessCommandDispatcher()
    bus.register(CommandType.EVALUATE_STRATEGY, bad_handler)
    with pytest.raises(ValueError, match="correlation mismatch"):
        bus.dispatch(command)


@pytest.mark.unit
def test_workflow_trace_connects_command_chain() -> None:
    """One correlation ID connects strategy → size → risk across the bus."""
    root = start_trace()
    store = InMemoryIdempotencyStore()
    bus = InProcessCommandDispatcher(store=store)
    timeline: list[WorkflowEvent] = []

    def strategy_handler(cmd: WorkflowCommand) -> WorkflowEvent:
        evaluate = cast(EvaluateStrategyCommand, cmd)
        event = StrategyEvaluatedEvent(
            event_id=new_event_id(),
            correlation_id=evaluate.correlation_id,
            causation_id=evaluate.command_id,
            idempotency_id=evaluate.idempotency_id,
            occurred_at=AS_OF,
            outcome=CommandOutcome.SUCCEEDED,
            strategy_run_id=evaluate.strategy_run_id,
            signal_ids=("sig_01HZYTRACECHAIN0001",),
        )
        timeline.append(event)
        return event

    def size_handler(cmd: WorkflowCommand) -> WorkflowEvent:
        sized = cast(SizePositionCommand, cmd)
        event = PositionSizedEvent(
            event_id=new_event_id(),
            correlation_id=sized.correlation_id,
            causation_id=sized.command_id,
            idempotency_id=sized.idempotency_id,
            occurred_at=AS_OF,
            outcome=CommandOutcome.SUCCEEDED,
            signal_id=sized.signal_id,
            candidate_id="cand_01HZYTRACECHAIN001",
        )
        timeline.append(event)
        return event

    def risk_handler(cmd: WorkflowCommand) -> WorkflowEvent:
        risk_cmd = cast(EvaluateRiskCommand, cmd)
        event = RiskEvaluatedEvent(
            event_id=new_event_id(),
            correlation_id=risk_cmd.correlation_id,
            causation_id=risk_cmd.command_id,
            idempotency_id=risk_cmd.idempotency_id,
            occurred_at=AS_OF,
            outcome=CommandOutcome.SUCCEEDED,
            risk_decision_id="risk_01HZYTRACECHAIN001",
            risk_outcome=RiskOutcome.APPROVED,
            candidate_id=risk_cmd.candidate_id,
        )
        timeline.append(event)
        return event

    bus.register(CommandType.EVALUATE_STRATEGY, strategy_handler)
    bus.register(CommandType.SIZE_POSITION, size_handler)
    bus.register(CommandType.EVALUATE_RISK, risk_handler)

    strategy_cmd = EvaluateStrategyCommand(
        command_id=new_command_id(),
        correlation_id=root.correlation_id,
        causation_id=None,
        idempotency_id=new_idempotency_id(),
        issued_at=AS_OF,
        strategy_run_id="srun_01HZYTRACECHAIN001",
        strategy_name="moving_average",
        strategy_version="1.0.0",
        context_digest=DIGEST,
    )
    strategy_event = bus.dispatch(strategy_cmd)

    size_trace = continue_trace(root, parent_id=strategy_event.event_id)
    size_cmd = SizePositionCommand(
        command_id=new_command_id(),
        correlation_id=size_trace.correlation_id,
        causation_id=size_trace.causation_id,
        idempotency_id=size_trace.idempotency_id,
        issued_at=AS_OF,
        signal_id="sig_01HZYTRACECHAIN0001",
    )
    size_event = bus.dispatch(size_cmd)

    risk_trace = continue_trace(size_trace, parent_id=size_event.event_id)
    risk_cmd = EvaluateRiskCommand(
        command_id=new_command_id(),
        correlation_id=risk_trace.correlation_id,
        causation_id=risk_trace.causation_id,
        idempotency_id=risk_trace.idempotency_id,
        issued_at=AS_OF,
        candidate_id="cand_01HZYTRACECHAIN001",
    )
    risk_event = bus.dispatch(risk_cmd)

    assert {e.correlation_id for e in timeline} == {root.correlation_id}
    assert strategy_event.causation_id == strategy_cmd.command_id
    assert size_event.causation_id == size_cmd.command_id
    assert risk_event.causation_id == risk_cmd.command_id
    assert size_cmd.causation_id == strategy_event.event_id
    assert risk_cmd.causation_id == size_event.event_id


@pytest.mark.unit
def test_broker_write_idempotent_replay_safe_but_blind_retry_blocked() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    command = ExecuteOrderCommand(
        command_id=command_id,
        correlation_id=correlation_id,
        idempotency_id=idempotency_id,
        issued_at=AS_OF,
        proposal_id="ordp_01HZYEXECUTE000001",
        order_hash=ORDER_HASH,
        client_order_id="client_ord_execute_1",
        approval_event_id="appe_01HZYEXECUTE000001",
    )
    calls = {"n": 0}

    def handler(cmd: WorkflowCommand) -> WorkflowEvent:
        calls["n"] += 1
        execute = cast(ExecuteOrderCommand, cmd)
        return OrderExecutedEvent(
            event_id=new_event_id(),
            correlation_id=execute.correlation_id,
            causation_id=execute.command_id,
            idempotency_id=execute.idempotency_id,
            occurred_at=AS_OF,
            outcome=CommandOutcome.SUBMIT_UNKNOWN,
            proposal_id=execute.proposal_id,
            client_order_id=execute.client_order_id,
        )

    bus = InProcessCommandDispatcher()
    bus.register(CommandType.EXECUTE_ORDER, handler)
    first = bus.dispatch(command)
    replay = bus.dispatch(command)
    assert calls["n"] == 1
    assert first == replay
    assert first.outcome is CommandOutcome.SUBMIT_UNKNOWN

    with pytest.raises(BlindBrokerRetryError, match="reconcile"):
        ensure_not_blind_broker_retry(
            CommandType.EXECUTE_ORDER,
            prior_outcome=first.outcome,
        )


@pytest.mark.unit
def test_ensure_not_blind_broker_retry_allows_pure_and_none() -> None:
    ensure_not_blind_broker_retry(CommandType.SIZE_POSITION, prior_outcome=None)
    ensure_not_blind_broker_retry(
        CommandType.SIZE_POSITION,
        prior_outcome=CommandOutcome.UNKNOWN,
    )
    ensure_not_blind_broker_retry(CommandType.EXECUTE_ORDER, prior_outcome=None)
    ensure_not_blind_broker_retry(
        CommandType.EXECUTE_ORDER,
        prior_outcome=CommandOutcome.SUCCEEDED,
    )


@pytest.mark.unit
def test_cancel_and_reconcile_command_shapes() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    cancel = CancelOrderCommand(
        command_id=command_id,
        correlation_id=correlation_id,
        idempotency_id=idempotency_id,
        issued_at=AS_OF,
        actor_kind=ActorKind.OPERATOR,
        actor_id="op_01",
        cancel_id="canc_01HZYCANCELORDER001",
        proposal_id="ordp_01HZYCANCELORDER001",
        broker_order_id="broker_ord_1",
        order_hash=ORDER_HASH,
        reason_code="OPERATOR_REQUEST",
        reason="user requested cancel",
    )
    assert cancel.retry_semantics is RetrySemantics.BROKER_WRITE

    reconcile = ReconcileCommand(
        command_id=new_command_id(),
        correlation_id=correlation_id,
        causation_id=cancel.command_id,
        idempotency_id=new_idempotency_id(),
        issued_at=AS_OF,
        broker_order_id="broker_ord_1",
        client_order_id="client_ord_execute_1",
    )
    assert reconcile.retry_semantics is RetrySemantics.READ_ONLY_EXTERNAL


@pytest.mark.unit
def test_command_rejected_event_requires_rejected_outcome() -> None:
    correlation_id = start_trace().correlation_id
    with pytest.raises(ValueError, match="outcome=REJECTED"):
        CommandRejectedEvent(
            event_id=new_event_id(),
            correlation_id=correlation_id,
            causation_id=new_command_id(),
            idempotency_id=new_idempotency_id(),
            occurred_at=AS_OF,
            outcome=CommandOutcome.SUCCEEDED,
            command_type=CommandType.EVALUATE_RISK,
            reason_code="BAD",
        )


@pytest.mark.unit
def test_reconciled_event_stub() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    event = ReconciledEvent(
        event_id=new_event_id(),
        correlation_id=correlation_id,
        causation_id=command_id,
        idempotency_id=idempotency_id,
        occurred_at=AS_OF,
        outcome=CommandOutcome.NEEDS_REVIEW,
        broker_order_id="broker_ord_1",
        reason_code="DIVERGED",
    )
    assert event.event_type is EventType.RECONCILED


@pytest.mark.unit
def test_handler_result_independent_of_hidden_counter() -> None:
    """Handlers must derive outcomes from the command, not process-local state."""
    correlation_id = start_trace().correlation_id
    shared_signal = "sig_01HZYNHIDDENSTATE01"

    def pure_handler(cmd: WorkflowCommand) -> WorkflowEvent:
        sized = cast(SizePositionCommand, cmd)
        candidate = f"cand_{sized.signal_id.removeprefix('sig_')}"
        return PositionSizedEvent(
            event_id=new_event_id(),
            correlation_id=sized.correlation_id,
            causation_id=sized.command_id,
            idempotency_id=sized.idempotency_id,
            occurred_at=AS_OF,
            outcome=CommandOutcome.SUCCEEDED,
            signal_id=sized.signal_id,
            candidate_id=candidate if candidate.startswith("cand_") else None,
        )

    bus = InProcessCommandDispatcher()
    bus.register(CommandType.SIZE_POSITION, pure_handler)

    results: list[PositionSizedEvent] = []
    for _ in range(2):
        cmd = SizePositionCommand(
            command_id=new_command_id(),
            correlation_id=correlation_id,
            idempotency_id=new_idempotency_id(),
            issued_at=AS_OF,
            signal_id=shared_signal,
        )
        results.append(cast(PositionSizedEvent, bus.dispatch(cmd)))

    assert results[0].candidate_id == results[1].candidate_id
    assert results[0].idempotency_id != results[1].idempotency_id


@pytest.mark.unit
def test_command_digest_ignores_attempt_scoped_fields() -> None:
    correlation_id = start_trace().correlation_id
    idempotency_id = new_idempotency_id()
    first = SizePositionCommand(
        command_id=new_command_id(),
        correlation_id=correlation_id,
        idempotency_id=idempotency_id,
        issued_at=AS_OF,
        signal_id="sig_01HZYDIGESTIGNORE01",
    )
    retry = SizePositionCommand(
        command_id=new_command_id(),
        correlation_id=correlation_id,
        idempotency_id=idempotency_id,
        issued_at=datetime(2026, 7, 27, 15, 0, 0, tzinfo=UTC),
        signal_id="sig_01HZYDIGESTIGNORE01",
    )
    assert first.command_id != retry.command_id
    assert first.issued_at != retry.issued_at
    assert command_digest(first) == command_digest(retry)


@pytest.mark.unit
def test_dispatch_replays_when_retry_mints_new_command_id() -> None:
    correlation_id = start_trace().correlation_id
    idempotency_id = new_idempotency_id()
    calls = {"n": 0}

    def handler(cmd: WorkflowCommand) -> WorkflowEvent:
        calls["n"] += 1
        sized = cast(SizePositionCommand, cmd)
        return PositionSizedEvent(
            event_id=new_event_id(),
            correlation_id=sized.correlation_id,
            causation_id=sized.command_id,
            idempotency_id=sized.idempotency_id,
            occurred_at=AS_OF,
            outcome=CommandOutcome.SUCCEEDED,
            signal_id=sized.signal_id,
            candidate_id="cand_01HZYDIGESTRETRY01",
        )

    bus = InProcessCommandDispatcher()
    bus.register(CommandType.SIZE_POSITION, handler)
    first = bus.dispatch(
        SizePositionCommand(
            command_id=new_command_id(),
            correlation_id=correlation_id,
            idempotency_id=idempotency_id,
            issued_at=AS_OF,
            signal_id="sig_01HZYDIGESTRETRY01",
        )
    )
    replay = bus.dispatch(
        SizePositionCommand(
            command_id=new_command_id(),
            correlation_id=correlation_id,
            idempotency_id=idempotency_id,
            issued_at=datetime(2026, 7, 27, 16, 0, 0, tzinfo=UTC),
            signal_id="sig_01HZYDIGESTRETRY01",
        )
    )
    assert calls["n"] == 1
    assert first == replay


@pytest.mark.unit
def test_handler_wrong_event_type_not_stored() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    command = SizePositionCommand(
        command_id=command_id,
        correlation_id=correlation_id,
        idempotency_id=idempotency_id,
        issued_at=AS_OF,
        signal_id="sig_01HZYWRONGEVENT0001",
    )

    def wrong_handler(cmd: WorkflowCommand) -> WorkflowEvent:
        return StrategyEvaluatedEvent(
            event_id=new_event_id(),
            correlation_id=cmd.correlation_id,
            causation_id=cmd.command_id,
            idempotency_id=cmd.idempotency_id,
            occurred_at=AS_OF,
            outcome=CommandOutcome.SUCCEEDED,
            strategy_run_id="srun_01HZYWRONGEVENT001",
        )

    store = InMemoryIdempotencyStore()
    bus = InProcessCommandDispatcher(store=store)
    bus.register(CommandType.SIZE_POSITION, wrong_handler)
    with pytest.raises(ValueError, match="STRATEGY_EVALUATED"):
        bus.dispatch(command)
    assert len(store) == 0


@pytest.mark.unit
def test_handler_may_return_command_rejected() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    command = SizePositionCommand(
        command_id=command_id,
        correlation_id=correlation_id,
        idempotency_id=idempotency_id,
        issued_at=AS_OF,
        signal_id="sig_01HZYREJECTEVENT001",
    )

    def reject_handler(cmd: WorkflowCommand) -> WorkflowEvent:
        return CommandRejectedEvent(
            event_id=new_event_id(),
            correlation_id=cmd.correlation_id,
            causation_id=cmd.command_id,
            idempotency_id=cmd.idempotency_id,
            occurred_at=AS_OF,
            outcome=CommandOutcome.REJECTED,
            reason_code="SIZING_REJECTED",
            command_type=CommandType.SIZE_POSITION,
            subject_id=cast(SizePositionCommand, cmd).signal_id,
        )

    bus = InProcessCommandDispatcher()
    bus.register(CommandType.SIZE_POSITION, reject_handler)
    event = bus.dispatch(command)
    assert event.event_type is EventType.COMMAND_REJECTED
    assert bus.dispatch(command) == event


@pytest.mark.unit
def test_manual_review_resolved_event_uses_resolution_enum() -> None:
    correlation_id, command_id, idempotency_id = _trace_ids()
    event = ManualReviewResolvedEvent(
        event_id=new_event_id(),
        correlation_id=correlation_id,
        causation_id=command_id,
        idempotency_id=idempotency_id,
        occurred_at=AS_OF,
        outcome=CommandOutcome.SUCCEEDED,
        subject_id="ordp_01HZYMANUALREVIEW01",
        resolution=ManualReviewResolution.CONFIRM_SUBMITTED,
    )
    assert event.resolution is ManualReviewResolution.CONFIRM_SUBMITTED
