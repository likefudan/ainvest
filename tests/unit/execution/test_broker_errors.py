"""Unit tests for the broker port error taxonomy and request models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ainvest.execution.broker import (
    BrokerAuthError,
    BrokerError,
    BrokerInvalidOrderError,
    BrokerRateLimitError,
    BrokerRejectedError,
    BrokerSubmitOutcome,
    BrokerSubmitRequest,
    BrokerSubmitResult,
    BrokerTimeoutError,
    BrokerUnknownOutcomeError,
    cancel_is_confirmed_rejection,
    cancel_is_unknown_outcome,
)
from ainvest.schemas.approval import ApprovalEvent
from ainvest.schemas.broker import BrokerOrder, CancelResult, CancelStatus
from ainvest.schemas.examples import (
    approval_event_example,
    broker_order_example,
    cancel_result_example,
    order_proposal_valid,
)
from ainvest.schemas.orders import OrderProposal
from execution.paper_fixtures import (
    make_paper_proposal,
    make_paper_proposal_and_approval,
    make_submit_request,
)

_OBSERVED = datetime(2026, 7, 24, 18, 30, 20, tzinfo=UTC)


@pytest.mark.unit
def test_broker_error_base_is_abstract() -> None:
    with pytest.raises(TypeError, match="abstract"):
        BrokerError("x", reason_code="X")


@pytest.mark.unit
def test_error_taxonomy_codes_are_stable() -> None:
    cases: list[tuple[BrokerError, str]] = [
        (BrokerAuthError("x", reason_code="AUTH_FAILED"), "AUTH"),
        (BrokerTimeoutError("x", reason_code="READ_TIMEOUT"), "TIMEOUT"),
        (BrokerRateLimitError("x", reason_code="RATE_LIMITED"), "RATE_LIMIT"),
        (BrokerInvalidOrderError("x", reason_code="BAD_TICK"), "INVALID_ORDER"),
        (BrokerRejectedError("x", reason_code="BROKER_REJECTED"), "REJECTED"),
        (
            BrokerUnknownOutcomeError(
                "x",
                reason_code="SUBMIT_TIMEOUT",
                operation="submit",
                idempotency_key="client_ord_1",
            ),
            "UNKNOWN_OUTCOME",
        ),
    ]
    for exc, code in cases:
        assert isinstance(exc, Exception)
        assert exc.code == code


@pytest.mark.unit
def test_unknown_outcome_is_not_confirmed_rejection() -> None:
    rejected = BrokerRejectedError("confirmed", reason_code="BROKER_REJECTED")
    unknown = BrokerUnknownOutcomeError(
        "ambiguous",
        reason_code="DISCONNECT",
        operation="submit",
        idempotency_key="client_ord_1",
    )

    assert rejected.is_confirmed_rejection is True
    assert rejected.is_unknown_outcome is False
    assert isinstance(rejected, BrokerRejectedError)
    assert not isinstance(rejected, BrokerUnknownOutcomeError)

    assert unknown.is_confirmed_rejection is False
    assert unknown.is_unknown_outcome is True
    assert isinstance(unknown, BrokerUnknownOutcomeError)
    assert not isinstance(unknown, BrokerRejectedError)
    assert isinstance(unknown, BrokerError)
    assert isinstance(rejected, BrokerError)
    assert not issubclass(BrokerUnknownOutcomeError, BrokerRejectedError)
    assert not issubclass(BrokerRejectedError, BrokerUnknownOutcomeError)


@pytest.mark.unit
def test_submit_result_distinguishes_rejected_and_unknown() -> None:
    rejected = BrokerSubmitResult.model_validate(
        {
            "outcome": "REJECTED",
            "client_order_id": "client_ord_1",
            "observed_at": "2026-07-24T18:30:20Z",
            "reason_code": "BROKER_REJECTED",
        }
    )
    unknown = BrokerSubmitResult.model_validate(
        {
            "outcome": "UNKNOWN",
            "client_order_id": "client_ord_1",
            "observed_at": "2026-07-24T18:30:20Z",
            "reason_code": "SUBMIT_TIMEOUT",
        }
    )
    accepted = BrokerSubmitResult(
        outcome=BrokerSubmitOutcome.ACCEPTED,
        client_order_id="client_ord_1",
        observed_at=_OBSERVED,
        broker_order=BrokerOrder.model_validate(broker_order_example()),
    )

    assert rejected.is_confirmed_rejection is True
    assert rejected.is_unknown_outcome is False
    assert unknown.is_confirmed_rejection is False
    assert unknown.is_unknown_outcome is True
    assert accepted.is_confirmed_rejection is False
    assert accepted.is_unknown_outcome is False


@pytest.mark.unit
def test_cancel_helpers_distinguish_rejected_and_unknown() -> None:
    rejected = CancelResult.model_validate(
        {
            **cancel_result_example(),
            "status": CancelStatus.REJECTED.value,
            "reason_code": "CANCEL_REJECTED",
        }
    )
    unknown = CancelResult.model_validate(
        {
            **cancel_result_example(),
            "status": CancelStatus.UNKNOWN.value,
            "reason_code": "CANCEL_TIMEOUT",
        }
    )
    assert cancel_is_confirmed_rejection(rejected) is True
    assert cancel_is_unknown_outcome(rejected) is False
    assert cancel_is_confirmed_rejection(unknown) is False
    assert cancel_is_unknown_outcome(unknown) is True


@pytest.mark.unit
def test_submit_request_requires_matching_approved_event() -> None:
    request = make_submit_request()
    assert request.client_order_id == "client_ord_1"
    assert request.approval.order_hash == request.proposal.order_hash
    assert request.proposal.account_scope.value == "paper"
    assert request.approval.scope.value == "paper"

    proposal, _approval = make_paper_proposal_and_approval()
    bad_approval = approval_event_example()
    bad_approval["outcome"] = "DENIED"
    bad_approval["order_hash"] = proposal.order_hash
    bad_approval["proposal_id"] = proposal.proposal_id
    with pytest.raises(ValidationError):
        BrokerSubmitRequest(
            proposal=proposal,
            approval=ApprovalEvent.model_validate(bad_approval),
            client_order_id="client_ord_1",
        )

    mismatched = {
        **approval_event_example(),
        "proposal_id": proposal.proposal_id,
        "order_hash": "sha256:" + ("b" * 64),
        "scope": "paper",
        "method": "telegram",
    }
    with pytest.raises(ValidationError):
        BrokerSubmitRequest(
            proposal=proposal,
            approval=ApprovalEvent.model_validate(mismatched),
            client_order_id="client_ord_1",
        )


@pytest.mark.unit
def test_submit_request_rejects_paper_approval_for_agentic_proposal() -> None:
    """Fail closed: telegram+paper must not authorize agentic/live submit."""
    agentic = OrderProposal.model_validate(order_proposal_valid())
    paper_approval = ApprovalEvent.model_validate(
        {
            **approval_event_example(),
            "proposal_id": agentic.proposal_id,
            "order_hash": agentic.order_hash,
        }
    )
    with pytest.raises(ValidationError, match=r"requires proposal\.account_scope"):
        BrokerSubmitRequest(
            proposal=agentic,
            approval=paper_approval,
            client_order_id="client_ord_1",
        )


@pytest.mark.unit
def test_submit_request_rejects_live_approval_for_paper_proposal() -> None:
    proposal = make_paper_proposal()
    live_approval = ApprovalEvent.model_validate(
        {
            **approval_event_example(),
            "proposal_id": proposal.proposal_id,
            "order_hash": proposal.order_hash,
            "method": "webauthn",
            "scope": "live",
            "approver_identity": "passkey_user_1",
        }
    )
    with pytest.raises(ValidationError, match=r"requires proposal\.account_scope"):
        BrokerSubmitRequest(
            proposal=proposal,
            approval=live_approval,
            client_order_id="client_ord_1",
        )


@pytest.mark.unit
def test_submit_request_accepts_live_approval_for_agentic_proposal() -> None:
    agentic = OrderProposal.model_validate(order_proposal_valid())
    live_approval = ApprovalEvent.model_validate(
        {
            **approval_event_example(),
            "proposal_id": agentic.proposal_id,
            "order_hash": agentic.order_hash,
            "method": "webauthn",
            "scope": "live",
            "approver_identity": "passkey_user_1",
        }
    )
    request = BrokerSubmitRequest(
        proposal=agentic,
        approval=live_approval,
        client_order_id="client_ord_1",
    )
    assert request.proposal.account_scope.value == "agentic"
    assert request.approval.scope.value == "live"


@pytest.mark.unit
def test_accepted_submit_requires_matching_broker_order() -> None:
    with pytest.raises(ValidationError):
        BrokerSubmitResult.model_validate(
            {
                "outcome": "ACCEPTED",
                "client_order_id": "client_ord_1",
                "observed_at": "2026-07-24T18:30:20Z",
            }
        )

    order = broker_order_example()
    order["client_order_id"] = "client_ord_other"
    with pytest.raises(ValidationError):
        BrokerSubmitResult(
            outcome=BrokerSubmitOutcome.ACCEPTED,
            client_order_id="client_ord_1",
            observed_at=_OBSERVED,
            broker_order=BrokerOrder.model_validate(order),
        )
