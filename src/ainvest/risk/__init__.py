"""Hard risk rules and unconditional veto authority.

Risk may reject candidate orders. It must not import ``ainvest.approval`` or
``ainvest.execution``.
"""

from ainvest.risk.engine import (
    RiskEngineOutput,
    aggregate_rule_results,
    build_default_rules,
    compute_config_digest,
    compute_input_digest,
    evaluate_risk,
    evaluate_rules,
)
from ainvest.risk.models import (
    AllowlistEntry,
    EligibilityLimits,
    EvaluationPhase,
    ExposureInputs,
    ExposureLimits,
    InstrumentMetadata,
    MarketQualityLimits,
    PhaseMarketQualityLimits,
    RiskContext,
    RiskRuleConfig,
    RuleResult,
    SectorAssignment,
)
from ainvest.risk.rules import (
    DEFAULT_EXPOSURE_RULE_CODES,
    DEFAULT_RULE_CODES,
    DEFAULT_SCREENING_RULE_CODES,
    RiskRule,
)

__all__ = [
    "DEFAULT_EXPOSURE_RULE_CODES",
    "DEFAULT_RULE_CODES",
    "DEFAULT_SCREENING_RULE_CODES",
    "AllowlistEntry",
    "EligibilityLimits",
    "EvaluationPhase",
    "ExposureInputs",
    "ExposureLimits",
    "InstrumentMetadata",
    "MarketQualityLimits",
    "PhaseMarketQualityLimits",
    "RiskContext",
    "RiskEngineOutput",
    "RiskRule",
    "RiskRuleConfig",
    "RuleResult",
    "SectorAssignment",
    "aggregate_rule_results",
    "build_default_rules",
    "compute_config_digest",
    "compute_input_digest",
    "evaluate_risk",
    "evaluate_rules",
]
