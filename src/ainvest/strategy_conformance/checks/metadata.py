"""Metadata, hooks, and Strategy API range checks."""

from __future__ import annotations

from ainvest.strategies.api import assert_strategy_api_compatible, parse_strategy_api_range
from ainvest.strategies.definitions import StrategyDefinition, StrategyError
from ainvest.strategy_conformance.checks._util import failed, passed, timed
from ainvest.strategy_conformance.codes import ConformanceCode
from ainvest.strategy_conformance.models import CheckResult


def check_metadata(definition: StrategyDefinition) -> CheckResult:
    """Validate plugin metadata fields and strategy ClassVars."""

    def _run() -> CheckResult:
        try:
            definition.metadata.validate()
        except StrategyError as exc:
            code = (
                ConformanceCode.API_INCOMPATIBLE
                if exc.code == "STRATEGY_API_INCOMPATIBLE"
                else ConformanceCode.METADATA_INVALID
            )
            return failed("metadata", code=code, message=str(exc))
        if definition.name != definition.strategy_type.name:
            return failed(
                "metadata",
                code=ConformanceCode.METADATA_INVALID,
                message="definition.name does not match strategy_type.name",
            )
        if definition.version != definition.strategy_type.version:
            return failed(
                "metadata",
                code=ConformanceCode.METADATA_INVALID,
                message="definition.version does not match strategy_type.version",
            )
        return passed(
            "metadata",
            message="plugin metadata and strategy identity are valid",
            details={
                "plugin_id": definition.metadata.plugin_id,
                "plugin_version": definition.metadata.plugin_version,
            },
        )

    return timed(_run)


def check_api_range(definition: StrategyDefinition) -> CheckResult:
    """Ensure the declared Strategy API range includes the host version."""

    def _run() -> CheckResult:
        raw = definition.metadata.ainvest_strategy_api
        try:
            parse_strategy_api_range(raw)
            assert_strategy_api_compatible(raw)
        except ValueError as exc:
            return failed(
                "api_range",
                code=ConformanceCode.API_INCOMPATIBLE,
                message=str(exc),
                details={"ainvest_strategy_api": raw},
            )
        return passed(
            "api_range",
            message="Strategy API range is compatible with the host",
            details={"ainvest_strategy_api": raw},
        )

    return timed(_run)


def check_hooks(definition: StrategyDefinition) -> CheckResult:
    """Ensure the strategy type exposes the required Protocol surface."""

    def _run() -> CheckResult:
        strategy_type = definition.strategy_type
        evaluate = getattr(strategy_type, "evaluate", None)
        if not callable(evaluate):
            return failed(
                "hooks",
                code=ConformanceCode.HOOK_INVALID,
                message="strategy type must define evaluate(context)",
            )
        params_model = getattr(strategy_type, "params_model", None)
        if params_model is not definition.params_model:
            return failed(
                "hooks",
                code=ConformanceCode.HOOK_INVALID,
                message="strategy params_model must match definition.params_model",
            )
        # Instantiation must not evaluate; create() validates params only.
        try:
            instance = definition.create({})
        except StrategyError as exc:
            return failed(
                "hooks",
                code=ConformanceCode.HOOK_INVALID,
                message=f"strategy cannot be instantiated with defaults: {exc}",
            )
        if not callable(getattr(instance, "evaluate", None)):
            return failed(
                "hooks",
                code=ConformanceCode.HOOK_INVALID,
                message="strategy instance missing evaluate()",
            )
        return passed("hooks", message="strategy Protocol surface is present")

    return timed(_run)


__all__ = ["check_api_range", "check_hooks", "check_metadata"]
