"""Hard risk rules and unconditional veto authority.

Risk may reject candidate orders. It must not import ``ainvest.approval`` or
``ainvest.execution``.
"""

from ainvest.risk.engine import (
    RiskEngineOutput,
    aggregate_rule_results,
    compute_config_digest,
    compute_input_digest,
    evaluate_risk,
    evaluate_rules,
)
from ainvest.risk.models import (
    AllowlistEntry,
    EligibilityLimits,
    EvaluationPhase,
    InstrumentMetadata,
    MarketQualityLimits,
    PhaseMarketQualityLimits,
    RiskContext,
    RiskRuleConfig,
    RuleResult,
)
from ainvest.risk.rules import (
    DEFAULT_C4A_RULE_CODES,
    RiskRule,
    clear_registry,
    register_rule,
    resolve_rules,
)

__all__ = [
    "DEFAULT_C4A_RULE_CODES",
    "AllowlistEntry",
    "EligibilityLimits",
    "EvaluationPhase",
    "InstrumentMetadata",
    "MarketQualityLimits",
    "PhaseMarketQualityLimits",
    "RiskContext",
    "RiskEngineOutput",
    "RiskRule",
    "RiskRuleConfig",
    "RuleResult",
    "aggregate_rule_results",
    "clear_registry",
    "compute_config_digest",
    "compute_input_digest",
    "evaluate_risk",
    "evaluate_rules",
    "register_rule",
    "resolve_rules",
]
