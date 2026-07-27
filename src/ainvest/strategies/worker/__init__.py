"""Isolated strategy worker processes (P03-T4).

The host exchanges versioned JSON only with child workers. Credentials, ORM
objects, and broker sockets never cross the boundary. See
``ainvest.strategies.worker.isolation`` for OS/container network isolation
expectations.
"""

from ainvest.strategies.worker.codes import WorkerFailureCode, WorkerStatus
from ainvest.strategies.worker.digests import digest_json, sha256_digest
from ainvest.strategies.worker.env import (
    SecretEnvironmentAccessError,
    is_sensitive_env_key,
    scrub_environ,
)
from ainvest.strategies.worker.protocol import (
    StrategyRef,
    WorkerLimits,
    WorkerRequest,
    WorkerResponse,
    WorkerRunRecord,
)
from ainvest.strategies.worker.runner import (
    WorkerRunSpec,
    build_request,
    evaluate_in_worker,
    evaluate_many_in_workers,
    package_version,
    run_worker,
    strategy_ref_from_definition,
)

__all__ = [
    "SecretEnvironmentAccessError",
    "StrategyRef",
    "WorkerFailureCode",
    "WorkerLimits",
    "WorkerRequest",
    "WorkerResponse",
    "WorkerRunRecord",
    "WorkerRunSpec",
    "WorkerStatus",
    "build_request",
    "digest_json",
    "evaluate_in_worker",
    "evaluate_many_in_workers",
    "is_sensitive_env_key",
    "package_version",
    "run_worker",
    "scrub_environ",
    "sha256_digest",
    "strategy_ref_from_definition",
]
