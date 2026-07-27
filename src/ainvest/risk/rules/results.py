"""Shared RuleResult constructors for hard reject and approve outcomes."""

from __future__ import annotations

from ainvest.risk.models import RuleResult
from ainvest.schemas.risk import RiskOutcome, RiskSeverity


def hard_reject(code: str, reason: str, evidence: str | None = None) -> RuleResult:
    """HARD severity reject used by all fail-closed risk rules."""
    return RuleResult(
        rule_code=code,
        severity=RiskSeverity.HARD,
        decision=RiskOutcome.REJECTED,
        reason=reason,
        evidence=evidence,
    )


def approve(code: str, reason: str, evidence: str | None = None) -> RuleResult:
    """INFO severity pass used when a rule finds no violation."""
    return RuleResult(
        rule_code=code,
        severity=RiskSeverity.INFO,
        decision=RiskOutcome.APPROVED,
        reason=reason,
        evidence=evidence,
    )


__all__ = ["approve", "hard_reject"]
