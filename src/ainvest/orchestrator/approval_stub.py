"""Explicit approval stub for paper orchestration (P03-T16).

Never auto-approves. Callers must invoke ``consume_challenge`` (tests/CLI
``--inject-approval``). TTL expiry yields ``EXPIRED`` without a broker write.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ainvest.orchestrator.types import (
    DEFAULT_APPROVAL_TTL,
    DEFAULT_CHALLENGE_NONCE_HASH,
    FIXED_APPROVAL_EVENT_ID,
    FIXED_CHALLENGE_ID,
)
from ainvest.schemas.approval import (
    ApprovalChallenge,
    ApprovalChallengeStatus,
    ApprovalEvent,
    ApprovalEventOutcome,
    ApprovalMethod,
    ApprovalScope,
)
from ainvest.schemas.common import ensure_utc
from ainvest.schemas.orders import OrderProposal


@dataclass
class ApprovalStubStore:
    """In-memory one-time challenges keyed by challenge_id."""

    challenges: dict[str, ApprovalChallenge] = field(default_factory=dict)
    events: dict[str, ApprovalEvent] = field(default_factory=dict)


def create_challenge(
    proposal: OrderProposal,
    *,
    as_of: datetime,
    store: ApprovalStubStore,
    challenge_id: str = FIXED_CHALLENGE_ID,
    ttl: timedelta = DEFAULT_APPROVAL_TTL,
    nonce_hash: str = DEFAULT_CHALLENGE_NONCE_HASH,
) -> ApprovalChallenge:
    """Create a PENDING telegram/paper challenge bound to ``proposal``."""
    clock = ensure_utc(as_of)
    if challenge_id in store.challenges:
        raise ValueError(f"challenge_id already exists: {challenge_id}")
    challenge = ApprovalChallenge(
        challenge_id=challenge_id,
        proposal_id=proposal.proposal_id,
        order_hash=proposal.order_hash,
        method=ApprovalMethod.TELEGRAM,
        scope=ApprovalScope.PAPER,
        nonce_hash=nonce_hash,
        created_at=clock,
        expires_at=clock + ttl,
        status=ApprovalChallengeStatus.PENDING,
    )
    store.challenges[challenge_id] = challenge
    return challenge


def consume_challenge(
    challenge_id: str,
    *,
    as_of: datetime,
    store: ApprovalStubStore,
    approved: bool,
    event_id: str = FIXED_APPROVAL_EVENT_ID,
    approver_identity: str = "stub_approver_d4a",
) -> ApprovalEvent:
    """Consume a one-time challenge.

    ``approved`` is required and must be passed explicitly — this stub never
    defaults to approval. Expired challenges never become APPROVED.
    """
    clock = ensure_utc(as_of)
    challenge = store.challenges.get(challenge_id)
    if challenge is None:
        raise KeyError(f"unknown challenge_id: {challenge_id}")
    if challenge.status is not ApprovalChallengeStatus.PENDING:
        raise ValueError(f"challenge is not PENDING: {challenge.status.value}")
    if event_id in store.events:
        raise ValueError(f"approval event_id already exists: {event_id}")

    if clock >= challenge.expires_at:
        expired = challenge.model_copy(update={"status": ApprovalChallengeStatus.EXPIRED})
        store.challenges[challenge_id] = expired
        event = ApprovalEvent(
            event_id=event_id,
            challenge_id=challenge_id,
            proposal_id=challenge.proposal_id,
            order_hash=challenge.order_hash,
            method=challenge.method,
            scope=challenge.scope,
            outcome=ApprovalEventOutcome.EXPIRED,
            approved_at=clock,
            approver_identity=approver_identity,
        )
        store.events[event_id] = event
        return event

    status = ApprovalChallengeStatus.CONSUMED
    outcome = ApprovalEventOutcome.APPROVED if approved else ApprovalEventOutcome.DENIED
    consumed = challenge.model_copy(update={"status": status})
    store.challenges[challenge_id] = consumed
    event = ApprovalEvent(
        event_id=event_id,
        challenge_id=challenge_id,
        proposal_id=challenge.proposal_id,
        order_hash=challenge.order_hash,
        method=challenge.method,
        scope=challenge.scope,
        outcome=outcome,
        approved_at=clock,
        approver_identity=approver_identity,
    )
    store.events[event_id] = event
    return event


def challenge_fingerprint(challenge: ApprovalChallenge) -> str:
    """Stable digest over challenge identity fields (for step digests)."""
    payload = (
        f"{challenge.challenge_id}|{challenge.proposal_id}|{challenge.order_hash}|"
        f"{challenge.expires_at.isoformat()}|{challenge.status.value}"
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "ApprovalStubStore",
    "challenge_fingerprint",
    "consume_challenge",
    "create_challenge",
]
