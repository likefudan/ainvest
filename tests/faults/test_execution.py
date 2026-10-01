"""Execution faults: ambiguous writes, partial fills, and uncertain cancels."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from ainvest.execution import BrokerSubmitOutcome, BrokerSubmitRequest, BrokerSubmitResult
from ainvest.execution.state_machine import (
    CancelCommandState,
    IllegalTransitionError,
    InMemoryStatePersistence,
    OrderLifecycleState,
    transition_cancel,
)
from ainvest.orchestrator import PaperFlowTerminal, run_paper_flow
from ainvest.orchestrator.fixtures import make_paper_flow_config
from ainvest.workflow.commands import ExecuteOrderCommand, WorkflowCommand
from ainvest.workflow.dispatcher import InProcessCommandDispatcher
from ainvest.workflow.events import CommandOutcome, OrderExecutedEvent, WorkflowEvent
from ainvest.workflow.semantics import CommandType

from .conftest import FaultEvidence, assert_fault_evidence


class _UnknownSubmitBoundary:
    def __init__(self) -> None:
        self.submit_calls = 0

    def submit(self, request: BrokerSubmitRequest) -> BrokerSubmitResult:
        self.submit_calls += 1
        return BrokerSubmitResult(
            outcome=BrokerSubmitOutcome.UNKNOWN,
            client_order_id=request.client_order_id,
            observed_at=request.proposal.created_at,
            reason_code="INJECTED_CONNECTION_RESET_AFTER_WRITE",
        )

    def cancel(self, command: object) -> object:
        raise AssertionError(f"unexpected cancel: {command!r}")


@pytest.mark.integration
def test_unknown_submit_enters_manual_review_without_blind_retry() -> None:
    boundary = _UnknownSubmitBoundary()

    result = run_paper_flow(make_paper_flow_config(inject_approval=True, write_port=boundary))
    evidence = FaultEvidence(
        final_state=result.lifecycle.value,
        audit_or_result=tuple(step.name for step in result.steps),
        external_calls=boundary.submit_calls,
        funds_effect=str(result.filled_quantity),
    )

    assert result.terminal is PaperFlowTerminal.SUBMIT_UNKNOWN
    assert result.lifecycle is OrderLifecycleState.MANUAL_REVIEW
    assert "reconcile_after_unknown" in evidence.audit_or_result
    assert "blind_retry_blocked" in evidence.audit_or_result
    assert_fault_evidence(evidence, state="MANUAL_REVIEW", calls=1, funds="0")


@pytest.mark.integration
def test_partial_fill_preserves_conservation_and_exact_funds_effect() -> None:
    result = run_paper_flow(make_paper_flow_config(inject_approval=True, market_liquidity="1"))
    evidence = FaultEvidence(
        final_state=result.lifecycle.value,
        audit_or_result=tuple(str(event.event_type) for event in result.audit_events),
        external_calls=1,
        funds_effect=str(result.filled_quantity),
    )

    assert result.terminal is PaperFlowTerminal.PARTIALLY_FILLED
    assert result.conservation_ok is True
    assert result.filled_quantity == Decimal("1")
    assert_fault_evidence(evidence, state="PARTIALLY_FILLED", calls=1, funds="1")


@pytest.mark.integration
def test_uncertain_cancel_can_only_reconcile_and_is_never_reissued() -> None:
    persistence = InMemoryStatePersistence()
    cancel_calls = 1  # the injected boundary returned UNKNOWN once
    first = transition_cancel(
        current=CancelCommandState.CANCEL_REQUESTED,
        expected_current=CancelCommandState.CANCEL_REQUESTED,
        target=CancelCommandState.CANCEL_UNKNOWN,
        subject_id="cancel_fault_01",
        event_id="event_cancel_unknown_01",
        persistence=persistence,
    )

    with pytest.raises(IllegalTransitionError):
        transition_cancel(
            current=CancelCommandState.CANCEL_UNKNOWN,
            expected_current=CancelCommandState.CANCEL_UNKNOWN,
            target=CancelCommandState.CANCEL_REQUESTED,
            subject_id="cancel_fault_01",
            event_id="event_cancel_retry_01",
            persistence=persistence,
        )
    reconciled = transition_cancel(
        current=CancelCommandState.CANCEL_UNKNOWN,
        expected_current=CancelCommandState.CANCEL_UNKNOWN,
        target=CancelCommandState.CANCEL_RECONCILING,
        subject_id="cancel_fault_01",
        event_id="event_cancel_reconcile_01",
        persistence=persistence,
    )
    evidence = FaultEvidence(
        final_state=reconciled.after,
        audit_or_result=(first.after, reconciled.after),
        external_calls=cancel_calls,
        funds_effect="unchanged",
    )

    assert len(persistence.records) == 2
    assert_fault_evidence(evidence, state="CANCEL_RECONCILING", calls=1, funds="unchanged")


@pytest.mark.integration
def test_duplicate_scheduler_command_replays_one_funds_effect() -> None:
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    command = ExecuteOrderCommand(
        command_id="cmd_fault_duplicate_01",
        correlation_id="corr_fault_duplicate_01",
        idempotency_id="idem_fault_duplicate_01",
        issued_at=now,
        input_digest="sha256:" + ("a" * 64),
        proposal_id="ordp_fault_duplicate_01",
        client_order_id="paper_fault_duplicate_01",
        order_hash="sha256:" + ("b" * 64),
        approval_event_id="apev_fault_duplicate_01",
    )
    funds_effects = 0

    def execute(candidate: WorkflowCommand) -> WorkflowEvent:
        nonlocal funds_effects
        execute_command = cast(ExecuteOrderCommand, candidate)
        funds_effects += 1
        return OrderExecutedEvent(
            event_id="evt_fault_duplicate_01",
            correlation_id=execute_command.correlation_id,
            causation_id=execute_command.command_id,
            idempotency_id=execute_command.idempotency_id,
            occurred_at=now,
            outcome=CommandOutcome.SUCCEEDED,
            proposal_id=execute_command.proposal_id,
            client_order_id=execute_command.client_order_id,
            broker_order_id="paper_order_fault_01",
        )

    dispatcher = InProcessCommandDispatcher()
    dispatcher.register(CommandType.EXECUTE_ORDER, execute)
    first = dispatcher.dispatch(command)
    replay = dispatcher.dispatch(command)
    evidence = FaultEvidence(
        final_state=first.outcome.value,
        audit_or_result=(first.event_id, replay.event_id),
        external_calls=funds_effects,
        funds_effect=str(funds_effects),
    )

    assert first == replay
    assert_fault_evidence(evidence, state="SUCCEEDED", calls=1, funds="1")
