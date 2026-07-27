"""Asset eligibility, allowlist, side, and session rules (P03-T10)."""

from __future__ import annotations

from decimal import Decimal

from ainvest.data.calendar_port import MarketCalendar, SessionStatus
from ainvest.risk.models import RiskContext, RuleResult
from ainvest.risk.rules import register_rule
from ainvest.schemas.common import AssetType, OrderSide, canonicalize_decimal
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


class AssetClassRule:
    """Allow only EQUITY/ETF; reject options/crypto metadata flags."""

    code = "ELIGIBILITY_ASSET_CLASS"

    def evaluate(self, context: RiskContext) -> RuleResult:
        cand = context.candidate
        meta = context.instrument
        if meta.is_option or cand.asset_type not in {AssetType.EQUITY, AssetType.ETF}:
            return _hard(
                self.code,
                "options and non equity/ETF asset types are rejected",
                evidence=f"asset_type={cand.asset_type.value}; is_option={meta.is_option}",
            )
        if meta.is_crypto:
            return _hard(
                self.code,
                "crypto instruments are rejected",
                evidence=f"instrument_id={meta.instrument_id}",
            )
        return _ok(self.code, "asset class is ordinary US equity or ETF")


class AllowlistRule:
    """Candidate must match an explicit allowlist entry."""

    code = "ELIGIBILITY_ALLOWLIST"

    def evaluate(self, context: RiskContext) -> RuleResult:
        allowlist = context.config.eligibility.allowlist
        cand = context.candidate
        for entry in allowlist:
            if (
                entry.instrument_id == cand.instrument_id
                and entry.symbol == cand.symbol
                and entry.exchange == cand.exchange
                and entry.currency == cand.currency
                and entry.asset_type == cand.asset_type
            ):
                return _ok(
                    self.code,
                    "candidate matches instrument allowlist",
                    evidence=f"instrument_id={cand.instrument_id}",
                )
        return _hard(
            self.code,
            "candidate is not on the instrument allowlist",
            evidence=(
                f"instrument_id={cand.instrument_id}; symbol={cand.symbol}; "
                f"exchange={cand.exchange}"
            ),
        )


class IdentityConsistencyRule:
    """Canonical identity and broker precision must match the candidate."""

    code = "ELIGIBILITY_IDENTITY"

    def evaluate(self, context: RiskContext) -> RuleResult:
        cand = context.candidate
        meta = context.instrument
        quote = context.quote
        mismatches: list[str] = []
        if meta.instrument_id != cand.instrument_id:
            mismatches.append("instrument_id")
        if meta.symbol != cand.symbol:
            mismatches.append("symbol")
        if meta.exchange != cand.exchange:
            mismatches.append("exchange")
        if meta.currency != cand.currency:
            mismatches.append("currency")
        if meta.asset_type != cand.asset_type:
            mismatches.append("asset_type")
        if quote.instrument.instrument_id != cand.instrument_id:
            mismatches.append("quote.instrument_id")
        if quote.instrument.symbol != cand.symbol:
            mismatches.append("quote.symbol")
        if not meta.tradable:
            return _hard(
                self.code,
                "instrument is not tradable at the broker",
                evidence=f"instrument_id={meta.instrument_id}",
            )
        if canonicalize_decimal(meta.price_increment) != canonicalize_decimal(cand.price_increment):
            mismatches.append("price_increment")
        if canonicalize_decimal(meta.quantity_increment) != canonicalize_decimal(
            cand.quantity_increment
        ):
            mismatches.append("quantity_increment")
        if mismatches:
            return _hard(
                self.code,
                "canonical instrument identity or precision metadata mismatch",
                evidence=",".join(mismatches),
            )
        return _ok(self.code, "canonical instrument identity is consistent")


class SideAndProductRule:
    """Reject shorts, margin, and leveraged/inverse products."""

    code = "ELIGIBILITY_SIDE_AND_PRODUCT"

    def evaluate(self, context: RiskContext) -> RuleResult:
        meta = context.instrument
        cand = context.candidate
        if meta.allows_margin:
            return _hard(
                self.code,
                "margin trading is rejected",
                evidence=f"instrument_id={meta.instrument_id}",
            )
        if meta.allows_short:
            return _hard(
                self.code,
                "short selling is rejected",
                evidence=f"instrument_id={meta.instrument_id}",
            )
        if meta.is_leveraged_or_inverse:
            return _hard(
                self.code,
                "leveraged and inverse ETFs are rejected",
                evidence=f"instrument_id={meta.instrument_id}",
            )
        if cand.side is OrderSide.SELL:
            if context.portfolio is None:
                return _hard(
                    self.code,
                    "SELL requires portfolio context to prove a long reduction",
                    evidence=f"instrument_id={cand.instrument_id}",
                )
            held = _held_qty(context.portfolio, cand.instrument_id)
            if held < cand.quantity:
                return _hard(
                    self.code,
                    "SELL quantity exceeds held long position (no shorts)",
                    evidence=f"held={held}; sell_qty={cand.quantity}",
                )
        return _ok(self.code, "side and product constraints satisfied")


class SessionRule:
    """Regular US equity session only; holidays/early-close/unknown fail closed."""

    code = "ELIGIBILITY_SESSION"

    def __init__(self, calendar: MarketCalendar) -> None:
        self._calendar = calendar

    def evaluate(self, context: RiskContext) -> RuleResult:
        status = self._calendar.session_status(context.as_of, exchange=context.candidate.exchange)
        if status is SessionStatus.UNKNOWN:
            return _hard(
                self.code,
                "market calendar returned UNKNOWN (fail closed)",
                evidence=f"exchange={context.candidate.exchange}",
            )
        if status is SessionStatus.HOLIDAY:
            return _hard(self.code, "trading holiday; new orders rejected")
        if status is SessionStatus.EARLY_CLOSED:
            return _hard(self.code, "past early close; new orders rejected")
        if status is SessionStatus.CLOSED:
            return _hard(self.code, "outside regular trading session")
        # SessionStatus is a closed enum; OPEN is the only remaining value.
        return _ok(self.code, "regular trading session is open")


def _held_qty(portfolio: object, instrument_id: str) -> Decimal:
    from ainvest.schemas.portfolio import PortfolioSnapshot

    assert isinstance(portfolio, PortfolioSnapshot)
    for position in portfolio.positions:
        if position.instrument.instrument_id == instrument_id:
            return position.quantity
    return Decimal("0")


def register_eligibility_rules(calendar: MarketCalendar) -> None:
    """Register eligibility rules; session rule binds the provided calendar."""
    register_rule(AssetClassRule.code, AssetClassRule)
    register_rule(AllowlistRule.code, AllowlistRule)
    register_rule(IdentityConsistencyRule.code, IdentityConsistencyRule)
    register_rule(SideAndProductRule.code, SideAndProductRule)
    register_rule(SessionRule.code, lambda: SessionRule(calendar))


__all__ = [
    "AllowlistRule",
    "AssetClassRule",
    "IdentityConsistencyRule",
    "SessionRule",
    "SideAndProductRule",
    "register_eligibility_rules",
]
