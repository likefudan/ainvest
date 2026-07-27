"""Network, broker-import, and secret-access isolation checks."""

from __future__ import annotations

from ainvest.strategies.definitions import StrategyDefinition
from ainvest.strategies.worker import WorkerFailureCode, WorkerStatus
from ainvest.strategy_conformance.checks._util import (
    FORBIDDEN_BROKER_MODULES,
    FORBIDDEN_NETWORK_MODULES,
    failed,
    module_imports_forbidden,
    passed,
    require_worker_success,
    run_in_worker,
    strategy_source_path,
    timed,
)
from ainvest.strategy_conformance.codes import ConformanceCode
from ainvest.strategy_conformance.fixtures import make_paper_context
from ainvest.strategy_conformance.models import CheckResult


def check_broker_imports(definition: StrategyDefinition) -> CheckResult:
    """Strategy source must not import broker / execution / approval packages."""

    def _run() -> CheckResult:
        path = strategy_source_path(definition)
        if path is None:
            return failed(
                "broker_imports",
                code=ConformanceCode.BROKER_IMPORT,
                message="unable to locate strategy source for broker import scan",
            )
        offenders = module_imports_forbidden(path, FORBIDDEN_BROKER_MODULES)
        if offenders:
            return failed(
                "broker_imports",
                code=ConformanceCode.BROKER_IMPORT,
                message="strategy imports forbidden broker/execution modules",
                details={"imports": ",".join(offenders)},
            )
        return passed(
            "broker_imports",
            message="no broker/execution/approval imports in strategy source",
            details={"source": path},
        )

    return timed(_run)


def check_network_isolation(definition: StrategyDefinition) -> CheckResult:
    """Static network imports forbidden; runtime must succeed with sockets blocked."""

    def _run() -> CheckResult:
        path = strategy_source_path(definition)
        if path is not None:
            offenders = module_imports_forbidden(path, FORBIDDEN_NETWORK_MODULES)
            if offenders:
                return failed(
                    "network",
                    code=ConformanceCode.NETWORK_ACCESS,
                    message="strategy imports network client modules",
                    details={"imports": ",".join(offenders)},
                )
        context = make_paper_context(
            strategy_name=definition.name,
            strategy_version=definition.version,
        )
        record = run_in_worker(
            definition,
            params={},
            context=context,
            run_id="conformance-network",
        )
        if (
            record.status is WorkerStatus.FAILED
            and record.failure_code is WorkerFailureCode.NETWORK_DENIED
        ):
            return failed(
                "network",
                code=ConformanceCode.NETWORK_ACCESS,
                message=record.failure_message or "strategy attempted network access",
                details={"worker_code": str(record.failure_code)},
            )
        early = require_worker_success(record, check_id="network")
        if early is not None:
            return failed(
                "network",
                code=ConformanceCode.NETWORK_ACCESS,
                message=early.message,
                details=early.details,
            )
        return passed(
            "network",
            message="strategy evaluates successfully with worker network blocked",
        )

    return timed(_run)


def check_secret_access(definition: StrategyDefinition) -> CheckResult:
    """Strategy must not require scrubbed credential environment variables."""

    def _run() -> CheckResult:
        context = make_paper_context(
            strategy_name=definition.name,
            strategy_version=definition.version,
        )
        record = run_in_worker(
            definition,
            params={},
            context=context,
            run_id="conformance-secrets",
        )
        if (
            record.status is WorkerStatus.FAILED
            and record.failure_code is WorkerFailureCode.SECRET_ACCESS
        ):
            return failed(
                "secret_access",
                code=ConformanceCode.SECRET_ACCESS,
                message=record.failure_message or "strategy attempted secret environment access",
                details={"worker_code": str(record.failure_code)},
            )
        early = require_worker_success(record, check_id="secret_access")
        if early is not None:
            return failed(
                "secret_access",
                code=ConformanceCode.SECRET_ACCESS,
                message=early.message,
                details=early.details,
            )
        return passed(
            "secret_access",
            message="strategy evaluates successfully without credential environment access",
        )

    return timed(_run)


__all__ = ["check_broker_imports", "check_network_isolation", "check_secret_access"]
