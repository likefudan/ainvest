"""Notional, position, sector, cash, and daily loss exposure rules (P03-T9).

All limits are explicit on :class:`~ainvest.risk.models.ExposureLimits`. Missing
portfolio, sector, or P&L inputs fail closed. Evaluations use projected
post-trade cash/positions at the candidate limit price.
"""

from __future__ import annotations

from decimal import Decimal

from ainvest.risk.models import (
    ZERO,
    ExposureInputs,
    RiskContext,
    RuleResult,
)
from ainvest.schemas.common import OrderSide, canonicalize_decimal
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.risk import RiskOutcome, RiskSeverity

_MONEY_QUANT = Decimal("0.000001")
_WEIGHT_QUANT = Decimal("0.00000001")


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


def _money(value: Decimal) -> Decimal:
    return canonicalize_decimal(Decimal(value).quantize(_MONEY_QUANT))


def _weight(value: Decimal) -> Decimal:
    return canonicalize_decimal(Decimal(value).quantize(_WEIGHT_QUANT))


def _require_portfolio(context: RiskContext, code: str) -> PortfolioSnapshot | RuleResult:
    if context.portfolio is None:
        return _hard(code, "portfolio snapshot is required for exposure rules")
    if context.portfolio.equity <= ZERO:
        return _hard(code, "account equity must be positive for exposure rules")
    return context.portfolio


def _require_exposure_inputs(context: RiskContext, code: str) -> ExposureInputs | RuleResult:
    if context.exposure_inputs is None:
        return _hard(code, "exposure inputs are required", evidence="exposure_inputs=None")
    return context.exposure_inputs


def _order_notional(context: RiskContext) -> Decimal:
    cand = context.candidate
    return _money(canonicalize_decimal(cand.quantity) * canonicalize_decimal(cand.limit_price))


def _projected_state(
    context: RiskContext, portfolio: PortfolioSnapshot
) -> tuple[Decimal, Decimal, Decimal]:
    """Return (projected_cash, projected_symbol_mv, projected_equity)."""
    cand = context.candidate
    notional = _order_notional(context)
    mark = canonicalize_decimal(context.quote.last_price)
    qty = canonicalize_decimal(cand.quantity)

    held = ZERO
    for position in portfolio.positions:
        if position.instrument.instrument_id == cand.instrument_id:
            held = canonicalize_decimal(position.quantity)
            break

    if cand.side is OrderSide.BUY:
        projected_qty = held + qty
        projected_cash = _money(portfolio.cash - notional)
    else:
        projected_qty = held - qty
        projected_cash = _money(portfolio.cash + notional)

    other_mv = ZERO
    for position in portfolio.positions:
        if position.instrument.instrument_id == cand.instrument_id:
            continue
        other_mv = _money(other_mv + position.market_value)

    symbol_mv = _money(projected_qty * mark)
    projected_equity = _money(projected_cash + other_mv + symbol_mv)
    return projected_cash, symbol_mv, projected_equity


def _sector_for(inputs: ExposureInputs, instrument_id: str) -> str | None:
    for item in inputs.sectors:
        if item.instrument_id == instrument_id:
            return item.sector
    return None


class MaxOrderNotionalRule:
    code = "EXPOSURE_MAX_ORDER_NOTIONAL"

    def evaluate(self, context: RiskContext) -> RuleResult:
        notional = _order_notional(context)
        limit = canonicalize_decimal(context.config.exposure.max_order_notional)
        if notional > limit:
            return _hard(
                self.code,
                "order notional exceeds maximum",
                evidence=f"notional={notional}; max={limit}",
            )
        return _ok(self.code, "order notional within limit", evidence=f"notional={notional}")


class SymbolWeightRule:
    code = "EXPOSURE_MAX_SYMBOL_WEIGHT"

    def evaluate(self, context: RiskContext) -> RuleResult:
        portfolio = _require_portfolio(context, self.code)
        if isinstance(portfolio, RuleResult):
            return portfolio
        _cash, symbol_mv, equity = _projected_state(context, portfolio)
        if equity <= ZERO:
            return _hard(self.code, "projected equity must be positive")
        weight = _weight(symbol_mv / equity)
        limit = canonicalize_decimal(context.config.exposure.max_symbol_weight)
        if weight > limit:
            return _hard(
                self.code,
                "projected symbol weight exceeds maximum",
                evidence=f"weight={weight}; max={limit}",
            )
        return _ok(self.code, "projected symbol weight within limit", evidence=f"weight={weight}")


class SectorExposureRule:
    code = "EXPOSURE_MAX_SECTOR_WEIGHT"

    def evaluate(self, context: RiskContext) -> RuleResult:
        portfolio = _require_portfolio(context, self.code)
        if isinstance(portfolio, RuleResult):
            return portfolio
        inputs = _require_exposure_inputs(context, self.code)
        if isinstance(inputs, RuleResult):
            return inputs
        sector = _sector_for(inputs, context.candidate.instrument_id)
        if sector is None:
            return _hard(
                self.code,
                "missing sector metadata for candidate instrument",
                evidence=f"instrument_id={context.candidate.instrument_id}",
            )

        _projected_cash, _symbol_mv, equity = _projected_state(context, portfolio)
        if equity <= ZERO:
            return _hard(self.code, "projected equity must be positive")

        # Rebuild position market values after trade for the candidate's sector.
        sector_mv = ZERO
        cand_id = context.candidate.instrument_id
        mark = canonicalize_decimal(context.quote.last_price)
        held = ZERO
        for position in portfolio.positions:
            if position.instrument.instrument_id == cand_id:
                held = canonicalize_decimal(position.quantity)
                continue
            pos_sector = _sector_for(inputs, position.instrument.instrument_id)
            if pos_sector is None:
                return _hard(
                    self.code,
                    "missing sector metadata for portfolio position",
                    evidence=f"instrument_id={position.instrument.instrument_id}",
                )
            if pos_sector == sector:
                sector_mv = _money(sector_mv + position.market_value)

        qty = canonicalize_decimal(context.candidate.quantity)
        projected_qty = held + qty if context.candidate.side is OrderSide.BUY else held - qty
        sector_mv = _money(sector_mv + projected_qty * mark)
        weight = _weight(sector_mv / equity)
        limit = canonicalize_decimal(context.config.exposure.max_sector_weight)
        if weight > limit:
            return _hard(
                self.code,
                "projected sector weight exceeds maximum",
                evidence=f"sector={sector}; weight={weight}; max={limit}",
            )
        return _ok(
            self.code,
            "projected sector weight within limit",
            evidence=f"sector={sector}; weight={weight}",
        )


class DailyTurnoverRule:
    code = "EXPOSURE_MAX_DAILY_TURNOVER"

    def evaluate(self, context: RiskContext) -> RuleResult:
        inputs = _require_exposure_inputs(context, self.code)
        if isinstance(inputs, RuleResult):
            return inputs
        notional = _order_notional(context)
        projected = _money(canonicalize_decimal(inputs.daily_turnover_to_date) + notional)
        limit = canonicalize_decimal(context.config.exposure.max_daily_turnover)
        if projected > limit:
            return _hard(
                self.code,
                "projected daily turnover exceeds maximum",
                evidence=f"turnover={projected}; max={limit}",
            )
        return _ok(
            self.code, "projected daily turnover within limit", evidence=f"turnover={projected}"
        )


class MinCashReserveRule:
    code = "EXPOSURE_MIN_CASH_RESERVE"

    def evaluate(self, context: RiskContext) -> RuleResult:
        portfolio = _require_portfolio(context, self.code)
        if isinstance(portfolio, RuleResult):
            return portfolio
        projected_cash, _symbol_mv, equity = _projected_state(context, portfolio)
        if equity <= ZERO:
            return _hard(self.code, "projected equity must be positive")
        if projected_cash < ZERO:
            return _hard(
                self.code,
                "projected cash would be negative",
                evidence=f"projected_cash={projected_cash}",
            )
        reserve = _weight(projected_cash / equity)
        minimum = canonicalize_decimal(context.config.exposure.min_cash_reserve_weight)
        if reserve < minimum:
            return _hard(
                self.code,
                "projected cash reserve weight below minimum",
                evidence=f"reserve={reserve}; min={minimum}",
            )
        return _ok(self.code, "projected cash reserve within limit", evidence=f"reserve={reserve}")


class DailyLossRule:
    code = "EXPOSURE_MAX_DAILY_LOSS"

    def evaluate(self, context: RiskContext) -> RuleResult:
        inputs = _require_exposure_inputs(context, self.code)
        if isinstance(inputs, RuleResult):
            return inputs
        if inputs.daily_unrealized_pnl is None:
            return _hard(
                self.code,
                "daily unrealized P&L is required (incomplete P&L fail closed)",
            )
        total = canonicalize_decimal(
            canonicalize_decimal(inputs.daily_realized_pnl)
            + canonicalize_decimal(inputs.daily_unrealized_pnl)
        )
        # Loss is negative P&L; reject when loss magnitude exceeds max_daily_loss.
        max_loss = canonicalize_decimal(context.config.exposure.max_daily_loss)
        if total < ZERO and abs(total) > max_loss:
            return _hard(
                self.code,
                "daily realized+unrealized loss exceeds maximum",
                evidence=f"daily_pnl={total}; max_loss={max_loss}",
            )
        return _ok(self.code, "daily loss within limit", evidence=f"daily_pnl={total}")


EXPOSURE_RULE_CODES: tuple[str, ...] = (
    MaxOrderNotionalRule.code,
    SymbolWeightRule.code,
    SectorExposureRule.code,
    DailyTurnoverRule.code,
    MinCashReserveRule.code,
    DailyLossRule.code,
)


def build_exposure_rules() -> dict[str, object]:
    rules = (
        MaxOrderNotionalRule(),
        SymbolWeightRule(),
        SectorExposureRule(),
        DailyTurnoverRule(),
        MinCashReserveRule(),
        DailyLossRule(),
    )
    return {rule.code: rule for rule in rules}


__all__ = [
    "EXPOSURE_RULE_CODES",
    "DailyLossRule",
    "DailyTurnoverRule",
    "MaxOrderNotionalRule",
    "MinCashReserveRule",
    "SectorExposureRule",
    "SymbolWeightRule",
    "build_exposure_rules",
]
