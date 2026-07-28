"""Transactional proposal and one-time approval challenge service (P05-T0).

The service freezes an approved risk decision and its canonical order in the
same transaction as a short-lived challenge. Only a domain-separated nonce
hash reaches persistence. Each decision conditionally transitions a PENDING
challenge, so concurrent or repeated callbacks can create at most one event.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from ainvest.approval.order_hash import (
    attach_order_hash,
    parse_order_proposal,
    verify_order_hash,
)
from ainvest.approval.tokens import (
    OpaqueApprovalToken,
    generate_approval_token,
    hash_approval_token,
)
from ainvest.db.errors import ConcurrentModificationError
from ainvest.db.repositories import (
    ApprovalRepository,
    ProposalRepository,
    RiskDecisionRepository,
)
from ainvest.db.uow import UnitOfWork
from ainvest.schemas.approval import (
    ApprovalChallenge,
    ApprovalChallengeStatus,
    ApprovalEvent,
    ApprovalEventOutcome,
    ApprovalMethod,
    ApprovalScope,
)
from ainvest.schemas.common import ensure_utc
from ainvest.schemas.orders import CandidateOrder, OrderProposal
from ainvest.schemas.portfolio import AccountScope
from ainvest.schemas.risk import RiskDecision, RiskOutcome

MIN_APPROVAL_TTL: Final[timedelta] = timedelta(seconds=60)
MAX_APPROVAL_TTL: Final[timedelta] = timedelta(seconds=120)
DEFAULT_APPROVAL_TTL: Final[timedelta] = MAX_APPROVAL_TTL

type Clock = Callable[[], datetime]
type IdFactory = Callable[[str], str]
type TokenFactory = Callable[[], OpaqueApprovalToken]


class ApprovalServiceError(RuntimeError):
    """Fail-closed approval error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class IssuedApprovalChallenge:
    """Frozen proposal/challenge plus the one-time raw token returned to transport."""

    proposal: OrderProposal
    challenge: ApprovalChallenge
    token: OpaqueApprovalToken


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_stable_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


class ApprovalService:
    """Create and decide approval challenges inside an active UnitOfWork."""

    def __init__(
        self,
        proposals: ProposalRepository,
        risk_decisions: RiskDecisionRepository,
        approvals: ApprovalRepository,
        *,
        clock: Clock = _utc_now,
        id_factory: IdFactory = _new_stable_id,
        token_factory: TokenFactory = generate_approval_token,
    ) -> None:
        self._proposals = proposals
        self._risk_decisions = risk_decisions
        self._approvals = approvals
        self._clock = clock
        self._id_factory = id_factory
        self._token_factory = token_factory

    @classmethod
    def from_uow(
        cls,
        uow: UnitOfWork,
        *,
        clock: Clock = _utc_now,
        id_factory: IdFactory = _new_stable_id,
        token_factory: TokenFactory = generate_approval_token,
    ) -> ApprovalService:
        """Build a service that shares the caller's atomic transaction."""
        return cls(
            uow.proposals_repo,
            uow.risk_decisions_repo,
            uow.approvals_repo,
            clock=clock,
            id_factory=id_factory,
            token_factory=token_factory,
        )

    def create_proposal_and_challenge(
        self,
        candidate: CandidateOrder,
        risk_decision: RiskDecision,
        *,
        method: ApprovalMethod,
        scope: ApprovalScope,
        ttl: timedelta = DEFAULT_APPROVAL_TTL,
        proposal_id: str | None = None,
        challenge_id: str | None = None,
    ) -> IssuedApprovalChallenge:
        """Freeze a risk-approved candidate and issue one short-lived token."""
        now = ensure_utc(self._clock())
        try:
            method = ApprovalMethod(method)
            scope = ApprovalScope(scope)
        except ValueError as exc:
            raise ApprovalServiceError(
                "FORBIDDEN_APPROVAL_SCOPE",
                "unsupported approval method or scope",
            ) from exc
        if (method, scope) not in {
            (ApprovalMethod.TELEGRAM, ApprovalScope.PAPER),
            (ApprovalMethod.WEBAUTHN, ApprovalScope.LIVE),
        }:
            raise ApprovalServiceError(
                "FORBIDDEN_APPROVAL_SCOPE",
                "approval must be telegram+paper or webauthn+live",
            )
        _require_ttl(ttl)
        _require_risk_approval(candidate, risk_decision)
        _require_scope_matches_account(scope, candidate.account_scope)

        resolved_proposal_id = proposal_id or self._id_factory("ordp")
        if (
            risk_decision.proposal_id is not None
            and risk_decision.proposal_id != resolved_proposal_id
        ):
            raise ApprovalServiceError(
                "RISK_PROPOSAL_MISMATCH",
                "risk decision is bound to a different proposal",
            )

        challenge_expiry = now + ttl
        if candidate.expires_at < challenge_expiry:
            raise ApprovalServiceError(
                "PROPOSAL_EXPIRES_TOO_SOON",
                "proposal expires before the approval challenge",
            )

        proposal = _build_proposal(
            candidate,
            risk_decision_id=risk_decision.risk_decision_id,
            proposal_id=resolved_proposal_id,
        )
        token = self._token_factory()
        nonce_hash = hash_approval_token(token)
        challenge = ApprovalChallenge(
            challenge_id=challenge_id or self._id_factory("apch"),
            proposal_id=proposal.proposal_id,
            order_hash=proposal.order_hash,
            method=method,
            scope=scope,
            nonce_hash=nonce_hash,
            created_at=now,
            expires_at=challenge_expiry,
            status=ApprovalChallengeStatus.PENDING,
        )

        self._freeze_risk_decision(risk_decision)
        self._proposals.add_fields(_proposal_fields(proposal))
        self._approvals.add_challenge_fields(_challenge_fields(challenge))
        return IssuedApprovalChallenge(proposal=proposal, challenge=challenge, token=token)

    def decide(
        self,
        raw_token: OpaqueApprovalToken | str,
        *,
        approved: bool,
        approver_identity: str,
        event_id: str | None = None,
    ) -> ApprovalEvent:
        """Atomically approve/reject one PENDING token, or expire it.

        The caller must commit the surrounding UnitOfWork. A concurrent loser
        receives ``CHALLENGE_ALREADY_USED`` and creates no approval event.
        """
        if not isinstance(approved, bool):
            raise ApprovalServiceError(
                "INVALID_APPROVAL_DECISION",
                "approval decision must be an explicit boolean",
            )
        now = ensure_utc(self._clock())
        try:
            nonce_hash = hash_approval_token(raw_token)
        except ValueError as exc:
            raise ApprovalServiceError("INVALID_APPROVAL_TOKEN", "invalid approval token") from exc

        stored = self._approvals.get_challenge_by_token_hash(nonce_hash)
        if stored is None or not hmac.compare_digest(stored.token_hash, nonce_hash):
            raise ApprovalServiceError("CHALLENGE_NOT_FOUND", "approval challenge not found")
        if stored.status != ApprovalChallengeStatus.PENDING.value:
            raise ApprovalServiceError(
                "CHALLENGE_ALREADY_USED",
                "approval challenge is no longer pending",
            )

        challenge = _load_challenge(stored)
        proposal = self._load_bound_proposal(challenge)

        if now >= challenge.expires_at or now >= proposal.expires_at:
            new_status = ApprovalChallengeStatus.EXPIRED
            outcome = ApprovalEventOutcome.EXPIRED
        elif approved:
            new_status = ApprovalChallengeStatus.APPROVED
            outcome = ApprovalEventOutcome.APPROVED
        else:
            new_status = ApprovalChallengeStatus.REJECTED
            outcome = ApprovalEventOutcome.DENIED

        transitioned = challenge.model_copy(update={"status": new_status})
        try:
            self._approvals.consume_challenge_once(
                challenge.challenge_id,
                expected_version=stored.version,
                expected_status=ApprovalChallengeStatus.PENDING.value,
                new_status=new_status.value,
                extra_values={"payload_json": transitioned.model_dump(mode="json")},
            )
        except ConcurrentModificationError as exc:
            raise ApprovalServiceError(
                "CHALLENGE_ALREADY_USED",
                "approval challenge lost a concurrent decision race",
            ) from exc

        event = ApprovalEvent(
            event_id=event_id or self._id_factory("apev"),
            challenge_id=challenge.challenge_id,
            proposal_id=proposal.proposal_id,
            order_hash=proposal.order_hash,
            method=challenge.method,
            scope=challenge.scope,
            outcome=outcome,
            approved_at=now,
            approver_identity=approver_identity,
        )
        self._approvals.add_event_fields(_event_fields(event))
        return event

    def _freeze_risk_decision(self, decision: RiskDecision) -> None:
        payload = decision.model_dump(mode="json")
        existing = self._risk_decisions.get(decision.risk_decision_id)
        if existing is not None:
            if (
                existing.payload_json != payload
                or existing.candidate_id != decision.candidate_id
                or existing.proposal_id != decision.proposal_id
                or existing.outcome != decision.outcome.value
                or existing.decided_at != decision.decided_at
                or existing.rule_set_version != decision.rule_set_version
                or existing.reason_code != decision.reason_code
            ):
                raise ApprovalServiceError(
                    "RISK_DECISION_CHANGED",
                    "stored risk decision does not match the proposal input",
                )
            return
        self._risk_decisions.add_fields(_risk_decision_fields(decision))

    def _load_bound_proposal(self, challenge: ApprovalChallenge) -> OrderProposal:
        stored = self._proposals.get_by_proposal_id(challenge.proposal_id)
        if stored is None:
            raise ApprovalServiceError("PROPOSAL_NOT_FOUND", "proposal not found")
        try:
            proposal = parse_order_proposal(dict(stored.payload_json))
        except (TypeError, ValueError, KeyError) as exc:
            raise ApprovalServiceError(
                "PROPOSAL_INTEGRITY_FAILED",
                "stored proposal failed canonical validation",
            ) from exc
        if (
            proposal.proposal_id != stored.proposal_id
            or proposal.signal_id != stored.signal_id
            or proposal.candidate_id != stored.candidate_id
            or proposal.risk_decision_id != stored.risk_decision_id
            or proposal.account_scope.value != stored.account_scope
            or proposal.instrument_id != stored.instrument_id
            or proposal.symbol != stored.symbol
            or proposal.side.value != stored.side
            or proposal.quantity != stored.quantity
            or proposal.limit_price != stored.limit_price
            or proposal.currency != stored.currency
            or proposal.strategy != stored.strategy
            or proposal.strategy_version != stored.strategy_version
            or proposal.order_hash != stored.order_hash
            or proposal.order_hash != challenge.order_hash
            or proposal.created_at != stored.proposal_created_at
            or proposal.expires_at != stored.expires_at
        ):
            raise ApprovalServiceError(
                "PROPOSAL_INTEGRITY_FAILED",
                "challenge and stored proposal binding do not match",
            )
        verify_order_hash(proposal)
        return proposal


def _require_ttl(ttl: timedelta) -> None:
    if not MIN_APPROVAL_TTL <= ttl <= MAX_APPROVAL_TTL:
        raise ApprovalServiceError(
            "INVALID_APPROVAL_TTL",
            "approval TTL must be between 60 and 120 seconds",
        )


def _require_risk_approval(candidate: CandidateOrder, decision: RiskDecision) -> None:
    if decision.outcome is not RiskOutcome.APPROVED:
        raise ApprovalServiceError(
            "RISK_NOT_APPROVED",
            "only an APPROVED risk decision can create a proposal",
        )
    if decision.candidate_id != candidate.candidate_id:
        raise ApprovalServiceError(
            "RISK_CANDIDATE_MISMATCH",
            "risk decision is bound to a different candidate",
        )


def _require_scope_matches_account(scope: ApprovalScope, account: AccountScope) -> None:
    expected = {
        ApprovalScope.PAPER: AccountScope.PAPER,
        ApprovalScope.LIVE: AccountScope.AGENTIC,
    }[scope]
    if account is not expected:
        raise ApprovalServiceError(
            "APPROVAL_ACCOUNT_SCOPE_MISMATCH",
            "approval scope does not match the proposal account",
        )


def _build_proposal(
    candidate: CandidateOrder,
    *,
    proposal_id: str,
    risk_decision_id: str,
) -> OrderProposal:
    payload = candidate.model_dump(mode="python")
    payload.pop("reason_codes", None)
    payload.update(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
    )
    return parse_order_proposal(attach_order_hash(payload))


def _risk_decision_fields(decision: RiskDecision) -> dict[str, Any]:
    return {
        "risk_decision_id": decision.risk_decision_id,
        "candidate_id": decision.candidate_id,
        "proposal_id": decision.proposal_id,
        "outcome": decision.outcome.value,
        "decided_at": decision.decided_at,
        "rule_set_version": decision.rule_set_version,
        "reason_code": decision.reason_code,
        "schema_version": decision.schema_version,
        "payload_json": decision.model_dump(mode="json"),
    }


def _proposal_fields(proposal: OrderProposal) -> dict[str, Any]:
    return {
        "proposal_id": proposal.proposal_id,
        "signal_id": proposal.signal_id,
        "candidate_id": proposal.candidate_id,
        "risk_decision_id": proposal.risk_decision_id,
        "account_scope": proposal.account_scope.value,
        "instrument_id": proposal.instrument_id,
        "symbol": proposal.symbol,
        "side": proposal.side.value,
        "quantity": proposal.quantity,
        "limit_price": proposal.limit_price,
        "currency": proposal.currency,
        "strategy": proposal.strategy,
        "strategy_version": proposal.strategy_version,
        "order_hash": proposal.order_hash,
        "status": "PENDING_APPROVAL",
        "proposal_created_at": proposal.created_at,
        "expires_at": proposal.expires_at,
        "schema_version": proposal.schema_version,
        "payload_json": proposal.model_dump(mode="json"),
    }


def _challenge_fields(challenge: ApprovalChallenge) -> dict[str, Any]:
    return {
        "challenge_id": challenge.challenge_id,
        "proposal_id": challenge.proposal_id,
        "order_hash": challenge.order_hash,
        "method": challenge.method.value,
        "scope": challenge.scope.value,
        "token_hash": challenge.nonce_hash,
        "status": challenge.status.value,
        "challenge_created_at": challenge.created_at,
        "expires_at": challenge.expires_at,
        "schema_version": challenge.schema_version,
        "payload_json": challenge.model_dump(mode="json"),
    }


def _event_fields(event: ApprovalEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "challenge_id": event.challenge_id,
        "proposal_id": event.proposal_id,
        "order_hash": event.order_hash,
        "method": event.method.value,
        "scope": event.scope.value,
        "outcome": event.outcome.value,
        "approved_at": event.approved_at,
        "approver_identity": event.approver_identity,
        "schema_version": event.schema_version,
        "payload_json": event.model_dump(mode="json"),
    }


def _load_challenge(stored: Any) -> ApprovalChallenge:
    try:
        challenge = ApprovalChallenge.model_validate(dict(stored.payload_json))
    except (TypeError, ValueError) as exc:
        raise ApprovalServiceError(
            "CHALLENGE_INTEGRITY_FAILED",
            "stored challenge failed validation",
        ) from exc
    if (
        challenge.challenge_id != stored.challenge_id
        or challenge.proposal_id != stored.proposal_id
        or challenge.order_hash != stored.order_hash
        or challenge.nonce_hash != stored.token_hash
        or challenge.method.value != stored.method
        or challenge.scope.value != stored.scope
        or challenge.status.value != stored.status
    ):
        raise ApprovalServiceError(
            "CHALLENGE_INTEGRITY_FAILED",
            "stored challenge fields do not match its frozen payload",
        )
    return challenge


__all__ = [
    "DEFAULT_APPROVAL_TTL",
    "MAX_APPROVAL_TTL",
    "MIN_APPROVAL_TTL",
    "ApprovalService",
    "ApprovalServiceError",
    "IssuedApprovalChallenge",
]
