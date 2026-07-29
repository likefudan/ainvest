"""Approval challenge and event schemas (P02-T3).

Schema validation permits only ``telegram+paper`` and ``webauthn+live``.
Telegram never authorizes live trading.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import StringConstraints, model_validator

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    DomainModel,
    SchemaVersion,
    StableId,
    UtcDateTime,
)
from ainvest.schemas.orders import OrderHashDigest


class ApprovalMethod(StrEnum):
    """Allowed human-approval methods."""

    TELEGRAM = "telegram"
    WEBAUTHN = "webauthn"


class ApprovalScope(StrEnum):
    """Approval authorization scope."""

    PAPER = "paper"
    LIVE = "live"


class ApprovalChallengeStatus(StrEnum):
    """Lifecycle for a one-time approval challenge."""

    PENDING = "PENDING"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ApprovalChallengeStatusV1_1(StrEnum):
    """v1.1 lifecycle with explicit human-decision states."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"
    CANCELLED = "CANCELLED"


class ApprovalEventOutcome(StrEnum):
    """Result of consuming an approval challenge."""

    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    REPLAYED = "REPLAYED"


ApproverIdentity = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128),
]


class ApprovalChallenge(DomainModel):
    """One-time challenge bound to a proposal and order hash."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    challenge_id: StableId
    proposal_id: StableId
    order_hash: OrderHashDigest
    method: ApprovalMethod
    scope: ApprovalScope
    nonce_hash: Annotated[
        str, StringConstraints(pattern=r"^[a-f0-9]{64}$", min_length=64, max_length=64)
    ]
    created_at: UtcDateTime
    expires_at: UtcDateTime
    status: ApprovalChallengeStatus = ApprovalChallengeStatus.PENDING

    @model_validator(mode="after")
    def _method_scope_and_window(self) -> ApprovalChallenge:
        _require_allowed_method_scope(self.method, self.scope)
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be > created_at")
        return self


class ApprovalChallengeV1_1(DomainModel):
    """Backward-compatible v1.1 challenge with explicit decision states."""

    schema_version: Literal["1.1"] = "1.1"
    challenge_id: StableId
    proposal_id: StableId
    order_hash: OrderHashDigest
    method: ApprovalMethod
    scope: ApprovalScope
    nonce_hash: Annotated[
        str, StringConstraints(pattern=r"^[a-f0-9]{64}$", min_length=64, max_length=64)
    ]
    created_at: UtcDateTime
    expires_at: UtcDateTime
    status: ApprovalChallengeStatusV1_1 = ApprovalChallengeStatusV1_1.PENDING

    @model_validator(mode="after")
    def _method_scope_and_window(self) -> ApprovalChallengeV1_1:
        _require_allowed_method_scope(self.method, self.scope)
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be > created_at")
        return self


type ApprovalChallengeDocument = ApprovalChallenge | ApprovalChallengeV1_1


def parse_approval_challenge(data: Mapping[str, Any]) -> ApprovalChallengeDocument:
    """Dispatch one challenge document to its exact implemented minor model."""
    version = data.get("schema_version", SCHEMA_VERSION_V1)
    if version == "1.0":
        return ApprovalChallenge.model_validate(data)
    if version == "1.1":
        return ApprovalChallengeV1_1.model_validate(data)
    raise ValueError(f"unsupported ApprovalChallenge schema_version: {version!r}")


class ApprovalEvent(DomainModel):
    """Immutable approval outcome consumed by execution."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    event_id: StableId
    challenge_id: StableId
    proposal_id: StableId
    order_hash: OrderHashDigest
    method: ApprovalMethod
    scope: ApprovalScope
    outcome: ApprovalEventOutcome
    approved_at: UtcDateTime
    approver_identity: ApproverIdentity

    @model_validator(mode="after")
    def _method_scope(self) -> ApprovalEvent:
        _require_allowed_method_scope(self.method, self.scope)
        return self


def _require_allowed_method_scope(method: ApprovalMethod, scope: ApprovalScope) -> None:
    allowed = {
        (ApprovalMethod.TELEGRAM, ApprovalScope.PAPER),
        (ApprovalMethod.WEBAUTHN, ApprovalScope.LIVE),
    }
    if (method, scope) not in allowed:
        raise ValueError("approval method/scope must be telegram+paper or webauthn+live")


__all__ = [
    "ApprovalChallenge",
    "ApprovalChallengeDocument",
    "ApprovalChallengeStatus",
    "ApprovalChallengeStatusV1_1",
    "ApprovalChallengeV1_1",
    "ApprovalEvent",
    "ApprovalEventOutcome",
    "ApprovalMethod",
    "ApprovalScope",
    "ApproverIdentity",
    "parse_approval_challenge",
]
