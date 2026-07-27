"""Unit tests for market-quality rules (P03-T11)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from risk.risk_fixtures import (
    make_candidate,
    make_context,
    make_fresh_quote,
    make_instrument,
    make_market_quality,
    make_phase_limits,
    make_risk_config,
)

from ainvest.risk.models import EvaluationPhase, RiskContext
from ainvest.risk.rules.market_quality import (
    LimitDeviationRule,
    QuoteFreshnessRule,
    SpreadRule,
    VolatilityRule,
)
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.risk import RiskOutcome


def _ctx(
    *,
    phase: EvaluationPhase = EvaluationPhase.PROPOSAL,
    as_of: datetime | None = None,
    quote: MarketQuote | None = None,
    vol: str | None = "10",
    limit_price: str = "214.50",
) -> RiskContext:
    moment = as_of or datetime(2026, 7, 23, 15, 0, 0, tzinfo=UTC)
    return make_context(
        risk_decision_id="risk_01HZYMQ000000001",
        phase=phase,
        as_of=moment,
        candidate=make_candidate(
            limit_price=limit_price,
            maximum_notional=str(Decimal(limit_price) * Decimal("2")),
        ),
        quote=quote
        or make_fresh_quote(observed="2026-07-23T14:59:50Z", received="2026-07-23T15:00:00Z"),
        instrument=make_instrument(),
        config=make_risk_config(
            market_quality=make_market_quality(
                proposal=make_phase_limits(age=60, spread="50", deviation="100", vol="500"),
                pretrade=make_phase_limits(age=15, spread="20", deviation="25", vol="200"),
                max_clock_skew_seconds=2,
            )
        ),
        short_term_volatility_bps=None if vol is None else Decimal(vol),
    )


@pytest.mark.unit
def test_stale_quote_rejected() -> None:
    # received_at matches as_of so rejection is from age, not clock skew.
    ctx = _ctx(
        quote=make_fresh_quote(observed="2026-07-23T14:50:00Z", received="2026-07-23T15:00:00Z")
    )
    result = QuoteFreshnessRule().evaluate(ctx)
    assert result.decision is RiskOutcome.REJECTED
    assert "quote age exceeds maximum" in (result.reason or "")


@pytest.mark.unit
def test_crossed_and_missing_bid_ask_rejected() -> None:
    # Align received_at with as_of so clock-skew does not mask bid/ask checks.
    missing_bid = make_fresh_quote(
        observed="2026-07-23T14:59:50Z", received="2026-07-23T15:00:00Z"
    ).model_copy(update={"bid": None})
    missing_bid_result = QuoteFreshnessRule().evaluate(_ctx(quote=missing_bid))
    assert missing_bid_result.decision is RiskOutcome.REJECTED
    assert "bid and ask are required" in (missing_bid_result.reason or "")

    missing_ask = make_fresh_quote(
        observed="2026-07-23T14:59:50Z", received="2026-07-23T15:00:00Z"
    ).model_copy(update={"ask": None})
    missing_ask_result = QuoteFreshnessRule().evaluate(_ctx(quote=missing_ask))
    assert missing_ask_result.decision is RiskOutcome.REJECTED
    assert "bid and ask are required" in (missing_ask_result.reason or "")

    # Schema rejects bid>ask on validate; construct + setattr reaches the rule guard.
    fresh = make_fresh_quote(observed="2026-07-23T14:59:50Z", received="2026-07-23T15:00:00Z")
    crossed = MarketQuote.model_construct(
        schema_version=fresh.schema_version,
        instrument=fresh.instrument,
        last_price=fresh.last_price,
        bid=Decimal("215.00"),
        ask=Decimal("214.00"),
        currency=fresh.currency,
        provenance=fresh.provenance,
    )
    crossed_ctx = _ctx()
    object.__setattr__(crossed_ctx, "quote", crossed)
    freshness = QuoteFreshnessRule().evaluate(crossed_ctx)
    assert freshness.decision is RiskOutcome.REJECTED
    assert "crossed market" in (freshness.reason or "").lower()
    assert SpreadRule().evaluate(crossed_ctx).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_spread_boundary() -> None:
    # mid=214.50; (ask-bid)/mid * 10000 = 1.0725/214.5 * 10000 = 50.0 exactly.
    # Rule rejects only when spread_bps > limit, so equality is approved.
    quote = make_fresh_quote(
        observed="2026-07-23T14:59:50Z",
        received="2026-07-23T15:00:00Z",
        bid="213.96375",
        ask="215.03625",
    )
    assert SpreadRule().evaluate(_ctx(quote=quote)).decision is RiskOutcome.APPROVED

    just_over = make_fresh_quote(
        observed="2026-07-23T14:59:50Z",
        received="2026-07-23T15:00:00Z",
        bid="213.96",
        ask="215.04",
    )
    # (215.04-213.96)/214.5 * 10000 ≈ 50.35 > 50
    assert SpreadRule().evaluate(_ctx(quote=just_over)).decision is RiskOutcome.REJECTED

    wide = make_fresh_quote(
        observed="2026-07-23T14:59:50Z",
        received="2026-07-23T15:00:00Z",
        bid="200.00",
        ask="230.00",
    )
    assert SpreadRule().evaluate(_ctx(quote=wide)).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_volatility_missing_and_over_limit() -> None:
    assert VolatilityRule().evaluate(_ctx(vol=None)).decision is RiskOutcome.REJECTED
    assert VolatilityRule().evaluate(_ctx(vol="9999")).decision is RiskOutcome.REJECTED
    assert VolatilityRule().evaluate(_ctx(vol="10")).decision is RiskOutcome.APPROVED


@pytest.mark.unit
def test_limit_deviation_and_phase_thresholds() -> None:
    # mid ~214.50; limit far away
    ctx = _ctx(limit_price="250.00")
    assert LimitDeviationRule().evaluate(ctx).decision is RiskOutcome.REJECTED

    # Same quote age ok for proposal (60s) but not pretrade (15s).
    # Keep received_at near as_of so clock-skew does not dominate.
    quote = make_fresh_quote(observed="2026-07-23T14:59:30Z", received="2026-07-23T15:00:00Z")
    proposal = _ctx(phase=EvaluationPhase.PROPOSAL, quote=quote)
    pretrade = _ctx(phase=EvaluationPhase.PRETRADE, quote=quote)
    assert QuoteFreshnessRule().evaluate(proposal).decision is RiskOutcome.APPROVED
    assert QuoteFreshnessRule().evaluate(pretrade).decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_pretrade_stricter_spread_cannot_reuse_proposal_pass() -> None:
    """A quote that passes proposal spread can still fail pretrade thresholds."""
    # ~40 bps spread: passes proposal max 50, fails pretrade max 20
    quote = make_fresh_quote(
        observed="2026-07-23T14:59:55Z",
        received="2026-07-23T14:59:56Z",
        bid="214.07",
        ask="214.93",
    )
    assert SpreadRule().evaluate(_ctx(phase=EvaluationPhase.PROPOSAL, quote=quote)).decision is (
        RiskOutcome.APPROVED
    )
    assert SpreadRule().evaluate(_ctx(phase=EvaluationPhase.PRETRADE, quote=quote)).decision is (
        RiskOutcome.REJECTED
    )
