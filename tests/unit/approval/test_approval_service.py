"""P05-T0 proposal freezing and one-time approval service tests."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.service import ApprovalService, ApprovalServiceError
from ainvest.approval.tokens import OpaqueApprovalToken, hash_approval_token
from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.uow import UnitOfWork
from ainvest.schemas.approval import (
    ApprovalChallengeStatus,
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


def _risk(candidate: CandidateOrder, *, outcome: str = "APPROVED") -> RiskDecision:
    payload = risk_decision_example()
    payload["candidate_id"] = candidate.candidate_id
    payload["outcome"] = outcome
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
    return RiskDecision.model_validate(payload)


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
    risk: RiskDecision | None = None,
) -> None:
    selected_candidate = candidate or _candidate()
    selected_risk = risk or _risk(selected_candidate)
    with UnitOfWork(factory) as uow:
        _service(uow).create_proposal_and_challenge(
            selected_candidate,
            selected_risk,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
        )


@pytest.mark.unit
def test_creation_freezes_proposal_risk_and_only_token_hash(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "approval.db")
    candidate = _candidate()
    risk = _risk(candidate)

    with UnitOfWork(factory) as uow:
        issued = _service(uow).create_proposal_and_challenge(
            candidate,
            risk,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
        )
        assert issued.challenge.expires_at - issued.challenge.created_at == timedelta(seconds=120)
        assert issued.proposal.risk_decision_id == risk.risk_decision_id
        assert issued.challenge.order_hash == issued.proposal.order_hash
        with pytest.raises(ValidationError):
            issued.proposal.quantity = Decimal("3")

    with UnitOfWork(factory) as uow:
        proposal = uow.proposals_repo.get_by_proposal_id(_ids("ordp"))
        challenge = uow.approvals_repo.get_challenge(_ids("apch"))
        stored_risk = uow.risk_decisions_repo.get(risk.risk_decision_id)
        assert proposal is not None and challenge is not None and stored_risk is not None
        assert proposal.payload_json == issued.proposal.model_dump(mode="json")
        assert stored_risk.payload_json == risk.model_dump(mode="json")
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
    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            candidate,
            _risk(candidate),
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
            ttl=timedelta(seconds=seconds),
        )
    assert caught.value.code == "INVALID_APPROVAL_TTL"


@pytest.mark.unit
def test_proposal_must_outlive_challenge(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "short-proposal.db")
    candidate = _candidate(expires_in=90)
    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            candidate,
            _risk(candidate),
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
    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            candidate,
            _risk(candidate),
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
    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            candidate,
            _risk(candidate),
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
        )
    assert caught.value.code == "APPROVAL_ACCOUNT_SCOPE_MISMATCH"


@pytest.mark.unit
def test_webauthn_live_is_the_only_supported_live_pair(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "live-pair.db")
    candidate = _candidate(scope="agentic")
    with UnitOfWork(factory) as uow:
        issued = _service(uow).create_proposal_and_challenge(
            candidate,
            _risk(candidate),
            method=ApprovalMethod.WEBAUTHN,
            scope=ApprovalScope.LIVE,
        )
        assert issued.challenge.method is ApprovalMethod.WEBAUTHN
        assert issued.challenge.scope is ApprovalScope.LIVE


@pytest.mark.unit
def test_non_approved_or_mismatched_risk_cannot_create_proposal(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "risk.db")
    candidate = _candidate()
    rejected = _risk(candidate, outcome="REJECTED")
    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            candidate,
            rejected,
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
        )
    assert caught.value.code == "RISK_NOT_APPROVED"

    other = _candidate().model_copy(update={"candidate_id": "cand_01HZYOTHER00001"})
    with UnitOfWork(factory) as uow, pytest.raises(ApprovalServiceError) as caught:
        _service(uow).create_proposal_and_challenge(
            other,
            _risk(candidate),
            method=ApprovalMethod.TELEGRAM,
            scope=ApprovalScope.PAPER,
        )
    assert caught.value.code == "RISK_CANDIDATE_MISMATCH"


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
