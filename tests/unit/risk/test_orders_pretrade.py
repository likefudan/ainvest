"""Unit tests for kill switch, order conflict rules, and pre-trade (P03-T12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from shared.portfolio_fixtures import make_cash_portfolio, make_open_order, with_open_orders

from ainvest.data.calendar_port import FakeMarketCalendar
from ainvest.risk.engine import evaluate_risk, evaluate_rules
from ainvest.risk.kill_switch import KillSwitch, KillSwitchAlertKind
from ainvest.risk.models import (
    EvaluationPhase,
    ExposureInputs,
    KillSwitchSnapshot,
    RecentOrderSubmission,
    RiskContext,
    SectorAssignment,
)
from ainvest.risk.pretrade import PretradeRequest, evaluate_pretrade
from ainvest.risk.rules import DEFAULT_ORDER_RULE_CODES, PRETRADE_RULE_CODES
from ainvest.risk.rules.orders import (
    DuplicateClientOrderIdRule,
    DuplicateProposalHashRule,
    DuplicateSymbolSideWindowRule,
    KillSwitchRule,
    OpenOrderConflictRule,
)
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.risk import RiskDecision, RiskOutcome, RiskSeverity
from risk.risk_fixtures import (
    make_candidate,
    make_context,
    make_fresh_quote,
    make_instrument,
    make_market_quality,
    make_phase_limits,
    make_risk_config,
)

# Friday regular session (14:30 America/New_York).
AS_OF = datetime(2026, 7, 24, 18, 30, tzinfo=UTC)
HASH_A = "sha256:" + ("a" * 64)
HASH_B = "sha256:" + ("b" * 64)
CALENDAR = FakeMarketCalendar()


def _submission(**overrides: Any) -> RecentOrderSubmission:
    payload: dict[str, Any] = {
        "client_order_id": "client_ord_prior",
        "order_hash": HASH_A,
        "instrument_id": "rh_inst_aapl_xnas",
        "symbol": "AAPL",
        "side": "BUY",
        "submitted_at": "2026-07-24T18:25:00Z",
    }
    payload.update(overrides)
    return RecentOrderSubmission.model_validate(payload)


def _quote() -> MarketQuote:
    return make_fresh_quote(
        observed="2026-07-24T18:29:50Z",
        received="2026-07-24T18:29:55Z",
        last_price="214.50",
        bid="214.48",
        ask="214.52",
    )


def _portfolio(**overrides: Any) -> PortfolioSnapshot:
    return make_cash_portfolio(cash="5000.00", **overrides)


def _order_ctx(**overrides: Any) -> RiskContext:
    base: dict[str, Any] = {
        "risk_decision_id": "risk_01HZYORDERS00001",
        "phase": EvaluationPhase.PRETRADE,
        "as_of": AS_OF,
        "candidate": make_candidate(
            created_at="2026-07-24T18:29:00Z",
            expires_at="2026-07-24T19:00:00Z",
        ),
        "quote": _quote(),
        "instrument": make_instrument(),
        "config": make_risk_config(),
        "portfolio": _portfolio(),
        "kill_switch": KillSwitchSnapshot(),
        "client_order_id": "client_ord_new",
        "proposal_order_hash": HASH_B,
        "recent_submissions": (),
    }
    base.update(overrides)
    return make_context(**base)


class _FakeMarketData:
    def __init__(self, *, quote: MarketQuote, portfolio: PortfolioSnapshot) -> None:
        self.quote = quote
        self.portfolio = portfolio
        self.quote_fetches = 0
        self.portfolio_fetches = 0

    def fetch_quote(self, instrument_id: str, *, as_of: datetime) -> MarketQuote:
        del instrument_id, as_of
        self.quote_fetches += 1
        return self.quote

    def fetch_portfolio(self, *, as_of: datetime) -> PortfolioSnapshot:
        del as_of
        self.portfolio_fetches += 1
        return self.portfolio


def _exposure() -> ExposureInputs:
    return ExposureInputs(
        sectors=(SectorAssignment(instrument_id="rh_inst_aapl_xnas", sector="TECH"),),
        daily_turnover_to_date=Decimal("0"),
        daily_realized_pnl=Decimal("0"),
        daily_unrealized_pnl=Decimal("0"),
    )


def _pretrade_request(**overrides: Any) -> PretradeRequest:
    payload: dict[str, Any] = {
        "risk_decision_id": "risk_01HZYPRETRADE001",
        "as_of": AS_OF,
        "candidate": make_candidate(
            created_at="2026-07-24T18:29:00Z",
            expires_at="2026-07-24T19:00:00Z",
        ),
        "instrument": make_instrument(),
        "config": make_risk_config(
            market_quality=make_market_quality(
                proposal=make_phase_limits(age=120, spread="100", deviation="500", vol="1000"),
                pretrade=make_phase_limits(age=120, spread="100", deviation="500", vol="1000"),
                max_clock_skew_seconds=30,
            )
        ),
        "client_order_id": "client_ord_new",
        "proposal_order_hash": HASH_B,
        "prior_proposal_decision_id": "risk_01HZYPROPOSAL001",
        # Explicit empty window (not None / unavailable).
        "recent_submissions": (),
        "exposure_inputs": _exposure(),
        "short_term_volatility_bps": Decimal("10"),
    }
    payload.update(overrides)
    return PretradeRequest.model_validate(payload)


@pytest.mark.unit
def test_kill_switch_blocks_and_alerts_without_auto_cancel() -> None:
    switch = KillSwitch()
    switch.activate_operational(
        reason="operator halt",
        as_of=AS_OF,
        operator_id="op_01",
    )
    assert switch.is_active()
    alerts = switch.drain_alerts()
    assert alerts[0].kind is KillSwitchAlertKind.ACTIVATED

    ctx = _order_ctx(kill_switch=switch.snapshot())
    result = KillSwitchRule().evaluate(ctx)
    assert result.decision is RiskOutcome.REJECTED
    switch.record_blocked_submission(reason="blocked at pretrade", as_of=AS_OF)
    blocked = switch.drain_alerts()
    assert blocked[0].kind is KillSwitchAlertKind.BLOCKED_NEW_ORDER

    with pytest.raises(RuntimeError, match="does not auto-cancel"):
        switch.cancel_open_orders()


@pytest.mark.unit
def test_configured_or_operational_source_rejects() -> None:
    configured = KillSwitchSnapshot(configured_active=True, reason="policy halt")
    operational = KillSwitchSnapshot(operational_active=True, reason="ops halt")
    for snapshot in (configured, operational):
        result = KillSwitchRule().evaluate(_order_ctx(kill_switch=snapshot))
        assert result.decision is RiskOutcome.REJECTED


@pytest.mark.unit
def test_duplicate_proposal_hash_and_client_order_id() -> None:
    prior = _submission(client_order_id="client_ord_dup", order_hash=HASH_A)
    hash_hit = DuplicateProposalHashRule().evaluate(
        _order_ctx(proposal_order_hash=HASH_A, recent_submissions=(prior,))
    )
    assert hash_hit.decision is RiskOutcome.REJECTED
    assert hash_hit.rule_code == "ORDERS_DUPLICATE_PROPOSAL_HASH"

    id_hit = DuplicateClientOrderIdRule().evaluate(
        _order_ctx(client_order_id="client_ord_dup", recent_submissions=(prior,))
    )
    assert id_hit.decision is RiskOutcome.REJECTED
    assert id_hit.rule_code == "ORDERS_DUPLICATE_CLIENT_ORDER_ID"


@pytest.mark.unit
def test_duplicate_symbol_side_window() -> None:
    inside = _submission(submitted_at="2026-07-24T18:26:00Z")
    result = DuplicateSymbolSideWindowRule().evaluate(_order_ctx(recent_submissions=(inside,)))
    assert result.decision is RiskOutcome.REJECTED

    outside = _submission(submitted_at="2026-07-24T17:00:00Z")
    ok = DuplicateSymbolSideWindowRule().evaluate(_order_ctx(recent_submissions=(outside,)))
    assert ok.decision is RiskOutcome.APPROVED


@pytest.mark.unit
def test_duplicate_window_matches_instrument_id_ignoring_symbol() -> None:
    """Ticker rename / symbol drift must not bypass instrument+side window checks."""
    prior = _submission(
        instrument_id="rh_inst_aapl_xnas",
        symbol="AAPL.OLD",
        side="BUY",
        submitted_at="2026-07-24T18:26:00Z",
    )
    result = DuplicateSymbolSideWindowRule().evaluate(_order_ctx(recent_submissions=(prior,)))
    assert result.decision is RiskOutcome.REJECTED
    assert result.evidence is not None
    assert "instrument_id=rh_inst_aapl_xnas" in result.evidence
    assert "prior_symbol=AAPL.OLD" in result.evidence


@pytest.mark.unit
def test_unavailable_recent_submissions_fail_closed_at_pretrade() -> None:
    for rule in (
        DuplicateProposalHashRule(),
        DuplicateClientOrderIdRule(),
        DuplicateSymbolSideWindowRule(),
    ):
        result = rule.evaluate(_order_ctx(recent_submissions=None))
        assert result.decision is RiskOutcome.REJECTED
        assert result.evidence == "recent_submissions=None"

    output = evaluate_pretrade(
        _pretrade_request(recent_submissions=None),
        market_data=_FakeMarketData(quote=_quote(), portfolio=_portfolio()),
        kill_switch=KillSwitch(),
        calendar=CALENDAR,
    )
    assert output.decision.outcome is RiskOutcome.REJECTED
    codes = {v.rule_code for v in output.decision.violations}
    assert "ORDERS_DUPLICATE_PROPOSAL_HASH" in codes
    assert "ORDERS_DUPLICATE_CLIENT_ORDER_ID" in codes
    assert "ORDERS_DUPLICATE_SYMBOL_SIDE_WINDOW" in codes


@pytest.mark.unit
def test_explicit_empty_recent_submissions_allows_duplicate_checks_to_pass() -> None:
    ctx = _order_ctx(recent_submissions=())
    assert DuplicateProposalHashRule().evaluate(ctx).decision is RiskOutcome.APPROVED
    assert DuplicateClientOrderIdRule().evaluate(ctx).decision is RiskOutcome.APPROVED
    assert DuplicateSymbolSideWindowRule().evaluate(ctx).decision is RiskOutcome.APPROVED


@pytest.mark.unit
def test_kill_switch_configured_deactivate_records_changed_source() -> None:
    switch = KillSwitch(configured_active=True)
    switch.set_configured(False, reason="policy clear", as_of=AS_OF)
    assert not switch.is_active()
    alerts = switch.drain_alerts()
    assert len(alerts) == 1
    assert alerts[0].kind is KillSwitchAlertKind.DEACTIVATED
    assert alerts[0].sources == ("CONFIGURED",)


@pytest.mark.unit
def test_opposing_and_overlapping_open_orders() -> None:
    opposing = with_open_orders(
        _portfolio(),
        make_open_order(order_id="ord_sell", side="SELL", quantity="1", limit_price="214.50"),
    )
    assert (
        OpenOrderConflictRule().evaluate(_order_ctx(portfolio=opposing)).decision
        is RiskOutcome.REJECTED
    )

    overlapping = with_open_orders(
        _portfolio(),
        make_open_order(order_id="ord_buy", side="BUY", quantity="1", limit_price="214.50"),
    )
    assert (
        OpenOrderConflictRule().evaluate(_order_ctx(portfolio=overlapping)).decision
        is RiskOutcome.REJECTED
    )


@pytest.mark.unit
def test_pretrade_rejects_duplicate_delivery() -> None:
    prior = _submission(client_order_id="client_ord_new", order_hash=HASH_B)
    market = _FakeMarketData(quote=_quote(), portfolio=_portfolio())
    output = evaluate_pretrade(
        _pretrade_request(recent_submissions=(prior,)),
        market_data=market,
        kill_switch=KillSwitch(),
        calendar=CALENDAR,
    )
    assert output.decision.outcome is RiskOutcome.REJECTED
    codes = {v.rule_code for v in output.decision.violations}
    assert "ORDERS_DUPLICATE_CLIENT_ORDER_ID" in codes
    assert market.quote_fetches == 1 and market.portfolio_fetches == 1


@pytest.mark.unit
def test_pretrade_rejects_stale_snapshots() -> None:
    payload = _portfolio().model_dump(mode="python")
    payload["as_of"] = "2026-07-24T17:00:00Z"
    payload["provenance"]["observed_at"] = "2026-07-24T16:59:50Z"
    payload["provenance"]["received_at"] = "2026-07-24T16:59:55Z"
    stale_portfolio = PortfolioSnapshot.model_validate(payload)
    with pytest.raises(ValueError, match="portfolio snapshot is stale"):
        evaluate_pretrade(
            _pretrade_request(),
            market_data=_FakeMarketData(quote=_quote(), portfolio=stale_portfolio),
            kill_switch=KillSwitchSnapshot(),
            calendar=CALENDAR,
        )


@pytest.mark.unit
def test_pretrade_rejects_active_kill_switch() -> None:
    switch = KillSwitch(configured_active=True)
    switch.set_configured(True, reason="configured halt", as_of=AS_OF)
    output = evaluate_pretrade(
        _pretrade_request(),
        market_data=_FakeMarketData(quote=_quote(), portfolio=_portfolio()),
        kill_switch=switch,
        calendar=CALENDAR,
    )
    assert output.decision.outcome is RiskOutcome.REJECTED
    assert any(v.rule_code == "ORDERS_KILL_SWITCH" for v in output.decision.violations)
    assert any(a.kind is KillSwitchAlertKind.BLOCKED_NEW_ORDER for a in switch.drain_alerts())


@pytest.mark.unit
def test_pretrade_rejects_existing_open_orders() -> None:
    portfolio = with_open_orders(
        _portfolio(),
        make_open_order(
            order_id="ord_open_buy",
            side="BUY",
            quantity="2",
            limit_price="214.50",
        ),
    )
    output = evaluate_pretrade(
        _pretrade_request(),
        market_data=_FakeMarketData(quote=_quote(), portfolio=portfolio),
        kill_switch=KillSwitch(),
        calendar=CALENDAR,
    )
    assert output.decision.outcome is RiskOutcome.REJECTED
    assert any(v.rule_code == "ORDERS_OPEN_ORDER_CONFLICT" for v in output.decision.violations)


@pytest.mark.unit
def test_pretrade_never_reuses_prior_approved_decision() -> None:
    prior = RiskDecision(
        risk_decision_id="risk_01HZYPROPOSAL001",
        candidate_id=make_candidate().candidate_id,
        proposal_id=None,
        outcome=RiskOutcome.APPROVED,
        decided_at=AS_OF - timedelta(minutes=1),
        rule_set_version="risk-rules-1.0.0",
        violations=(),
        reason_code="ALL_RULES_PASSED",
        reason="all hard and review rules passed",
    )
    request = _pretrade_request()
    with pytest.raises(ValueError, match="must not reuse"):
        PretradeRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "risk_decision_id": "risk_01HZYPROPOSAL001",
                "prior_proposal_decision_id": "risk_01HZYPROPOSAL001",
            }
        )

    switch = KillSwitch()
    switch.activate_operational(reason="halt", as_of=AS_OF)
    output = evaluate_pretrade(
        request,
        market_data=_FakeMarketData(quote=_quote(), portfolio=_portfolio()),
        kill_switch=switch,
        calendar=CALENDAR,
        prior_decision=prior,
    )
    assert output.decision.outcome is RiskOutcome.REJECTED
    assert output.decision.risk_decision_id == "risk_01HZYPRETRADE001"
    assert output.decision.risk_decision_id != prior.risk_decision_id


@pytest.mark.unit
def test_pretrade_runs_every_hard_order_rule_again() -> None:
    output = evaluate_pretrade(
        _pretrade_request(),
        market_data=_FakeMarketData(quote=_quote(), portfolio=_portfolio()),
        kill_switch=KillSwitch(),
        calendar=CALENDAR,
    )
    assert set(DEFAULT_ORDER_RULE_CODES).issubset(set(output.rule_codes))
    assert output.rule_codes == PRETRADE_RULE_CODES
    order_results = {
        r.rule_code: r for r in output.rule_results if r.rule_code in DEFAULT_ORDER_RULE_CODES
    }
    assert set(order_results) == set(DEFAULT_ORDER_RULE_CODES)
    for result in order_results.values():
        assert result.severity in {RiskSeverity.HARD, RiskSeverity.INFO}


@pytest.mark.unit
def test_proposal_phase_does_not_require_order_ids() -> None:
    ctx = make_context(
        phase=EvaluationPhase.PROPOSAL,
        as_of=AS_OF,
        quote=_quote(),
        kill_switch=None,
        client_order_id=None,
        proposal_order_hash=None,
    )
    assert KillSwitchRule().evaluate(ctx).decision is RiskOutcome.APPROVED
    assert DuplicateProposalHashRule().evaluate(ctx).decision is RiskOutcome.APPROVED
    assert DuplicateClientOrderIdRule().evaluate(ctx).decision is RiskOutcome.APPROVED


@pytest.mark.unit
def test_evaluate_risk_proposal_path_still_works_without_order_rules() -> None:
    ctx = make_context(
        phase=EvaluationPhase.PROPOSAL,
        as_of=AS_OF,
        candidate=make_candidate(
            created_at="2026-07-24T18:29:00Z",
            expires_at="2026-07-24T19:00:00Z",
        ),
        quote=_quote(),
        portfolio=_portfolio(),
        exposure_inputs=_exposure(),
        short_term_volatility_bps=Decimal("10"),
        config=make_risk_config(
            market_quality=make_market_quality(
                proposal=make_phase_limits(age=120, spread="100", deviation="500", vol="1000"),
                pretrade=make_phase_limits(age=30, spread="25", deviation="50", vol="300"),
                max_clock_skew_seconds=30,
            )
        ),
    )
    output = evaluate_risk(ctx, calendar=CALENDAR)
    assert "ORDERS_KILL_SWITCH" not in output.rule_codes
    assert output.decision.outcome in {
        RiskOutcome.APPROVED,
        RiskOutcome.REJECTED,
        RiskOutcome.NEEDS_REVIEW,
    }


@pytest.mark.unit
def test_order_rules_fail_closed_when_pretrade_inputs_missing() -> None:
    ctx = _order_ctx(
        kill_switch=None,
        client_order_id=None,
        proposal_order_hash=None,
        portfolio=None,
    )
    results = evaluate_rules(
        ctx,
        [
            KillSwitchRule(),
            DuplicateProposalHashRule(),
            DuplicateClientOrderIdRule(),
            OpenOrderConflictRule(),
        ],
    )
    assert results.decision.outcome is RiskOutcome.REJECTED
    codes = {v.rule_code for v in results.decision.violations}
    assert codes >= {
        "ORDERS_KILL_SWITCH",
        "ORDERS_DUPLICATE_PROPOSAL_HASH",
        "ORDERS_DUPLICATE_CLIENT_ORDER_ID",
        "ORDERS_OPEN_ORDER_CONFLICT",
    }
