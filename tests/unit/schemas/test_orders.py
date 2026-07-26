"""Unit tests for order, risk, approval, and broker schemas (P02-T3)."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from ainvest.approval.order_hash import attach_order_hash, compute_order_hash
from ainvest.schemas.approval import ApprovalChallenge, ApprovalEvent
from ainvest.schemas.broker import BrokerOrder, CancelCommand, ReconciliationResult
from ainvest.schemas.orders import CandidateOrder, OrderProposal, order_proposal_example
from ainvest.schemas.risk import RiskDecision


def _valid_proposal() -> dict[str, Any]:
    return attach_order_hash(order_proposal_example())


@pytest.mark.unit
def test_design_order_proposal_example_round_trips() -> None:
    payload = _valid_proposal()
    proposal = OrderProposal.model_validate(payload)
    assert proposal.symbol == "AAPL"
    assert proposal.order_hash == compute_order_hash(payload)
    raw = json.loads(proposal.model_dump_json())
    again = OrderProposal.model_validate(raw)
    assert again.order_hash == proposal.order_hash
    assert again.limit_price == proposal.limit_price


@pytest.mark.unit
def test_rejects_unsupported_order_shape() -> None:
    payload = _valid_proposal()
    payload["order_type"] = "MARKET"
    with pytest.raises(ValidationError):
        OrderProposal.model_validate(payload)

    payload = _valid_proposal()
    payload["time_in_force"] = "GTC"
    with pytest.raises(ValidationError):
        OrderProposal.model_validate(payload)

    payload = _valid_proposal()
    payload["side"] = "SHORT"
    with pytest.raises(ValidationError):
        OrderProposal.model_validate(payload)

    payload = _valid_proposal()
    payload["asset_type"] = "OPTION"
    with pytest.raises(ValidationError):
        OrderProposal.model_validate(payload)


@pytest.mark.unit
def test_candidate_order_enforces_notional_and_increments() -> None:
    payload = _valid_proposal()
    payload["candidate_id"] = "cand_01HZYEXAMPLE0001"
    payload.pop("proposal_id", None)
    payload.pop("risk_decision_id", None)
    payload.pop("order_hash", None)
    payload["reason_codes"] = ["SIZED_TO_TARGET_WEIGHT"]
    candidate = CandidateOrder.model_validate(payload)
    assert candidate.side.value == "BUY"

    bad = deepcopy(payload)
    bad["quantity"] = "2.5"
    bad["quantity_increment"] = "1"
    with pytest.raises(ValidationError, match="quantity_increment"):
        CandidateOrder.model_validate(bad)

    oversized = deepcopy(payload)
    oversized["maximum_notional"] = "100.00"
    with pytest.raises(ValidationError, match="maximum_notional"):
        CandidateOrder.model_validate(oversized)


@pytest.mark.unit
def test_approval_rejects_telegram_live_and_webauthn_paper() -> None:
    base = {
        "challenge_id": "apch_01HZYEXAMPLE0001",
        "proposal_id": "ordp_01HZYEXAMPLE0001",
        "order_hash": _valid_proposal()["order_hash"],
        "nonce_hash": "a" * 64,
        "created_at": "2026-07-24T18:30:12Z",
        "expires_at": "2026-07-24T18:32:12Z",
    }
    with pytest.raises(ValidationError, match="telegram"):
        ApprovalChallenge.model_validate({**base, "method": "telegram", "scope": "live"})
    with pytest.raises(ValidationError, match="webauthn"):
        ApprovalEvent.model_validate(
            {
                "event_id": "apev_01HZYEXAMPLE0001",
                "challenge_id": "apch_01HZYEXAMPLE0001",
                "proposal_id": "ordp_01HZYEXAMPLE0001",
                "order_hash": base["order_hash"],
                "method": "webauthn",
                "scope": "paper",
                "outcome": "APPROVED",
                "approved_at": "2026-07-24T18:31:00Z",
                "approver_identity": "user:1",
            }
        )

    challenge = ApprovalChallenge.model_validate({**base, "method": "telegram", "scope": "paper"})
    assert challenge.scope.value == "paper"


@pytest.mark.unit
def test_risk_decision_requires_stable_reason_and_hard_reject_rules() -> None:
    approved = RiskDecision.model_validate(
        {
            "risk_decision_id": "risk_01HZYEXAMPLE0001",
            "candidate_id": "cand_01HZYEXAMPLE0001",
            "outcome": "APPROVED",
            "decided_at": "2026-07-24T18:30:11Z",
            "rule_set_version": "1.0.0",
            "violations": [],
            "reason_code": "ALL_RULES_PASSED",
        }
    )
    assert approved.outcome.value == "APPROVED"

    with pytest.raises(ValidationError, match="HARD"):
        RiskDecision.model_validate(
            {
                "risk_decision_id": "risk_01HZYEXAMPLE0002",
                "candidate_id": "cand_01HZYEXAMPLE0001",
                "outcome": "REJECTED",
                "decided_at": "2026-07-24T18:30:11Z",
                "rule_set_version": "1.0.0",
                "violations": [],
                "reason_code": "MISSING_LIMIT",
            }
        )

    rejected = RiskDecision.model_validate(
        {
            "risk_decision_id": "risk_01HZYEXAMPLE0003",
            "candidate_id": "cand_01HZYEXAMPLE0001",
            "outcome": "REJECTED",
            "decided_at": "2026-07-24T18:30:11Z",
            "rule_set_version": "1.0.0",
            "violations": [
                {
                    "rule_code": "MAX_ORDER_NOTIONAL",
                    "severity": "HARD",
                    "reason": "exceeds configured notional",
                }
            ],
            "reason_code": "MAX_ORDER_NOTIONAL",
        }
    )
    assert rejected.violations[0].severity.value == "HARD"


@pytest.mark.unit
def test_cancel_and_broker_schemas_are_separate_from_replace() -> None:
    order_hash = _valid_proposal()["order_hash"]
    cancel = CancelCommand.model_validate(
        {
            "cancel_id": "cncl_01HZYEXAMPLE0001",
            "proposal_id": "ordp_01HZYEXAMPLE0001",
            "broker_order_id": "brk_ord_1",
            "order_hash": order_hash,
            "account_scope": "paper",
            "reason_code": "USER_REQUESTED",
            "idempotency_key": "cancel-key-0001",
            "requested_at": "2026-07-24T18:31:00Z",
        }
    )
    assert cancel.broker_order_id == "brk_ord_1"

    broker = BrokerOrder.model_validate(
        {
            "broker_order_id": "brk_ord_1",
            "client_order_id": "client_ord_1",
            "proposal_id": "ordp_01HZYEXAMPLE0001",
            "order_hash": order_hash,
            "account_scope": "paper",
            "side": "BUY",
            "status": "ACCEPTED",
            "submitted_at": "2026-07-24T18:30:20Z",
            "updated_at": "2026-07-24T18:30:20Z",
        }
    )
    assert broker.status.value == "ACCEPTED"

    with pytest.raises(ValidationError):
        ReconciliationResult.model_validate(
            {
                "reconciliation_id": "recon_01HZYEXAMPLE01",
                "outcome": "MATCHED",
                "reason_code": "MATCHED",
                "observed_at": "2026-07-24T18:31:00Z",
            }
        )
