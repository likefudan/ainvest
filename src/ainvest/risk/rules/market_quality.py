"""Quote freshness, spread, volatility, and limit deviation rules (P03-T11)."""

from __future__ import annotations

from decimal import Decimal

from ainvest.risk.models import (
    BPS_DENOM,
    ZERO,
    EvaluationPhase,
    PhaseMarketQualityLimits,
    RiskContext,
    RuleResult,
)
from ainvest.risk.rules import register_rule
from ainvest.schemas.common import QualityFlag, canonicalize_decimal, ensure_utc
from ainvest.schemas.risk import RiskOutcome, RiskSeverity


def _hard(code: str, reason: str, evidence: str | None = None) -> RuleResult:
    return RuleResult(
        rule_code=code,
        severity=RiskSeverity.HARD,
        decision=RiskOutcome.REJECTED,
        reason=reason,
        evidence=evidence,
    )


def _ok(code: str, reason: str, evidence: str | None = None) -> RuleResult:
    return RuleResult(
        rule_code=code,
        severity=RiskSeverity.INFO,
        decision=RiskOutcome.APPROVED,
        reason=reason,
        evidence=evidence,
    )


def _phase_limits(context: RiskContext) -> PhaseMarketQualityLimits:
    mq = context.config.market_quality
    if context.phase is EvaluationPhase.PROPOSAL:
        return mq.proposal
    if context.phase is EvaluationPhase.PRETRADE:
        return mq.pretrade
    raise ValueError(f"unknown evaluation phase: {context.phase}")


def _mid(bid: Decimal, ask: Decimal) -> Decimal:
    return canonicalize_decimal((bid + ask) / Decimal("2"))


class QuoteFreshnessRule:
    """Reject stale/delayed quotes, missing bid/ask, skew, and bad prices."""

    code = "MARKET_QUALITY_QUOTE"

    def evaluate(self, context: RiskContext) -> RuleResult:
        quote = context.quote
        limits = _phase_limits(context)
        max_skew = context.config.market_quality.max_clock_skew_seconds
        as_of = ensure_utc(context.as_of)
        observed = ensure_utc(quote.provenance.observed_at)
        received = ensure_utc(quote.provenance.received_at)

        if received < observed:
            return _hard(self.code, "quote received_at precedes observed_at")

        skew = abs((received - as_of).total_seconds())
        if skew > max_skew:
            return _hard(
                self.code,
                "quote clock skew exceeds maximum",
                evidence=f"skew_seconds={skew}; max={max_skew}",
            )

        age = (as_of - observed).total_seconds()
        if age < 0:
            return _hard(
                self.code,
                "quote observed_at is in the future relative to as_of (clock skew)",
                evidence=f"age_seconds={age}",
            )
        if age > limits.max_quote_age_seconds:
            return _hard(
                self.code,
                "quote age exceeds maximum for evaluation phase",
                evidence=(
                    f"age_seconds={age}; max={limits.max_quote_age_seconds}; "
                    f"phase={context.phase.value}"
                ),
            )

        if quote.provenance.is_delayed or QualityFlag.DELAYED in quote.provenance.quality_flags:
            return _hard(self.code, "delayed quotes are rejected")
        if QualityFlag.STALE in quote.provenance.quality_flags:
            return _hard(self.code, "stale quality flag is rejected")

        if quote.bid is None or quote.ask is None:
            return _hard(self.code, "bid and ask are required")
        bid = canonicalize_decimal(quote.bid)
        ask = canonicalize_decimal(quote.ask)
        last = canonicalize_decimal(quote.last_price)
        if bid <= ZERO or ask <= ZERO or last <= ZERO:
            return _hard(self.code, "zero or negative prices are rejected")
        if bid > ask:
            return _hard(
                self.code,
                "crossed market (bid > ask) is rejected",
                evidence=f"bid={bid}; ask={ask}",
            )
        return _ok(
            self.code,
            "quote freshness and completeness checks passed",
            evidence=f"age_seconds={age}; phase={context.phase.value}",
        )


class SpreadRule:
    """Maximum bid/ask spread in basis points."""

    code = "MARKET_QUALITY_SPREAD"

    def evaluate(self, context: RiskContext) -> RuleResult:
        quote = context.quote
        if quote.bid is None or quote.ask is None:
            return _hard(self.code, "bid and ask are required for spread check")
        bid = canonicalize_decimal(quote.bid)
        ask = canonicalize_decimal(quote.ask)
        if bid <= ZERO or ask <= ZERO or bid > ask:
            return _hard(self.code, "invalid bid/ask for spread check")
        mid = _mid(bid, ask)
        if mid <= ZERO:
            return _hard(self.code, "mid price must be positive")
        spread_bps = canonicalize_decimal((ask - bid) / mid * BPS_DENOM)
        limit = canonicalize_decimal(_phase_limits(context).max_spread_bps)
        if spread_bps > limit:
            return _hard(
                self.code,
                "spread exceeds maximum for evaluation phase",
                evidence=(f"spread_bps={spread_bps}; max={limit}; phase={context.phase.value}"),
            )
        return _ok(
            self.code,
            "spread within limit",
            evidence=f"spread_bps={spread_bps}; max={limit}",
        )


class VolatilityRule:
    """Abnormal short-term volatility vs phase threshold (fail closed if missing)."""

    code = "MARKET_QUALITY_VOLATILITY"

    def evaluate(self, context: RiskContext) -> RuleResult:
        limit = canonicalize_decimal(_phase_limits(context).max_short_term_volatility_bps)
        measured = context.short_term_volatility_bps
        if measured is None:
            return _hard(
                self.code,
                "short-term volatility input is required",
                evidence=f"phase={context.phase.value}",
            )
        value = canonicalize_decimal(measured)
        if value > limit:
            return _hard(
                self.code,
                "short-term volatility exceeds maximum for evaluation phase",
                evidence=(f"volatility_bps={value}; max={limit}; phase={context.phase.value}"),
            )
        return _ok(
            self.code,
            "short-term volatility within limit",
            evidence=f"volatility_bps={value}; max={limit}",
        )


class LimitDeviationRule:
    """Limit price vs quote reference (mid) deviation in bps."""

    code = "MARKET_QUALITY_LIMIT_DEVIATION"

    def evaluate(self, context: RiskContext) -> RuleResult:
        quote = context.quote
        if quote.bid is None or quote.ask is None:
            return _hard(self.code, "bid and ask are required for limit deviation")
        bid = canonicalize_decimal(quote.bid)
        ask = canonicalize_decimal(quote.ask)
        if bid <= ZERO or ask <= ZERO or bid > ask:
            return _hard(self.code, "invalid bid/ask for limit deviation")
        mid = _mid(bid, ask)
        limit_price = canonicalize_decimal(context.candidate.limit_price)
        if mid <= ZERO or limit_price <= ZERO:
            return _hard(self.code, "reference and limit prices must be positive")
        deviation_bps = canonicalize_decimal(abs(limit_price - mid) / mid * BPS_DENOM)
        max_dev = canonicalize_decimal(_phase_limits(context).max_limit_deviation_bps)
        if deviation_bps > max_dev:
            return _hard(
                self.code,
                "limit price deviation from reference exceeds maximum",
                evidence=(
                    f"deviation_bps={deviation_bps}; max={max_dev}; phase={context.phase.value}"
                ),
            )
        return _ok(
            self.code,
            "limit deviation within limit",
            evidence=f"deviation_bps={deviation_bps}; max={max_dev}",
        )


def register_market_quality_rules() -> None:
    register_rule(QuoteFreshnessRule.code, QuoteFreshnessRule)
    register_rule(SpreadRule.code, SpreadRule)
    register_rule(VolatilityRule.code, VolatilityRule)
    register_rule(LimitDeviationRule.code, LimitDeviationRule)


__all__ = [
    "LimitDeviationRule",
    "QuoteFreshnessRule",
    "SpreadRule",
    "VolatilityRule",
    "register_market_quality_rules",
]
