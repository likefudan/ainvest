"""Duplicate and open-order conflict rules (P03-T12).

Detects duplicate submissions by proposal hash, client order ID, and
instrument/side time window, plus opposing or overlapping open orders.
Kill-switch blocking of new orders also lives here as a hard rule.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from ainvest.risk.models import (
    EvaluationPhase,
    RiskContext,
    RuleResult,
)
from ainvest.risk.rules.results import approve, hard_reject
from ainvest.schemas.common import OrderSide

if TYPE_CHECKING:
    from ainvest.risk.rules import RiskRule


def _require_recent_submissions(context: RiskContext, code: str) -> RuleResult | None:
    """Fail closed at PRETRADE when submission history was not loaded."""
    if context.recent_submissions is not None:
        return None
    if context.phase is EvaluationPhase.PRETRADE:
        return hard_reject(
            code,
            "recent submission history is required for pre-trade duplicate checks",
            evidence="recent_submissions=None",
        )
    return approve(code, "recent submissions not supplied at proposal phase")


class KillSwitchRule:
    """Any active configured or operational kill switch rejects new orders."""

    code = "ORDERS_KILL_SWITCH"

    def evaluate(self, context: RiskContext) -> RuleResult:
        snapshot = context.kill_switch
        if snapshot is None:
            if context.phase is EvaluationPhase.PRETRADE:
                return hard_reject(
                    self.code,
                    "kill switch state is required for pre-trade evaluation",
                    evidence="kill_switch=None",
                )
            return approve(self.code, "kill switch not supplied at proposal phase")
        if snapshot.is_active:
            sources = ",".join(snapshot.active_sources) or "UNKNOWN"
            reason = snapshot.reason or "kill switch is active"
            return hard_reject(
                self.code,
                "kill switch is active; new orders are blocked",
                evidence=f"sources={sources}; reason={reason}",
            )
        return approve(self.code, "kill switch is inactive")


class DuplicateProposalHashRule:
    """Reject when the proposal order hash was already submitted."""

    code = "ORDERS_DUPLICATE_PROPOSAL_HASH"

    def evaluate(self, context: RiskContext) -> RuleResult:
        missing_history = _require_recent_submissions(context, self.code)
        if missing_history is not None:
            return missing_history
        assert context.recent_submissions is not None

        if context.proposal_order_hash is None:
            if context.phase is EvaluationPhase.PRETRADE:
                return hard_reject(
                    self.code,
                    "proposal order hash is required for pre-trade duplicate checks",
                    evidence="proposal_order_hash=None",
                )
            return approve(self.code, "proposal order hash not supplied at proposal phase")

        for prior in context.recent_submissions:
            if prior.order_hash == context.proposal_order_hash:
                return hard_reject(
                    self.code,
                    "duplicate proposal order hash",
                    evidence=(
                        f"order_hash={context.proposal_order_hash}; "
                        f"prior_client_order_id={prior.client_order_id}"
                    ),
                )
        return approve(self.code, "proposal order hash is unique among recent submissions")


class DuplicateClientOrderIdRule:
    """Reject when the client order ID was already used."""

    code = "ORDERS_DUPLICATE_CLIENT_ORDER_ID"

    def evaluate(self, context: RiskContext) -> RuleResult:
        missing_history = _require_recent_submissions(context, self.code)
        if missing_history is not None:
            return missing_history
        assert context.recent_submissions is not None

        if context.client_order_id is None:
            if context.phase is EvaluationPhase.PRETRADE:
                return hard_reject(
                    self.code,
                    "client order id is required for pre-trade duplicate checks",
                    evidence="client_order_id=None",
                )
            return approve(self.code, "client order id not supplied at proposal phase")

        for prior in context.recent_submissions:
            if prior.client_order_id == context.client_order_id:
                return hard_reject(
                    self.code,
                    "duplicate client order id",
                    evidence=(
                        f"client_order_id={context.client_order_id}; "
                        f"prior_order_hash={prior.order_hash}"
                    ),
                )
        return approve(self.code, "client order id is unique among recent submissions")


class DuplicateSymbolSideWindowRule:
    """Reject same instrument+side submissions inside the configured time window.

    Matching is by ``instrument_id`` and ``side`` (canonical identity). Symbol is
    evidence-only and must not gate the reject decision (ticker rename / drift).
    """

    code = "ORDERS_DUPLICATE_SYMBOL_SIDE_WINDOW"

    def evaluate(self, context: RiskContext) -> RuleResult:
        missing_history = _require_recent_submissions(context, self.code)
        if missing_history is not None:
            return missing_history
        assert context.recent_submissions is not None

        window = context.config.order_conflicts.duplicate_window_seconds
        cand = context.candidate
        cutoff = context.as_of - timedelta(seconds=window)
        for prior in context.recent_submissions:
            if prior.submitted_at < cutoff:
                continue
            if prior.instrument_id == cand.instrument_id and prior.side is cand.side:
                return hard_reject(
                    self.code,
                    "duplicate instrument/side submission within the configured window",
                    evidence=(
                        f"instrument_id={cand.instrument_id}; "
                        f"candidate_symbol={cand.symbol}; "
                        f"prior_symbol={prior.symbol}; "
                        f"side={cand.side.value}; "
                        f"window_seconds={window}; "
                        f"prior_client_order_id={prior.client_order_id}; "
                        f"prior_submitted_at={prior.submitted_at.isoformat()}"
                    ),
                )
        return approve(
            self.code,
            "no recent same-instrument/side submission in the duplicate window",
            evidence=f"window_seconds={window}",
        )


class OpenOrderConflictRule:
    """Reject opposing or overlapping open orders for the candidate instrument."""

    code = "ORDERS_OPEN_ORDER_CONFLICT"

    def evaluate(self, context: RiskContext) -> RuleResult:
        portfolio = context.portfolio
        if portfolio is None:
            if context.phase is EvaluationPhase.PRETRADE:
                return hard_reject(
                    self.code,
                    "portfolio snapshot with open orders is required for pre-trade",
                    evidence="portfolio=None",
                )
            return approve(self.code, "open-order conflict not evaluated without portfolio")

        cand = context.candidate
        opposing: list[str] = []
        overlapping: list[str] = []
        for order in portfolio.open_orders:
            if order.instrument.instrument_id != cand.instrument_id:
                continue
            if order.side is cand.side:
                overlapping.append(order.order_id)
            elif (cand.side is OrderSide.BUY and order.side is OrderSide.SELL) or (
                cand.side is OrderSide.SELL and order.side is OrderSide.BUY
            ):
                opposing.append(order.order_id)

        if opposing:
            return hard_reject(
                self.code,
                "opposing open order exists for the same instrument",
                evidence=(
                    f"candidate_side={cand.side.value}; "
                    f"opposing_order_ids={','.join(sorted(opposing))}"
                ),
            )
        if overlapping:
            return hard_reject(
                self.code,
                "overlapping open order exists for the same instrument and side",
                evidence=(
                    f"candidate_side={cand.side.value}; "
                    f"overlapping_order_ids={','.join(sorted(overlapping))}"
                ),
            )
        return approve(self.code, "no opposing or overlapping open orders")


def build_order_rules() -> dict[str, RiskRule]:
    """Instantiate the P03-T12 order / kill-switch rule set."""
    rules: tuple[RiskRule, ...] = (
        KillSwitchRule(),
        DuplicateProposalHashRule(),
        DuplicateClientOrderIdRule(),
        DuplicateSymbolSideWindowRule(),
        OpenOrderConflictRule(),
    )
    return {rule.code: rule for rule in rules}


__all__ = [
    "DuplicateClientOrderIdRule",
    "DuplicateProposalHashRule",
    "DuplicateSymbolSideWindowRule",
    "KillSwitchRule",
    "OpenOrderConflictRule",
    "build_order_rules",
]
