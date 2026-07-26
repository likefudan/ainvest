"""Unit tests for order, risk, approval, and broker schemas (P02-T3)."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from ainvest.approval.order_hash import (
    attach_order_hash,
    compute_order_hash,
    parse_order_proposal,
)
from ainvest.schemas.approval import ApprovalChallenge, ApprovalEvent
from ainvest.schemas.broker import BrokerOrder, CancelCommand, ReconciliationResult
from ainvest.schemas.orders import CandidateOrder, OrderProposal, order_proposal_example
from ainvest.schemas.risk import RiskDecision


def _valid_proposal() -> dict[str, Any]:
    return attach_order_hash(order_proposal_example())


@pytest.mark.unit
def test_design_order_proposal_example_round_trips() -> None:
    payload = _valid_proposal()
    proposal = parse_order_proposal(payload)
    assert proposal.symbol == "AAPL"
    assert proposal.order_hash == compute_order_hash(payload)
    raw = json.loads(proposal.model_dump_json())
    again = parse_order_proposal(raw)
    assert again.order_hash == proposal.order_hash
    assert again.limit_price == proposal.limit_price


@pytest.mark.unit
def test_parse_order_proposal_rejects_stale_or_tampered_hash() -> None:
    payload = _valid_proposal()
    payload["order_hash"] = "sha256:" + ("0" * 64)
    # Structural validate still accepts a syntactically valid digest...
    OrderProposal.model_validate(payload)
    # ...but the public construction path must reject the mismatch.
    with pytest.raises(ValueError, match="order_hash"):
        parse_order_proposal(payload)


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
def test_candidate_and_proposal_enforce_price_and_quantity_increments() -> None:
    payload = _valid_proposal()
    payload["candidate_id"] = "cand_01HZYEXAMPLE0001"
    payload.pop("proposal_id", None)
    payload.pop("risk_decision_id", None)
    payload.pop("order_hash", None)
    payload["reason_codes"] = ["SIZED_TO_TARGET_WEIGHT"]
    candidate = CandidateOrder.model_validate(payload)
    assert candidate.side.value == "BUY"

    bad_qty = deepcopy(payload)
    bad_qty["quantity"] = "2.5"
    bad_qty["quantity_increment"] = "1"
    with pytest.raises(ValidationError, match="quantity"):
        CandidateOrder.model_validate(bad_qty)

    off_tick = deepcopy(payload)
    off_tick["limit_price"] = "214.505"
    off_tick["price_increment"] = "0.01"
    with pytest.raises(ValidationError, match="limit_price"):
        CandidateOrder.model_validate(off_tick)
    proposal_off_tick = attach_order_hash(
        {
            **{k: v for k, v in off_tick.items() if k != "reason_codes"},
            **_proposal_ids(),
        }
    )
    with pytest.raises(ValidationError, match="limit_price"):
        OrderProposal.model_validate(proposal_off_tick)
    exact = deepcopy(payload)
    exact["limit_price"] = "214.50"
    exact["price_increment"] = "0.01"
    CandidateOrder.model_validate(exact)

    oversized = deepcopy(payload)
    oversized["maximum_notional"] = "100.00"
    with pytest.raises(ValidationError, match="maximum_notional"):
        CandidateOrder.model_validate(oversized)


@pytest.mark.unit
def test_candidate_reason_codes_treat_null_as_empty() -> None:
    payload = _valid_proposal()
    payload["candidate_id"] = "cand_01HZYEXAMPLE0001"
    payload.pop("proposal_id", None)
    payload.pop("risk_decision_id", None)
    payload.pop("order_hash", None)
    payload["reason_codes"] = None
    candidate = CandidateOrder.model_validate(payload)
    assert candidate.reason_codes == ()


@pytest.mark.unit
def test_large_significand_increment_and_notional_are_exact() -> None:
    """Default Decimal precision must not accept off-increment or over-notional."""
    payload = _valid_proposal()
    payload["candidate_id"] = "cand_01HZYEXAMPLE0001"
    payload.pop("proposal_id", None)
    payload.pop("risk_decision_id", None)
    payload.pop("order_hash", None)
    payload["reason_codes"] = ["SIZED_TO_TARGET_WEIGHT"]

    off_increment = deepcopy(payload)
    off_increment["quantity"] = "10000000000000000000000000000.5"
    off_increment["quantity_increment"] = "1"
    off_increment["limit_price"] = "1"
    off_increment["price_increment"] = "1"
    off_increment["maximum_notional"] = "999999999999999999999999999999999"
    with pytest.raises(ValidationError, match="quantity"):
        CandidateOrder.model_validate(off_increment)

    # 9999999999999999^2 exceeds the rounded Decimal product by 1.
    over_notional = deepcopy(payload)
    over_notional["quantity"] = "9999999999999999"
    over_notional["quantity_increment"] = "1"
    over_notional["limit_price"] = "9999999999999999"
    over_notional["price_increment"] = "1"
    over_notional["maximum_notional"] = "99999999999999980000000000000000"
    with pytest.raises(ValidationError, match="maximum_notional"):
        CandidateOrder.model_validate(over_notional)

    exact = deepcopy(over_notional)
    exact["maximum_notional"] = "99999999999999980000000000000001"
    CandidateOrder.model_validate(exact)


@pytest.mark.unit
def test_extreme_exponents_rejected_before_power_operations() -> None:
    from decimal import Decimal

    payload = _valid_proposal()
    payload["candidate_id"] = "cand_01HZYEXAMPLE0001"
    payload.pop("proposal_id", None)
    payload.pop("risk_decision_id", None)
    payload.pop("order_hash", None)
    payload["reason_codes"] = ["SIZED_TO_TARGET_WEIGHT"]

    scientific = deepcopy(payload)
    scientific["quantity"] = "1e1000000"
    with pytest.raises(ValidationError, match="decimal"):
        CandidateOrder.model_validate(scientific)

    # Bypass string pattern with a pre-built Decimal; helpers must still refuse.
    huge = deepcopy(payload)
    huge["quantity"] = Decimal("1e1000000")
    huge["quantity_increment"] = Decimal("1")
    huge["limit_price"] = Decimal("1")
    huge["price_increment"] = Decimal("1")
    huge["maximum_notional"] = Decimal("1")
    with pytest.raises(ValidationError, match="exponent"):
        CandidateOrder.model_validate(huge)

    tiny = deepcopy(huge)
    tiny["quantity"] = Decimal("1")
    tiny["limit_price"] = Decimal("1e-1000000")
    with pytest.raises(ValidationError, match="exponent"):
        CandidateOrder.model_validate(tiny)

    # Extreme-exponent zero must canonicalize before notional power ops.
    zero_max = deepcopy(payload)
    zero_max["quantity"] = "1"
    zero_max["limit_price"] = "1"
    zero_max["price_increment"] = "1"
    zero_max["quantity_increment"] = "1"
    zero_max["maximum_notional"] = Decimal("0e1000000")
    with pytest.raises(ValidationError, match="maximum_notional"):
        CandidateOrder.model_validate(zero_max)


def _proposal_ids() -> dict[str, str]:
    return {
        "proposal_id": "ordp_01HZYEXAMPLE0001",
        "risk_decision_id": "risk_01HZYEXAMPLE0001",
    }


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
def test_risk_decision_requires_human_reason_and_outcome_consistency() -> None:
    approved = RiskDecision.model_validate(
        {
            "risk_decision_id": "risk_01HZYEXAMPLE0001",
            "candidate_id": "cand_01HZYEXAMPLE0001",
            "outcome": "APPROVED",
            "decided_at": "2026-07-24T18:30:11Z",
            "rule_set_version": "1.0.0",
            "violations": [],
            "reason_code": "ALL_RULES_PASSED",
            "reason": "all hard and review rules passed",
        }
    )
    assert approved.reason.startswith("all hard")

    with pytest.raises(ValidationError, match="reason"):
        RiskDecision.model_validate(
            {
                "risk_decision_id": "risk_01HZYEXAMPLE0002",
                "candidate_id": "cand_01HZYEXAMPLE0001",
                "outcome": "APPROVED",
                "decided_at": "2026-07-24T18:30:11Z",
                "rule_set_version": "1.0.0",
                "violations": [],
                "reason_code": "ALL_RULES_PASSED",
            }
        )

    with pytest.raises(ValidationError, match="HARD"):
        RiskDecision.model_validate(
            {
                "risk_decision_id": "risk_01HZYEXAMPLE0003",
                "candidate_id": "cand_01HZYEXAMPLE0001",
                "outcome": "REJECTED",
                "decided_at": "2026-07-24T18:30:11Z",
                "rule_set_version": "1.0.0",
                "violations": [],
                "reason_code": "MISSING_LIMIT",
                "reason": "missing required limit",
            }
        )

    with pytest.raises(ValidationError, match="REVIEW"):
        RiskDecision.model_validate(
            {
                "risk_decision_id": "risk_01HZYEXAMPLE0004",
                "candidate_id": "cand_01HZYEXAMPLE0001",
                "outcome": "NEEDS_REVIEW",
                "decided_at": "2026-07-24T18:30:11Z",
                "rule_set_version": "1.0.0",
                "violations": [],
                "reason_code": "EMPTY_REVIEW",
                "reason": "empty needs review",
            }
        )

    with pytest.raises(ValidationError, match="REVIEW"):
        RiskDecision.model_validate(
            {
                "risk_decision_id": "risk_01HZYEXAMPLE0005",
                "candidate_id": "cand_01HZYEXAMPLE0001",
                "outcome": "NEEDS_REVIEW",
                "decided_at": "2026-07-24T18:30:11Z",
                "rule_set_version": "1.0.0",
                "violations": [
                    {
                        "rule_code": "INFO_ONLY",
                        "severity": "INFO",
                        "reason": "informational note",
                    }
                ],
                "reason_code": "INFO_ONLY",
                "reason": "info only cannot become needs review",
            }
        )

    rejected = RiskDecision.model_validate(
        {
            "risk_decision_id": "risk_01HZYEXAMPLE0006",
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
            "reason": "order notional exceeds configured maximum",
        }
    )
    assert rejected.violations[0].severity.value == "HARD"

    needs_review = RiskDecision.model_validate(
        {
            "risk_decision_id": "risk_01HZYEXAMPLE0007",
            "candidate_id": "cand_01HZYEXAMPLE0001",
            "outcome": "NEEDS_REVIEW",
            "decided_at": "2026-07-24T18:30:11Z",
            "rule_set_version": "1.0.0",
            "violations": [
                {
                    "rule_code": "SECTOR_NEAR_LIMIT",
                    "severity": "REVIEW",
                    "reason": "sector weight near configured soft limit",
                }
            ],
            "reason_code": "SECTOR_NEAR_LIMIT",
            "reason": "sector exposure requires manual review",
        }
    )
    assert needs_review.outcome.value == "NEEDS_REVIEW"


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
