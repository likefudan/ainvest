"""Child-process evaluation entry for isolated strategy workers."""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
import traceback
from typing import Any, TextIO

from ainvest.strategies.definitions import PluginMetadata, StrategyDefinition, StrategyResult
from ainvest.strategies.worker.codes import WorkerFailureCode, WorkerStatus
from ainvest.strategies.worker.env import (
    SecretEnvironmentAccessError,
    assert_no_secrets_in_environ,
    scrub_environ,
)
from ainvest.strategies.worker.isolation import (
    NetworkAccessDeniedError,
    apply_isolation,
)
from ainvest.strategies.worker.protocol import (
    WorkerFailurePayload,
    WorkerRequest,
    WorkerResponse,
    WorkerSuccessPayload,
)


def _load_strategy_type(module_name: str, qualname: str) -> type[Any]:
    module = importlib.import_module(module_name)
    obj: Any = module
    for part in qualname.split("."):
        obj = getattr(obj, part)
    if not isinstance(obj, type):
        raise TypeError(f"strategy {module_name}:{qualname} is not a class")
    return obj


def _classify_exception(exc: BaseException) -> tuple[WorkerFailureCode, str]:
    if isinstance(exc, SecretEnvironmentAccessError):
        return WorkerFailureCode.SECRET_ACCESS, str(exc)[:512]
    if isinstance(exc, NetworkAccessDeniedError):
        return WorkerFailureCode.NETWORK_DENIED, str(exc)[:512]
    if isinstance(exc, MemoryError):
        return WorkerFailureCode.OOM, "strategy worker memory limit exceeded"
    if isinstance(exc, RuntimeError) and str(exc).startswith("sensitive environment keys present"):
        return WorkerFailureCode.SECRET_ACCESS, str(exc)[:512]
    message = str(exc).strip() or exc.__class__.__name__
    return WorkerFailureCode.EVALUATION_ERROR, message[:512]


def evaluate_request(request: WorkerRequest) -> WorkerResponse:
    """Evaluate one WorkerRequest inside the already-isolated child process."""
    started = time.perf_counter()
    strategy_ref = request.strategy
    try:
        assert_no_secrets_in_environ()
        strategy_type = _load_strategy_type(strategy_ref.module, strategy_ref.qualname)
        metadata = PluginMetadata(
            plugin_id=strategy_ref.plugin_id,
            plugin_version=strategy_ref.plugin_version,
            ainvest_strategy_api=strategy_ref.ainvest_strategy_api,
            source_commit=strategy_ref.source_commit,
            owner="worker",
            repository="local/worker",
        )
        definition = StrategyDefinition.from_type(strategy_type, metadata=metadata)
        if definition.name != strategy_ref.name or definition.version != strategy_ref.version:
            raise ValueError("strategy identity mismatch between request and imported class")
        instance = definition.create(strategy_ref.params)
        raw_result = instance.evaluate(request.context)
        if isinstance(raw_result, StrategyResult):
            result = raw_result
        else:
            result = StrategyResult.model_validate(raw_result)
        duration_ms = (time.perf_counter() - started) * 1000.0
        return WorkerResponse(
            run_id=request.run_id,
            status=WorkerStatus.SUCCESS,
            package_version=request.package_version,
            strategy_name=strategy_ref.name,
            strategy_version=strategy_ref.version,
            plugin_version=strategy_ref.plugin_version,
            source_commit=strategy_ref.source_commit,
            parameter_digest=request.parameter_digest,
            input_digest=request.input_digest,
            success=WorkerSuccessPayload(result=result, duration_ms=duration_ms),
        )
    except Exception as exc:
        code, message = _classify_exception(exc)
        duration_ms = (time.perf_counter() - started) * 1000.0
        return WorkerResponse(
            run_id=request.run_id,
            status=WorkerStatus.FAILED,
            package_version=request.package_version,
            strategy_name=strategy_ref.name,
            strategy_version=strategy_ref.version,
            plugin_version=strategy_ref.plugin_version,
            source_commit=strategy_ref.source_commit,
            parameter_digest=request.parameter_digest,
            input_digest=request.input_digest,
            failure=WorkerFailurePayload(code=code, message=message, duration_ms=duration_ms),
        )


def _read_request(stdin: TextIO) -> WorkerRequest:
    raw = stdin.read()
    if not raw.strip():
        raise ValueError("empty worker stdin")
    payload = json.loads(raw)
    return WorkerRequest.model_validate(payload)


def _write_response(response: WorkerResponse, stdout: TextIO) -> None:
    stdout.write(response.model_dump_json())
    stdout.write("\n")
    stdout.flush()


def os_environ_clear_and_update(cleaned: dict[str, str]) -> None:
    """Replace ``os.environ`` contents with the scrubbed mapping."""
    os.environ.clear()
    os.environ.update(cleaned)


def _emit_bootstrap_failure(exc: BaseException) -> None:
    """Best-effort JSON failure when the request itself cannot be parsed."""
    payload = {
        "schema_version": "1.0",
        "run_id": "worker_bootstrap_failure",
        "status": WorkerStatus.FAILED.value,
        "package_version": "unknown",
        "strategy_name": "unknown_strategy",
        "strategy_version": "0.0.0",
        "plugin_version": "0.0.0",
        "source_commit": "unknown",
        "parameter_digest": "sha256:" + ("0" * 64),
        "input_digest": "sha256:" + ("0" * 64),
        "failure": {
            "schema_version": "1.0",
            "code": WorkerFailureCode.INVALID_INPUT.value,
            "message": f"invalid worker request: {exc}"[:512],
            "duration_ms": 0.0,
        },
    }
    try:
        sys.stdout.write(json.dumps(payload, separators=(",", ":")))
        sys.stdout.write("\n")
        sys.stdout.flush()
    except Exception:
        sys.stderr.write(traceback.format_exc())


def main(argv: list[str] | None = None) -> int:
    """Child entrypoint: scrub env, apply isolation, evaluate one JSON request."""
    del argv
    cleaned = scrub_environ()
    os_environ_clear_and_update(cleaned)

    try:
        request = _read_request(sys.stdin)
    except Exception as exc:
        _emit_bootstrap_failure(exc)
        return 2

    try:
        apply_isolation(request.limits)
    except Exception as exc:
        response = WorkerResponse(
            run_id=request.run_id,
            status=WorkerStatus.FAILED,
            package_version=request.package_version,
            strategy_name=request.strategy.name,
            strategy_version=request.strategy.version,
            plugin_version=request.strategy.plugin_version,
            source_commit=request.strategy.source_commit,
            parameter_digest=request.parameter_digest,
            input_digest=request.input_digest,
            failure=WorkerFailurePayload(
                code=WorkerFailureCode.INTERNAL_ERROR,
                message=f"failed to apply isolation: {exc}"[:512],
                duration_ms=0.0,
            ),
        )
        _write_response(response, sys.stdout)
        return 3

    response = evaluate_request(request)
    _write_response(response, sys.stdout)
    return 0 if response.status is WorkerStatus.SUCCESS else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["evaluate_request", "main"]
