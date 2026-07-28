"""P05-T0 proposal freezing and one-time approval service tests."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from risk.risk_fixtures import make_context
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.service import ApprovalService, ApprovalServiceError
from ainvest.approval.tokens import OpaqueApprovalToken, hash_approval_token
from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.uow import UnitOfWork
from ainvest.risk.engine import (
    RiskEngineOutput,
    compute_config_digest,
    compute_input_digest,
)
from ainvest.risk.models import RiskContext
from ainvest.schemas.approval import (
    ApprovalChallenge,
    ApprovalChallengeStatus,
    ApprovalEvent,
    ApprovalEventOutcome,
    ApprovalMethod,
    ApprovalScope,
)
from ainvest.schemas.examples import candidate_order_example, risk_decision_example
from ainvest.schemas.orders import CandidateOrder
from ainvest.schemas.risk import RiskDecision

NOW = datetime(2026, 7, 24, 18, 30, 12, tzinfo=UTC)
FIXED_TOKEN_VALUE = "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg"
FIXED_TOKEN = OpaqueApprovalToken(FIXED_TOKEN_VALUE)


def _session_factory(path: Path) -> sessionmaker[Session]:
    engine = create_db_engine(
        f"sqlite+pysqlite:///{path}",
        connect_args={"timeout": 10},
    )
    create_all_tables(engine)
    return create_session_factory(engine)


def _candidate(*, scope: str = "paper", expires_in: int = 120) -> CandidateOrder:
    payload = candidate_order_example()
    payload["account_scope"] = scope
    payload["created_at"] = NOW.isoformat()
    payload["expires_at"] = (NOW + timedelta(seconds=expires_in)).isoformat()
    return CandidateOrder.model_validate(payload)


def _risk(
    candidate: CandidateOrder,
    *,
    outcome: str = "APPROVED",
    risk_decision_id: str = "risk_01HZYEXAMPLE0001",
) -> tuple[RiskEngineOutput, RiskContext]:
    context = make_context(
        risk_decision_id=risk_decision_id,
        as_of=NOW,
        candidate=candidate,
    )
    payload = risk_decision_example()
    payload["risk_decision_id"] = risk_decision_id
    payload["candidate_id"] = candidate.candidate_id
    payload["outcome"] = outcome
    payload["rule_set_version"] = context.config.rule_set_version
    if outcome == "REJECTED":
        payload["violations"] = [
            {
                "rule_code": "LIMIT_EXCEEDED",
                "severity": "HARD",
                "reason": "test rejection",
            }
        ]
        payload["reason_code"] = "LIMIT_EXCEEDED"
        payload["reason"] = "test rejection"
    decision = RiskDecision.model_validate(payload)
    return (
        RiskEngineOutput(
            decision=decision,
            input_digest=compute_input_digest(context),
            config_digest=compute_config_digest(context.config),
            rule_codes=(),
            rule_results=(),
        ),
        context,
    )


def _ids(prefix: str) -> str:
    return {
        "ordp": "ordp_01HZYAPPROVAL0001",
        "apch": "apch_01HZYAPPROVAL0001",
        "apev": "apev_01HZYAPPROVAL0001",
    }[prefix]


def _service(
    uow: UnitOfWork,
    *,
    now: datetime = NOW,
    event_suffix: str = "",
) -> ApprovalService:
    return ApprovalService.from_uow(
        uow,
        clock=lambda: now,
        id_factory=lambda prefix: (
            f"apev_01HZYAPPROVAL{event_suffix or '0001'}" if prefix == "apev" else _ids(prefix)
        ),
        token_factory=lambda: FIXED_TOKEN,
    )


def _issue(
    factory: sessionmaker[Session],
    *,
    candidate: CandidateOrder | None = None,
    risk: tuple[RiskEngineOutput, RiskContext] | None = None,
) -> None:
    selected_candidate = candidate or _candidate()
    selected_output, selected_context = risk or _risk(selected_candidate)
    with UnitOfWork(factory) as uow:
        _service(uow).create_proposal_and_challenge(
            selected_candidate,
            selected_output,
            risk_context=selected_context,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
        )


@pytest.mark.unit
def test_creation_freezes_proposal_risk_and_only_token_hash(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "approval.db")
    candidate = _candidate()
    output, context = _risk(candidate)

    with UnitOfWork(factory) as uow:
        issued = _service(uow).create_proposal_and_challenge(
            candidate,
            output,
            risk_context=context,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
        )
        assert issued.challenge.expires_at - issued.challenge.created_at == timedelta(seconds=120)
        assert issued.proposal.risk_decision_id == output.decision.risk_decision_id
        assert issued.challenge.order_hash == issued.proposal.order_hash
        assert not is_dataclass(issued)
        with pytest.raises(TypeError):
            asdict(issued)  # type: ignore[call-overload]
        with pytest.raises(TypeError):
            vars(issued)
        assert FIXED_TOKEN_VALUE not in json.dumps({"issued": issued}, default=str)
        with pytest.raises(ValidationError):
            issued.proposal.quantity = Decimal("3")

    with UnitOfWork(factory) as uow:
        proposal = uow.proposals_repo.get_by_proposal_id(_ids("ordp"))
        challenge = uow.approvals_repo.get_challenge(_ids("apch"))
        stored_risk = uow.risk_decisions_repo.get(output.decision.risk_decision_id)
        assert proposal is not None and challenge is not None and stored_risk is not None
        assert proposal.payload_json == issued.proposal.model_dump(mode="json")
        assert stored_risk.payload_json == output.model_dump(mode="json")
        assert stored_risk.proposal_id == issued.proposal.proposal_id
        assert challenge.token_hash == hash_approval_token(FIXED_TOKEN)
        persisted = json.dumps(
            [proposal.payload_json, challenge.payload_json, stored_risk.payload_json],
            sort_keys=True,
        )
        assert FIXED_TOKEN_VALUE not in persisted


@pytest.mark.unit
@pytest.mark.parametrize("seconds", [59, 121])
def test_ttl_outside_60_to_120_seconds_is_rejected(tmp_path: Path, seconds: int) -> None:
    factory = _session_factory(tmp_path / f"ttl-{seconds}.db")
    candidate = _candidate(expires_in=300)
    output, context = _risk(candidate)
    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            candidate,
            output,
            risk_context=context,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
            ttl=timedelta(seconds=seconds),
        )
    assert caught.value.code == "INVALID_APPROVAL_TTL"


@pytest.mark.unit
@pytest.mark.parametrize("lifetime_seconds", [1, 300])
def test_approval_challenge_v1_preserves_general_positive_lifetime(
    lifetime_seconds: int,
) -> None:
    challenge = ApprovalChallenge(
        challenge_id="apch_01HZYAPPROVAL0001",
        proposal_id="ordp_01HZYAPPROVAL0001",
        order_hash="sha256:" + ("0" * 64),
        method=ApprovalMethod.TELEGRAM,
        scope=ApprovalScope.PAPER,
        nonce_hash="a" * 64,
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=lifetime_seconds),
        status=ApprovalChallengeStatus.CANCELLED,
    )
    assert challenge.status is ApprovalChallengeStatus.CANCELLED


@pytest.mark.unit
def test_proposal_must_outlive_challenge(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "short-proposal.db")
    candidate = _candidate(expires_in=90)
    output, context = _risk(candidate)
    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            candidate,
            output,
            risk_context=context,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
            ttl=timedelta(seconds=120),
        )
    assert caught.value.code == "PROPOSAL_EXPIRES_TOO_SOON"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "scope"),
    [
        (ApprovalMethod.TELEGRAM, ApprovalScope.LIVE),
        (ApprovalMethod.WEBAUTHN, ApprovalScope.PAPER),
    ],
)
def test_forbidden_method_scope_combinations_never_persist(
    tmp_path: Path,
    method: ApprovalMethod,
    scope: ApprovalScope,
) -> None:
    factory = _session_factory(tmp_path / f"scope-{method.value}-{scope.value}.db")
    account = "agentic" if scope is ApprovalScope.LIVE else "paper"
    candidate = _candidate(scope=account)
    output, context = _risk(candidate)
    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            candidate,
            output,
            risk_context=context,
            method=method,
            scope=scope,
        )
    assert caught.value.code == "FORBIDDEN_APPROVAL_SCOPE"

    with UnitOfWork(factory) as uow:
        assert uow.proposals_repo.get_by_proposal_id(_ids("ordp")) is None
        assert uow.approvals_repo.get_challenge(_ids("apch")) is None


@pytest.mark.unit
def test_scope_must_match_candidate_account(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "account-scope.db")
    candidate = _candidate(scope="agentic")
    output, context = _risk(candidate)
    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            candidate,
            output,
            risk_context=context,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
        )
    assert caught.value.code == "APPROVAL_ACCOUNT_SCOPE_MISMATCH"


@pytest.mark.unit
def test_webauthn_live_is_the_only_supported_live_pair(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "live-pair.db")
    candidate = _candidate(scope="agentic")
    output, context = _risk(candidate)
    with UnitOfWork(factory) as uow:
        issued = _service(uow).create_proposal_and_challenge(
            candidate,
            output,
            risk_context=context,
            method=ApprovalMethod.WEBAUTHN,
            scope=ApprovalScope.LIVE,
        )
        assert issued.challenge.method is ApprovalMethod.WEBAUTHN
        assert issued.challenge.scope is ApprovalScope.LIVE


@pytest.mark.unit
def test_non_approved_or_mismatched_risk_cannot_create_proposal(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "risk.db")
    candidate = _candidate()
    rejected, rejected_context = _risk(candidate, outcome="REJECTED")
    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            candidate,
            rejected,
            risk_context=rejected_context,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
        )
    assert caught.value.code == "RISK_NOT_APPROVED"

    other = _candidate().model_copy(update={"candidate_id": "cand_01HZYOTHER00001"})
    output, context = _risk(candidate)
    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            other,
            output,
            risk_context=context,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
        )
    assert caught.value.code == "RISK_CANDIDATE_MISMATCH"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", "1"),
        ("limit_price", "200.00"),
        ("maximum_notional", "1000.00"),
        ("account_scope", "agentic"),
        ("expires_at", (NOW + timedelta(seconds=240)).isoformat()),
        ("strategy_version", "2.0.0"),
    ],
)
def test_risk_digest_rejects_changed_candidate_economics_or_scope(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    factory = _session_factory(tmp_path / f"risk-digest-{field}.db")
    original = _candidate()
    output, _ = _risk(original)
    changed_payload = original.model_dump(mode="json")
    changed_payload[field] = value
    changed = CandidateOrder.model_validate(changed_payload)
    _, changed_context = _risk(changed)

    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            changed,
            output,
            risk_context=changed_context,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
        )
    assert caught.value.code == "RISK_INPUT_DIGEST_MISMATCH"


@pytest.mark.unit
def test_one_risk_decision_cannot_create_multiple_proposals(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "risk-reuse.db")
    candidate = _candidate()
    output, context = _risk(candidate)

    with UnitOfWork(factory) as uow:
        _service(uow).create_proposal_and_challenge(
            candidate,
            output,
            risk_context=context,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
        )

    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            candidate,
            output,
            risk_context=context,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
            proposal_id="ordp_01HZYAPPROVAL0002",
            challenge_id="apch_01HZYAPPROVAL0002",
        )
    assert caught.value.code == "RISK_DECISION_ALREADY_USED"

    with UnitOfWork(factory) as uow:
        stored = uow.risk_decisions_repo.get(output.decision.risk_decision_id)
        assert stored is not None
        assert stored.proposal_id == _ids("ordp")
        assert uow.proposals_repo.get_by_proposal_id("ordp_01HZYAPPROVAL0002") is None
        assert uow.approvals_repo.get_challenge("apch_01HZYAPPROVAL0002") is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("approved", "expected_status", "expected_outcome"),
    [
        (True, ApprovalChallengeStatus.APPROVED, ApprovalEventOutcome.APPROVED),
        (False, ApprovalChallengeStatus.REJECTED, ApprovalEventOutcome.DENIED),
    ],
)
def test_decision_is_bound_and_single_use(
    tmp_path: Path,
    approved: bool,
    expected_status: ApprovalChallengeStatus,
    expected_outcome: ApprovalEventOutcome,
) -> None:
    factory = _session_factory(tmp_path / f"decision-{approved}.db")
    _issue(factory)

    with UnitOfWork(factory) as uow:
        event = _service(uow, now=NOW + timedelta(seconds=30)).decide(
            FIXED_TOKEN,
            approved=approved,
            approver_identity="telegram:12345",
        )
        assert event.outcome is expected_outcome
        assert event.method is ApprovalMethod.TELEGRAM
        assert event.scope is ApprovalScope.PAPER

    with UnitOfWork(factory) as uow:
        challenge = uow.approvals_repo.get_challenge(_ids("apch"))
        assert challenge is not None
        assert challenge.status == expected_status.value
        events = uow.approvals_repo.list_events_for_proposal(_ids("ordp"))
        assert len(events) == 1
        with pytest.raises(ApprovalServiceError) as caught:
            _service(uow, now=NOW + timedelta(seconds=31)).decide(
                FIXED_TOKEN,
                approved=approved,
                approver_identity="telegram:12345",
            )
        assert caught.value.code == "CHALLENGE_ALREADY_USED"


@pytest.mark.unit
def test_expired_token_creates_only_an_expired_event(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "expiry.db")
    candidate = _candidate()
    _issue(factory, candidate=candidate, risk=_risk(candidate))

    with UnitOfWork(factory) as uow:
        event = _service(uow, now=NOW + timedelta(seconds=120)).decide(
            FIXED_TOKEN,
            approved=True,
            approver_identity="telegram:12345",
        )
        assert event.outcome is ApprovalEventOutcome.EXPIRED

    with UnitOfWork(factory) as uow:
        challenge = uow.approvals_repo.get_challenge(_ids("apch"))
        assert challenge is not None
        assert challenge.status == ApprovalChallengeStatus.EXPIRED.value
        events = uow.approvals_repo.list_events_for_proposal(_ids("ordp"))
        assert [event.outcome for event in events] == [ApprovalEventOutcome.EXPIRED.value]


@pytest.mark.unit
def test_approval_decision_requires_an_explicit_boolean(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "strict-decision.db")
    _issue(factory)
    with UnitOfWork(factory) as uow:
        with pytest.raises(ApprovalServiceError) as caught:
            _service(uow, now=NOW + timedelta(seconds=30)).decide(
                FIXED_TOKEN,
                approved="yes",  # type: ignore[arg-type]
                approver_identity="telegram:12345",
            )
        assert caught.value.code == "INVALID_APPROVAL_DECISION"


@pytest.mark.unit
def test_changed_order_invalidates_nonce_without_consuming_it(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "tamper.db")
    _issue(factory)

    with UnitOfWork(factory) as uow:
        proposal = uow.proposals_repo.get_by_proposal_id(_ids("ordp"))
        assert proposal is not None
        changed = dict(proposal.payload_json)
        changed["quantity"] = "1"
        proposal.payload_json = changed

    with UnitOfWork(factory) as uow:
        with pytest.raises(ApprovalServiceError) as caught:
            _service(uow, now=NOW + timedelta(seconds=30)).decide(
                FIXED_TOKEN,
                approved=True,
                approver_identity="telegram:12345",
            )
        assert caught.value.code == "PROPOSAL_INTEGRITY_FAILED"

    with UnitOfWork(factory) as uow:
        challenge = uow.approvals_repo.get_challenge(_ids("apch"))
        assert challenge is not None
        assert challenge.status == ApprovalChallengeStatus.PENDING.value
        assert uow.approvals_repo.list_events_for_proposal(_ids("ordp")) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("storage", "field"),
    [
        ("payload", "created_at"),
        ("payload", "expires_at"),
        ("payload", "schema_version"),
        ("column", "created_at"),
        ("column", "expires_at"),
        ("column", "schema_version"),
    ],
)
def test_challenge_duplicate_field_tamper_fails_without_consumption(
    tmp_path: Path,
    storage: str,
    field: str,
) -> None:
    factory = _session_factory(tmp_path / f"challenge-tamper-{storage}-{field}.db")
    _issue(factory)

    with UnitOfWork(factory) as uow:
        row = uow.approvals_repo.get_challenge(_ids("apch"))
        assert row is not None
        if storage == "payload":
            payload = deepcopy(row.payload_json)
            if field == "created_at":
                payload[field] = (NOW + timedelta(seconds=1)).isoformat()
            elif field == "expires_at":
                payload[field] = (NOW + timedelta(seconds=180)).isoformat()
            else:
                payload[field] = "0.9"
            row.payload_json = payload
        elif field == "created_at":
            row.challenge_created_at = NOW + timedelta(seconds=1)
        elif field == "expires_at":
            row.expires_at = NOW + timedelta(seconds=180)
        else:
            row.schema_version = "0.9"

    with UnitOfWork(factory) as uow:
        with pytest.raises(ApprovalServiceError) as caught:
            _service(uow, now=NOW + timedelta(seconds=30)).decide(
                FIXED_TOKEN,
                approved=True,
                approver_identity="telegram:12345",
            )
        assert caught.value.code == "CHALLENGE_INTEGRITY_FAILED"

    with UnitOfWork(factory) as uow:
        challenge = uow.approvals_repo.get_challenge(_ids("apch"))
        assert challenge is not None
        assert challenge.status == ApprovalChallengeStatus.PENDING.value
        assert uow.approvals_repo.list_events_for_proposal(_ids("ordp")) == []


@pytest.mark.unit
def test_invalid_event_validation_cannot_consume_challenge_when_caught_in_uow(
    tmp_path: Path,
) -> None:
    factory = _session_factory(tmp_path / "invalid-event.db")
    _issue(factory)

    with UnitOfWork(factory) as uow:
        with pytest.raises(ApprovalServiceError) as caught:
            _service(uow, now=NOW + timedelta(seconds=30)).decide(
                FIXED_TOKEN,
                approved=True,
                approver_identity="telegram:12345",
                event_id="invalid",
            )
        assert caught.value.code == "INVALID_APPROVAL_EVENT"
        challenge = uow.approvals_repo.get_challenge(_ids("apch"))
        assert challenge is not None
        assert challenge.status == ApprovalChallengeStatus.PENDING.value
        assert uow.approvals_repo.list_events_for_proposal(_ids("ordp")) == []

    with UnitOfWork(factory) as uow:
        challenge = uow.approvals_repo.get_challenge(_ids("apch"))
        assert challenge is not None
        assert challenge.status == ApprovalChallengeStatus.PENDING.value
        assert uow.approvals_repo.list_events_for_proposal(_ids("ordp")) == []


@pytest.mark.unit
def test_event_conflict_rolls_back_transition_when_caught_in_uow(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "event-conflict.db")
    _issue(factory)
    conflicting_event = ApprovalEvent(
        event_id="apev_01HZYAPPROVALCONFLICT",
        challenge_id=_ids("apch"),
        proposal_id=_ids("ordp"),
        order_hash="sha256:" + ("0" * 64),
        method=ApprovalMethod.TELEGRAM,
        scope=ApprovalScope.PAPER,
        outcome=ApprovalEventOutcome.DENIED,
        approved_at=NOW + timedelta(seconds=10),
        approver_identity="telegram:existing",
    )

    with UnitOfWork(factory) as uow:
        uow.approvals_repo.add_event_fields(
            {
                "event_id": conflicting_event.event_id,
                "challenge_id": conflicting_event.challenge_id,
                "proposal_id": conflicting_event.proposal_id,
                "order_hash": conflicting_event.order_hash,
                "method": conflicting_event.method.value,
                "scope": conflicting_event.scope.value,
                "outcome": conflicting_event.outcome.value,
                "approved_at": conflicting_event.approved_at,
                "approver_identity": conflicting_event.approver_identity,
                "schema_version": conflicting_event.schema_version,
                "payload_json": conflicting_event.model_dump(mode="json"),
            }
        )

    with UnitOfWork(factory) as uow:
        with pytest.raises(ApprovalServiceError) as caught:
            _service(uow, now=NOW + timedelta(seconds=30)).decide(
                FIXED_TOKEN,
                approved=True,
                approver_identity="telegram:12345",
                event_id=conflicting_event.event_id,
            )
        assert caught.value.code == "APPROVAL_EVENT_CONFLICT"
        challenge = uow.approvals_repo.get_challenge(_ids("apch"))
        assert challenge is not None
        assert challenge.status == ApprovalChallengeStatus.PENDING.value
        assert len(uow.approvals_repo.list_events_for_proposal(_ids("ordp"))) == 1

    with UnitOfWork(factory) as uow:
        challenge = uow.approvals_repo.get_challenge(_ids("apch"))
        assert challenge is not None
        assert challenge.status == ApprovalChallengeStatus.PENDING.value
        events = uow.approvals_repo.list_events_for_proposal(_ids("ordp"))
        assert [event.event_id for event in events] == [conflicting_event.event_id]


@pytest.mark.unit
def test_concurrent_decision_has_exactly_one_winner(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "concurrent.db")
    _issue(factory)

    def decide(suffix: str) -> str:
        try:
            with UnitOfWork(factory) as uow:
                _service(
                    uow,
                    now=NOW + timedelta(seconds=30),
                    event_suffix=suffix,
                ).decide(
                    FIXED_TOKEN,
                    approved=True,
                    approver_identity="telegram:12345",
                )
            return "won"
        except ApprovalServiceError as exc:
            assert exc.code == "CHALLENGE_ALREADY_USED"
            return "lost"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, ("0002", "0003")))

    assert sorted(outcomes) == ["lost", "won"]
    with UnitOfWork(factory) as uow:
        events = uow.approvals_repo.list_events_for_proposal(_ids("ordp"))
        assert len(events) == 1
        challenge = uow.approvals_repo.get_challenge(_ids("apch"))
        assert challenge is not None
        assert challenge.status == ApprovalChallengeStatus.APPROVED.value


@pytest.mark.unit
def test_invalid_raw_token_is_not_echoed_in_error_or_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    factory = _session_factory(tmp_path / "invalid-token.db")
    invalid = "attacker-controlled-token"
    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).decide(
            invalid,
            approved=True,
            approver_identity="telegram:12345",
        )
    assert caught.value.code == "INVALID_APPROVAL_TOKEN"
    assert invalid not in str(caught.value)
    assert invalid not in caplog.text
