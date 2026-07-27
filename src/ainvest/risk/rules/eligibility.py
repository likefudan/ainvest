"""Asset eligibility, allowlist, side, and session rules (P03-T10)."""

from __future__ import annotations

from decimal import Decimal

from ainvest.data.calendar_port import MarketCalendar, SessionStatus
from ainvest.risk.models import RiskContext, RuleResult
from ainvest.risk.rules.results import approve, hard_reject
from ainvest.schemas.common import AssetType, OrderSide, canonicalize_decimal


class AssetClassRule:
    """Allow only EQUITY/ETF; reject options/crypto metadata flags."""

    code = "ELIGIBILITY_ASSET_CLASS"

    def evaluate(self, context: RiskContext) -> RuleResult:
        cand = context.candidate
        meta = context.instrument
        if meta.is_option or cand.asset_type not in {AssetType.EQUITY, AssetType.ETF}:
            return hard_reject(
                self.code,
                "options and non equity/ETF asset types are rejected",
                evidence=f"asset_type={cand.asset_type.value}; is_option={meta.is_option}",
            )
        if meta.is_crypto:
            return hard_reject(
                self.code,
                "crypto instruments are rejected",
                evidence=f"instrument_id={meta.instrument_id}",
            )
        return approve(self.code, "asset class is ordinary US equity or ETF")


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
                return approve(
                    self.code,
                    "candidate matches instrument allowlist",
                    evidence=f"instrument_id={cand.instrument_id}",
                )
        return hard_reject(
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
            return hard_reject(
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
            return hard_reject(
                self.code,
                "canonical instrument identity or precision metadata mismatch",
                evidence=",".join(mismatches),
            )
        return approve(self.code, "canonical instrument identity is consistent")


class SideAndProductRule:
    """Reject shorts, margin, and leveraged/inverse products."""

    code = "ELIGIBILITY_SIDE_AND_PRODUCT"

    def evaluate(self, context: RiskContext) -> RuleResult:
        meta = context.instrument
        cand = context.candidate
        if meta.allows_margin:
            return hard_reject(
                self.code,
                "margin trading is rejected",
                evidence=f"instrument_id={meta.instrument_id}",
            )
        if meta.is_leveraged_or_inverse:
            return hard_reject(
                self.code,
                "leveraged and inverse ETFs are rejected",
                evidence=f"instrument_id={meta.instrument_id}",
            )
        if cand.side is OrderSide.SELL:
            # Long-only: SELL may only reduce an existing long. Instruments that
            # are short-enabled at the broker still require portfolio proof.
            if context.portfolio is None:
                return hard_reject(
                    self.code,
                    "SELL requires portfolio context to prove a long reduction",
                    evidence=f"instrument_id={cand.instrument_id}",
                )
            held = _held_qty(context.portfolio, cand.instrument_id)
            open_sells = _open_sell_qty(context.portfolio, cand.instrument_id)
            sellable = held - open_sells
            if sellable < cand.quantity:
                return hard_reject(
                    self.code,
                    "short sales are rejected (SELL exceeds sellable long)",
                    evidence=(
                        f"held={held}; open_sells={open_sells}; sellable={sellable}; "
                        f"sell_qty={cand.quantity}; allows_short={meta.allows_short}"
                    ),
                )
        return approve(self.code, "side and product constraints satisfied")


class SessionRule:
    """Regular US equity session only; holidays/early-close/unknown fail closed."""

    code = "ELIGIBILITY_SESSION"

    def __init__(self, calendar: MarketCalendar) -> None:
        self._calendar = calendar

    def evaluate(self, context: RiskContext) -> RuleResult:
        status = self._calendar.session_status(context.as_of, exchange=context.candidate.exchange)
        if status is SessionStatus.UNKNOWN:
            return hard_reject(
                self.code,
                "market calendar returned UNKNOWN (fail closed)",
                evidence=f"exchange={context.candidate.exchange}",
            )
        if status is SessionStatus.HOLIDAY:
            return hard_reject(self.code, "trading holiday; new orders rejected")
        if status is SessionStatus.EARLY_CLOSED:
            return hard_reject(self.code, "past early close; new orders rejected")
        if status is SessionStatus.CLOSED:
            return hard_reject(self.code, "outside regular trading session")
        # SessionStatus is a closed enum; OPEN is the only remaining value.
        return approve(self.code, "regular trading session is open")


def _held_qty(portfolio: object, instrument_id: str) -> Decimal:
    from ainvest.schemas.portfolio import PortfolioSnapshot

    assert isinstance(portfolio, PortfolioSnapshot)
    for position in portfolio.positions:
        if position.instrument.instrument_id == instrument_id:
            return position.quantity
    return Decimal("0")


def _open_sell_qty(portfolio: object, instrument_id: str) -> Decimal:
    from ainvest.schemas.commitments import open_order_side_quantities
    from ainvest.schemas.portfolio import PortfolioSnapshot

    assert isinstance(portfolio, PortfolioSnapshot)
    _buy_qty, sell_qty = open_order_side_quantities(portfolio, instrument_id)
    return sell_qty


__all__ = [
    "AllowlistRule",
    "AssetClassRule",
    "IdentityConsistencyRule",
    "SessionRule",
    "SideAndProductRule",
]
