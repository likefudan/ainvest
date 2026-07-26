"""Risk decision schemas (P02-T3)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints, field_validator, model_validator

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    DomainModel,
    MachineCode,
    SchemaVersion,
    StableId,
    UtcDateTime,
)

RuleCode = MachineCode


class RiskSeverity(StrEnum):
    """Violation severity for explainable risk aggregation."""

    INFO = "INFO"
    REVIEW = "REVIEW"
    HARD = "HARD"


class RiskOutcome(StrEnum):
    """Aggregated risk decision outcomes."""

    APPROVED = "APPROVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"


class RiskViolation(DomainModel):
    """One rule finding with a stable machine-readable code."""

    rule_code: RuleCode
    severity: RiskSeverity
    reason: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    evidence: Annotated[str, StringConstraints(min_length=1, max_length=1024)] | None = None


class RiskDecision(DomainModel):
    """Immutable risk outcome bound to a candidate or proposal.

    Every decision carries both a machine-readable ``reason_code`` and a
    human-readable ``reason`` (design.md §5.4).
    """

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    risk_decision_id: StableId
    candidate_id: StableId | None = None
    proposal_id: StableId | None = None
    outcome: RiskOutcome
    decided_at: UtcDateTime
    rule_set_version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    violations: tuple[RiskViolation, ...] = ()
    reason_code: RuleCode
    reason: Annotated[str, StringConstraints(min_length=1, max_length=512)]

    @field_validator("violations", mode="before")
    @classmethod
    def _coerce_violations(cls, value: object) -> object:
        if value is None:
            return ()
        return value

    @model_validator(mode="after")
    def _decision_consistency(self) -> RiskDecision:
        if self.candidate_id is None and self.proposal_id is None:
            raise ValueError("risk decision requires candidate_id or proposal_id")
        hard = any(item.severity is RiskSeverity.HARD for item in self.violations)
        review = any(item.severity is RiskSeverity.REVIEW for item in self.violations)
        if self.outcome is RiskOutcome.REJECTED and not hard:
            raise ValueError("REJECTED decisions require at least one HARD violation")
        if hard and self.outcome is not RiskOutcome.REJECTED:
            raise ValueError("HARD violations require REJECTED outcome")
        if self.outcome is RiskOutcome.NEEDS_REVIEW and not review:
            raise ValueError("NEEDS_REVIEW decisions require at least one REVIEW violation")
        if review and not hard and self.outcome is RiskOutcome.APPROVED:
            raise ValueError("REVIEW violations cannot yield APPROVED")
        if self.outcome is RiskOutcome.APPROVED and (hard or review):
            raise ValueError("APPROVED cannot include HARD or REVIEW violations")
        return self


__all__ = [
    "RiskDecision",
    "RiskOutcome",
    "RiskSeverity",
    "RiskViolation",
    "RuleCode",
]
