"""Parameter and TradeSignal schema checks."""

from __future__ import annotations

from pydantic import ValidationError

from ainvest.strategies.definitions import StrategyDefinition, StrategyError, StrategyResult
from ainvest.strategy_conformance.checks._util import (
    failed,
    passed,
    require_worker_success,
    run_in_worker,
    timed,
)
from ainvest.strategy_conformance.codes import ConformanceCode
from ainvest.strategy_conformance.fixtures import make_paper_context
from ainvest.strategy_conformance.models import CheckResult


def check_parameters(definition: StrategyDefinition) -> CheckResult:
    """Validate params_model forbids extras and accepts empty/default params."""

    def _run() -> CheckResult:
        try:
            definition.validate_params({})
        except StrategyError as exc:
            return failed(
                "parameters",
                code=ConformanceCode.PARAMS_INVALID,
                message=f"default/empty params rejected: {exc}",
            )
        try:
            definition.validate_params({"__conformance_unknown__": True})
        except StrategyError:
            return passed(
                "parameters",
                message="params_model accepts defaults and rejects unknown keys",
            )
        return failed(
            "parameters",
            code=ConformanceCode.PARAMS_INVALID,
            message="params_model must reject unknown keys (extra='forbid')",
        )

    return timed(_run)


def check_signal_schemas(definition: StrategyDefinition) -> CheckResult:
    """Evaluate once in a worker and ensure StrategyResult / signals validate."""

    def _run() -> CheckResult:
        context = make_paper_context(
            strategy_name=definition.name,
            strategy_version=definition.version,
        )
        record = run_in_worker(
            definition,
            params={},
            context=context,
            run_id="conformance-signals",
        )
        early = require_worker_success(record, check_id="signal_schemas")
        if early is not None:
            return early
        try:
            result = StrategyResult.model_validate(record.result.model_dump(mode="python"))
        except ValidationError as exc:
            return failed(
                "signal_schemas",
                code=ConformanceCode.SIGNAL_INVALID,
                message=f"StrategyResult failed validation: {exc.error_count()} error(s)",
            )
        for index, signal in enumerate(result.signals):
            if signal.strategy != definition.name:
                return failed(
                    "signal_schemas",
                    code=ConformanceCode.SIGNAL_INVALID,
                    message=f"signal[{index}].strategy must equal strategy name",
                )
            if signal.strategy_version != definition.version:
                return failed(
                    "signal_schemas",
                    code=ConformanceCode.SIGNAL_INVALID,
                    message=f"signal[{index}].strategy_version must equal strategy version",
                )
            if signal.generated_at != context.as_of:
                return failed(
                    "signal_schemas",
                    code=ConformanceCode.SIGNAL_INVALID,
                    message=(
                        f"signal[{index}].generated_at must equal context.as_of "
                        "(no wall-clock timestamps)"
                    ),
                )
        return passed(
            "signal_schemas",
            message=f"emitted {len(result.signals)} valid TradeSignal(s)",
            details={"signal_count": str(len(result.signals))},
        )

    return timed(_run)


__all__ = ["check_parameters", "check_signal_schemas"]
