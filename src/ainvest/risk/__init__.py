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
from ainvest.risk.kill_switch import (
    KillSwitch,
    KillSwitchAlert,
    KillSwitchAlertKind,
)
from ainvest.risk.models import (
    AllowlistEntry,
    EligibilityLimits,
    EvaluationPhase,
    ExposureInputs,
    ExposureLimits,
    InstrumentMetadata,
    KillSwitchSnapshot,
    MarketQualityLimits,
    OrderConflictLimits,
    PhaseMarketQualityLimits,
    RecentOrderSubmission,
    RiskContext,
    RiskRuleConfig,
    RuleResult,
    SectorAssignment,
)
from ainvest.risk.pretrade import (
    ClientOrderId,
    PretradeMarketData,
    PretradeRequest,
    evaluate_pretrade,
)
from ainvest.risk.rules import (
    DEFAULT_EXPOSURE_RULE_CODES,
    DEFAULT_ORDER_RULE_CODES,
    DEFAULT_RULE_CODES,
    DEFAULT_SCREENING_RULE_CODES,
    PRETRADE_RULE_CODES,
    RiskRule,
)

__all__ = [
    "DEFAULT_EXPOSURE_RULE_CODES",
    "DEFAULT_ORDER_RULE_CODES",
    "DEFAULT_RULE_CODES",
    "DEFAULT_SCREENING_RULE_CODES",
    "PRETRADE_RULE_CODES",
    "AllowlistEntry",
    "ClientOrderId",
    "EligibilityLimits",
    "EvaluationPhase",
    "ExposureInputs",
    "ExposureLimits",
    "InstrumentMetadata",
    "KillSwitch",
    "KillSwitchAlert",
    "KillSwitchAlertKind",
    "KillSwitchSnapshot",
    "MarketQualityLimits",
    "OrderConflictLimits",
    "PhaseMarketQualityLimits",
    "PretradeMarketData",
    "PretradeRequest",
    "RecentOrderSubmission",
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
    "evaluate_pretrade",
    "evaluate_risk",
    "evaluate_rules",
]
