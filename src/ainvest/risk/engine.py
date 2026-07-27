"""Risk decision aggregator (P03-T8).

Composable pure-Python rules → explainable :class:`RiskDecision`. Aggregation
is order-independent: any hard reject wins, else any review, else approved.
Missing inputs, unknown rules, and rule exceptions fail closed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Annotated

from pydantic import StringConstraints

from ainvest.data.calendar_port import MarketCalendar
from ainvest.risk.models import RiskContext, RiskRuleConfig, RuleResult
from ainvest.risk.rules import DEFAULT_RULE_CODES, RiskRule
from ainvest.risk.rules.eligibility import (
    AllowlistRule,
    AssetClassRule,
    IdentityConsistencyRule,
    SessionRule,
    SideAndProductRule,
)
from ainvest.risk.rules.exposure import build_exposure_rules
from ainvest.risk.rules.market_quality import (
    LimitDeviationRule,
    QuoteFreshnessRule,
    SpreadRule,
    VolatilityRule,
)
from ainvest.schemas.common import SCHEMA_VERSION_V1, DomainModel, SchemaVersion
from ainvest.schemas.risk import (
    RiskDecision,
    RiskOutcome,
    RiskSeverity,
    RiskViolation,
)

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$")]


class RiskEngineOutput(DomainModel):
    """Decision plus digests for audit persistence (P03-T8)."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    decision: RiskDecision
    input_digest: Digest
    config_digest: Digest
    rule_codes: tuple[str, ...]
    rule_results: tuple[RuleResult, ...]


def _sha256_digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_input_digest(context: RiskContext) -> str:
    candidate = context.candidate
    quote = context.quote
    instrument = context.instrument
    portfolio = context.portfolio
    exposure = context.exposure_inputs
    return _sha256_digest(
        {
            "phase": context.phase.value,
            "as_of": context.as_of.isoformat(),
            "candidate_id": candidate.candidate_id,
            "instrument_id": candidate.instrument_id,
            "symbol": candidate.symbol,
            "side": candidate.side.value,
            "quantity": str(candidate.quantity),
            "limit_price": str(candidate.limit_price),
            "quote_last": str(quote.last_price),
            "quote_bid": str(quote.bid) if quote.bid is not None else None,
            "quote_ask": str(quote.ask) if quote.ask is not None else None,
            "quote_observed_at": quote.provenance.observed_at.isoformat(),
            "instrument_tradable": instrument.tradable,
            "vol_bps": (
                str(context.short_term_volatility_bps)
                if context.short_term_volatility_bps is not None
                else None
            ),
            "portfolio": (
                None
                if portfolio is None
                else {
                    "snapshot_id": portfolio.snapshot_id,
                    "cash": str(portfolio.cash),
                    "buying_power": str(portfolio.buying_power),
                    "equity": str(portfolio.equity),
                    "positions": [
                        {
                            "instrument_id": p.instrument.instrument_id,
                            "quantity": str(p.quantity),
                            "market_value": str(p.market_value),
                        }
                        for p in sorted(
                            portfolio.positions,
                            key=lambda p: p.instrument.instrument_id,
                        )
                    ],
                    "open_orders": [
                        {
                            "order_id": o.order_id,
                            "instrument_id": o.instrument.instrument_id,
                            "side": o.side.value,
                            "quantity": str(o.quantity),
                            "limit_price": (
                                str(o.limit_price) if o.limit_price is not None else None
                            ),
                        }
                        for o in sorted(
                            portfolio.open_orders,
                            key=lambda o: (o.order_id, o.instrument.instrument_id),
                        )
                    ],
                }
            ),
            "exposure_inputs": (
                None
                if exposure is None
                else {
                    **exposure.model_dump(mode="json"),
                    "sectors": [
                        s.model_dump(mode="json")
                        for s in sorted(
                            exposure.sectors,
                            key=lambda s: (s.instrument_id, s.sector),
                        )
                    ],
                }
            ),
        }
    )


def compute_config_digest(config: RiskRuleConfig) -> str:
    return _sha256_digest(config.model_dump(mode="json"))


def aggregate_rule_results(
    results: Sequence[RuleResult],
) -> tuple[RiskOutcome, tuple[RiskViolation, ...]]:
    """Aggregate per-rule results. Order of ``results`` must not change outcome."""
    violations: list[RiskViolation] = []
    for result in results:
        if result.decision is RiskOutcome.APPROVED and result.severity is RiskSeverity.INFO:
            continue
        violations.append(
            RiskViolation(
                rule_code=result.rule_code,
                severity=result.severity,
                reason=result.reason,
                evidence=result.evidence,
            )
        )
    hard = any(v.severity is RiskSeverity.HARD for v in violations)
    review = any(v.severity is RiskSeverity.REVIEW for v in violations)
    if hard:
        outcome = RiskOutcome.REJECTED
    elif review:
        outcome = RiskOutcome.NEEDS_REVIEW
    else:
        outcome = RiskOutcome.APPROVED
    # Stable ordering for explainability (does not affect outcome).
    ordered = tuple(sorted(violations, key=lambda v: (v.severity.value, v.rule_code, v.reason)))
    return outcome, ordered


def _decision_reason(
    outcome: RiskOutcome, violations: tuple[RiskViolation, ...]
) -> tuple[str, str]:
    if outcome is RiskOutcome.APPROVED:
        return "ALL_RULES_PASSED", "all hard and review rules passed"
    if outcome is RiskOutcome.REJECTED:
        hard = next(v for v in violations if v.severity is RiskSeverity.HARD)
        return hard.rule_code, hard.reason
    review = next(v for v in violations if v.severity is RiskSeverity.REVIEW)
    return review.rule_code, review.reason


def evaluate_rules(
    context: RiskContext,
    rules: Sequence[RiskRule],
) -> RiskEngineOutput:
    """Run ``rules`` and aggregate. Rule exceptions become HARD rejects."""
    results: list[RuleResult] = []
    for rule in rules:
        try:
            result = rule.evaluate(context)
        except Exception as exc:
            results.append(
                RuleResult(
                    rule_code="RULE_EXCEPTION",
                    severity=RiskSeverity.HARD,
                    decision=RiskOutcome.REJECTED,
                    reason="rule raised an exception (fail closed)",
                    evidence=f"{type(exc).__name__}: {exc}"[:1024],
                )
            )
            continue
        results.append(result)

    outcome, violations = aggregate_rule_results(results)
    reason_code, reason = _decision_reason(outcome, violations)
    decision = RiskDecision(
        risk_decision_id=context.risk_decision_id,
        candidate_id=context.candidate.candidate_id,
        proposal_id=None,
        outcome=outcome,
        decided_at=context.as_of,
        rule_set_version=context.config.rule_set_version,
        violations=violations,
        reason_code=reason_code,
        reason=reason,
    )
    return RiskEngineOutput(
        decision=decision,
        input_digest=compute_input_digest(context),
        config_digest=compute_config_digest(context.config),
        rule_codes=tuple(rule.code for rule in rules),
        rule_results=tuple(results),
    )


def build_default_rules(calendar: MarketCalendar) -> dict[str, RiskRule]:
    """Instantiate the default screening + exposure rule set."""
    rules: dict[str, RiskRule] = {
        rule.code: rule
        for rule in (
            AssetClassRule(),
            AllowlistRule(),
            IdentityConsistencyRule(),
            SideAndProductRule(),
            SessionRule(calendar),
            QuoteFreshnessRule(),
            SpreadRule(),
            VolatilityRule(),
            LimitDeviationRule(),
        )
    }
    rules.update(build_exposure_rules())
    return rules


def evaluate_risk(
    context: RiskContext,
    *,
    calendar: MarketCalendar,
    rule_codes: Sequence[str] | None = DEFAULT_RULE_CODES,
) -> RiskEngineOutput:
    """Evaluate with the standard rule set (unknown codes fail closed)."""
    available = build_default_rules(calendar)
    codes = tuple(DEFAULT_RULE_CODES if rule_codes is None else rule_codes)
    selected: list[RiskRule] = []
    for code in codes:
        rule = available.get(code)
        if rule is None:
            return evaluate_rules(context, [_UnknownRule(code)])
        selected.append(rule)
    return evaluate_rules(context, selected)


class _UnknownRule:
    code = "UNKNOWN_RULE"

    def __init__(self, missing: str) -> None:
        self._missing = missing

    def evaluate(self, context: RiskContext) -> RuleResult:
        del context
        return RuleResult(
            rule_code="UNKNOWN_RULE",
            severity=RiskSeverity.HARD,
            decision=RiskOutcome.REJECTED,
            reason="unknown risk rule requested (fail closed)",
            evidence=f"missing_rule={self._missing}",
        )


__all__ = [
    "RiskEngineOutput",
    "aggregate_rule_results",
    "build_default_rules",
    "compute_config_digest",
    "compute_input_digest",
    "evaluate_risk",
    "evaluate_rules",
]
