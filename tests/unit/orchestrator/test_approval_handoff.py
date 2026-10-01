"""P05-T6 Approval-to-Workflow composition bridge tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from ainvest.approval.handoff import (
    ApprovalExecutionOutcome,
    PaperExecutionHandoff,
)
from ainvest.orchestrator.approval_handoff import WorkflowExecutionHandoff
from ainvest.workflow.commands import ExecuteOrderCommand, WorkflowCommand
from ainvest.workflow.dispatcher import InProcessCommandDispatcher
from ainvest.workflow.events import CommandOutcome, OrderExecutedEvent, WorkflowEvent
from ainvest.workflow.semantics import CommandType

NOW = datetime(2026, 7, 24, 18, 31, 10, tzinfo=UTC)
ORDER_HASH = "sha256:" + ("a" * 64)


def _request() -> PaperExecutionHandoff:
    return PaperExecutionHandoff(
        command_id="cmd_01HZYHANDOFF00001",
        correlation_id="corr_01HZYHANDOFF00001",
        idempotency_id="idem_01HZYHANDOFF00001",
        issued_at=NOW,
        input_digest=ORDER_HASH,
        proposal_id="ordp_01HZYHANDOFF0001",
        order_hash=ORDER_HASH,
        client_order_id="paper_01HZYHANDOFF0001",
        approval_event_id="apev_01HZYHANDOFF00001",
    )


@pytest.mark.unit
def test_bridge_builds_exact_execute_command_and_dispatcher_replays() -> None:
    commands: list[ExecuteOrderCommand] = []
    dispatcher = InProcessCommandDispatcher()

    def handle(command: WorkflowCommand) -> WorkflowEvent:
        execute = cast(ExecuteOrderCommand, command)
        commands.append(execute)
        return OrderExecutedEvent(
            event_id="evt_01HZYHANDOFF00001",
            correlation_id=execute.correlation_id,
            causation_id=execute.command_id,
            idempotency_id=execute.idempotency_id,
            occurred_at=NOW,
            outcome=CommandOutcome.SUCCEEDED,
            proposal_id=execute.proposal_id,
            client_order_id=execute.client_order_id,
            broker_order_id="paper_order_01",
        )

    dispatcher.register(CommandType.EXECUTE_ORDER, handle)
    bridge = WorkflowExecutionHandoff(dispatcher)
    request = _request()

    first = bridge.dispatch(request)
    second = bridge.dispatch(request)

    assert first == second
    assert first.outcome is ApprovalExecutionOutcome.SUCCEEDED
    assert len(commands) == 1
    command = commands[0]
    assert command.proposal_id == request.proposal_id
    assert command.order_hash == request.order_hash
    assert command.approval_event_id == request.approval_event_id
    assert command.client_order_id == request.client_order_id
    assert command.input_digest == request.input_digest


@pytest.mark.unit
def test_bridge_maps_unknown_to_retry_later() -> None:
    dispatcher = InProcessCommandDispatcher()

    def handle(command: WorkflowCommand) -> WorkflowEvent:
        execute = cast(ExecuteOrderCommand, command)
        return OrderExecutedEvent(
            event_id="evt_01HZYHANDOFFUNKN1",
            correlation_id=execute.correlation_id,
            causation_id=execute.command_id,
            idempotency_id=execute.idempotency_id,
            occurred_at=NOW,
            outcome=CommandOutcome.SUBMIT_UNKNOWN,
            reason_code="BROKER_UNKNOWN_OUTCOME",
            proposal_id=execute.proposal_id,
            client_order_id=execute.client_order_id,
        )

    dispatcher.register(CommandType.EXECUTE_ORDER, handle)

    result = WorkflowExecutionHandoff(dispatcher).dispatch(_request())

    assert result.outcome is ApprovalExecutionOutcome.RETRY_LATER
    assert result.reason_code == "BROKER_UNKNOWN_OUTCOME"
