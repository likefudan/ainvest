"""Risk rule protocol and default rule-code catalogs (P03-T8).

Concrete rules live in submodules. The engine instantiates them via
:func:`~ainvest.risk.engine.build_default_rules` — there is no separate
runtime registry.
"""

from __future__ import annotations

from typing import Final, Protocol, runtime_checkable

from ainvest.risk.models import RiskContext, RuleResult

RuleCodeName = str


@runtime_checkable
class RiskRule(Protocol):
    """Pure rule: immutable context in, explainable result out."""

    @property
    def code(self) -> RuleCodeName:
        """Stable machine-readable rule identifier."""
        ...

    def evaluate(self, context: RiskContext) -> RuleResult:
        """Return one rule result; must not mutate ``context``."""
        ...


DEFAULT_SCREENING_RULE_CODES: Final[tuple[str, ...]] = (
    "ELIGIBILITY_ASSET_CLASS",
    "ELIGIBILITY_ALLOWLIST",
    "ELIGIBILITY_IDENTITY",
    "ELIGIBILITY_SIDE_AND_PRODUCT",
    "ELIGIBILITY_SESSION",
    "MARKET_QUALITY_QUOTE",
    "MARKET_QUALITY_SPREAD",
    "MARKET_QUALITY_VOLATILITY",
    "MARKET_QUALITY_LIMIT_DEVIATION",
)

DEFAULT_EXPOSURE_RULE_CODES: Final[tuple[str, ...]] = (
    "EXPOSURE_MAX_ORDER_NOTIONAL",
    "EXPOSURE_MAX_SYMBOL_WEIGHT",
    "EXPOSURE_MAX_SECTOR_WEIGHT",
    "EXPOSURE_MAX_DAILY_TURNOVER",
    "EXPOSURE_MIN_CASH_RESERVE",
    "EXPOSURE_MAX_DAILY_LOSS",
)

DEFAULT_ORDER_RULE_CODES: Final[tuple[str, ...]] = (
    "ORDERS_KILL_SWITCH",
    "ORDERS_DUPLICATE_PROPOSAL_HASH",
    "ORDERS_DUPLICATE_CLIENT_ORDER_ID",
    "ORDERS_DUPLICATE_SYMBOL_SIDE_WINDOW",
    "ORDERS_OPEN_ORDER_CONFLICT",
)

DEFAULT_RULE_CODES: Final[tuple[str, ...]] = (
    DEFAULT_SCREENING_RULE_CODES + DEFAULT_EXPOSURE_RULE_CODES
)

PRETRADE_RULE_CODES: Final[tuple[str, ...]] = DEFAULT_RULE_CODES + DEFAULT_ORDER_RULE_CODES


__all__ = [
    "DEFAULT_EXPOSURE_RULE_CODES",
    "DEFAULT_ORDER_RULE_CODES",
    "DEFAULT_RULE_CODES",
    "DEFAULT_SCREENING_RULE_CODES",
    "PRETRADE_RULE_CODES",
    "RiskRule",
]
