"""Composition bridge from approval handoff requests to workflow commands."""

from __future__ import annotations

from ainvest.approval.handoff import (
    ApprovalExecutionOutcome,
    ApprovalExecutionResult,
    ApprovalHandoffContractError,
    PaperExecutionHandoff,
)
from ainvest.audit.digests import digest_json
from ainvest.workflow.commands import ExecuteOrderCommand
from ainvest.workflow.dispatcher import CommandDispatcher
from ainvest.workflow.events import CommandOutcome, CommandRejectedEvent, OrderExecutedEvent
from ainvest.workflow.semantics import CommandType


class WorkflowExecutionHandoff:
    """Translate the neutral Approval request at the composition boundary."""

    def __init__(self, dispatcher: CommandDispatcher) -> None:
        self._dispatcher = dispatcher

    def dispatch(self, request: PaperExecutionHandoff) -> ApprovalExecutionResult:
        command = ExecuteOrderCommand(
            command_id=request.command_id,
            correlation_id=request.correlation_id,
            causation_id=None,
            idempotency_id=request.idempotency_id,
            issued_at=request.issued_at,
            actor_id="approval-handoff",
            input_digest=request.input_digest,
            proposal_id=request.proposal_id,
            order_hash=request.order_hash,
            client_order_id=request.client_order_id,
            approval_event_id=request.approval_event_id,
        )
        event = self._dispatcher.dispatch(command)
        if isinstance(event, OrderExecutedEvent):
            if (
                event.proposal_id != request.proposal_id
                or event.client_order_id != request.client_order_id
            ):
                raise ApprovalHandoffContractError("workflow result subject does not match request")
        elif isinstance(event, CommandRejectedEvent):
            if event.command_type is not CommandType.EXECUTE_ORDER or event.subject_id not in {
                None,
                request.proposal_id,
            }:
                raise ApprovalHandoffContractError("workflow rejection does not match request")
        else:
            raise ApprovalHandoffContractError("workflow returned the wrong event type")

        outcome = {
            CommandOutcome.SUCCEEDED: ApprovalExecutionOutcome.SUCCEEDED,
            CommandOutcome.REJECTED: ApprovalExecutionOutcome.REJECTED,
            CommandOutcome.UNKNOWN: ApprovalExecutionOutcome.RETRY_LATER,
            CommandOutcome.SUBMIT_UNKNOWN: ApprovalExecutionOutcome.RETRY_LATER,
            CommandOutcome.NEEDS_REVIEW: ApprovalExecutionOutcome.RETRY_LATER,
        }.get(event.outcome)
        if outcome is None:
            raise ApprovalHandoffContractError("workflow returned an unsupported outcome")
        return ApprovalExecutionResult(
            command_id=request.command_id,
            correlation_id=event.correlation_id,
            idempotency_id=event.idempotency_id,
            proposal_id=request.proposal_id,
            client_order_id=request.client_order_id,
            outcome=outcome,
            event_id=event.event_id,
            reason_code=event.reason_code,
            output_digest=digest_json(event.model_dump(mode="json")),
        )


__all__ = ["WorkflowExecutionHandoff"]
