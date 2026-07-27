"""Parent-side strategy worker runner (subprocess isolation)."""

from __future__ import annotations

import contextlib
import importlib.metadata
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from ainvest.schemas.strategy import StrategyContext
from ainvest.strategies.config import format_duration
from ainvest.strategies.definitions import StrategyDefinition
from ainvest.strategies.worker.codes import WorkerFailureCode, WorkerStatus
from ainvest.strategies.worker.digests import digest_json
from ainvest.strategies.worker.env import scrub_environ
from ainvest.strategies.worker.isolation import (
    cleanup_worker_workdir,
    create_worker_workdir,
    inject_limit_environ,
)
from ainvest.strategies.worker.protocol import (
    StrategyRef,
    WorkerLimits,
    WorkerRequest,
    WorkerResponse,
    WorkerRunRecord,
)

_SIGKILL_CODES = frozenset({-signal.SIGKILL, 128 + signal.SIGKILL, 137})
_SIGXCPU_CODES = frozenset({-getattr(signal, "SIGXCPU", 24), 128 + getattr(signal, "SIGXCPU", 24)})
_REAP_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class WorkerRunSpec:
    """One strategy evaluation to execute in an isolated worker."""

    definition: StrategyDefinition
    params: Mapping[str, Any] | BaseModel
    context: StrategyContext
    run_id: str | None = None
    limits: WorkerLimits | None = None


def package_version() -> str:
    """Return the installed ``ainvest`` distribution version."""
    try:
        return importlib.metadata.version("ainvest")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+local"


def _jsonable_params(value: Any) -> Any:
    """Convert validated params into JSON-safe values that round-trip through Pydantic."""
    if isinstance(value, BaseModel):
        return _jsonable_params(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {str(key): _jsonable_params(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable_params(item) for item in value]
    if isinstance(value, timedelta):
        return format_duration(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def strategy_ref_from_definition(
    definition: StrategyDefinition,
    params: Mapping[str, Any] | BaseModel,
) -> StrategyRef:
    """Build a JSON-safe strategy reference from a registry definition."""
    validated = definition.validate_params(params)
    params_payload = _jsonable_params(validated)
    if not isinstance(params_payload, dict):
        raise TypeError("strategy params must serialize to a JSON object")
    strategy_type = definition.strategy_type
    return StrategyRef(
        module=strategy_type.__module__,
        qualname=strategy_type.__qualname__,
        name=definition.name,
        version=definition.version,
        plugin_id=definition.metadata.plugin_id,
        plugin_version=definition.metadata.plugin_version,
        ainvest_strategy_api=definition.metadata.ainvest_strategy_api,
        source_commit=definition.metadata.source_commit,
        params=params_payload,
    )


def build_request(
    *,
    definition: StrategyDefinition,
    params: Mapping[str, Any] | BaseModel,
    context: StrategyContext,
    limits: WorkerLimits | None = None,
    run_id: str | None = None,
    version: str | None = None,
) -> WorkerRequest:
    """Construct a versioned worker request with parameter/input digests."""
    ref = strategy_ref_from_definition(definition, params)
    context_payload = context.model_dump(mode="json")
    return WorkerRequest(
        run_id=run_id or f"run_{uuid.uuid4().hex}",
        package_version=version or package_version(),
        strategy=ref,
        context=context,
        limits=limits or WorkerLimits(),
        parameter_digest=digest_json(ref.params),
        input_digest=digest_json(context_payload),
    )


def _signal_number(returncode: int) -> int | None:
    """Return the signal number for a negative or 128+N exit status."""
    if returncode < 0:
        return -returncode
    if returncode >= 128:
        return returncode - 128
    return None


def _classify_exit(
    *,
    returncode: int | None,
    timed_out: bool,
    memory_limit: int | None,
) -> tuple[WorkerFailureCode, str] | None:
    """Map subprocess termination to a failure code when JSON is unavailable."""
    if timed_out:
        return WorkerFailureCode.TIMEOUT, "strategy worker wall-clock timeout exceeded"
    if returncode is None:
        return WorkerFailureCode.CRASH, "strategy worker terminated without an exit code"
    if returncode in _SIGKILL_CODES:
        if memory_limit is not None:
            return WorkerFailureCode.OOM, "strategy worker killed after exceeding memory limit"
        return WorkerFailureCode.CRASH, "strategy worker killed by SIGKILL"
    if returncode in _SIGXCPU_CODES:
        return WorkerFailureCode.TIMEOUT, "strategy worker exceeded CPU time limit"
    sig = _signal_number(returncode)
    if sig is not None:
        return (
            WorkerFailureCode.CRASH,
            f"strategy worker terminated by signal {sig}",
        )
    return None


def _record_from_response(
    response: WorkerResponse,
    *,
    plugin_id: str,
    exit_code: int | None,
    duration_ms: float,
) -> WorkerRunRecord:
    if response.status is WorkerStatus.SUCCESS and response.success is not None:
        return WorkerRunRecord(
            run_id=response.run_id,
            status=WorkerStatus.SUCCESS,
            result=response.success.result,
            package_version=response.package_version,
            strategy_name=response.strategy_name,
            strategy_version=response.strategy_version,
            plugin_id=plugin_id,
            plugin_version=response.plugin_version,
            source_commit=response.source_commit,
            parameter_digest=response.parameter_digest,
            input_digest=response.input_digest,
            duration_ms=duration_ms,
            exit_code=exit_code,
            reason_codes=response.success.result.diagnostics.reason_codes,
        )
    failure = response.failure
    code = failure.code if failure is not None else WorkerFailureCode.INVALID_OUTPUT
    message = failure.message if failure is not None else "worker returned FAILED without payload"
    return WorkerRunRecord(
        run_id=response.run_id,
        status=WorkerStatus.FAILED,
        failure_code=code,
        failure_message=message,
        reason_codes=(code.value,),
        package_version=response.package_version,
        strategy_name=response.strategy_name,
        strategy_version=response.strategy_version,
        plugin_id=plugin_id,
        plugin_version=response.plugin_version,
        source_commit=response.source_commit,
        parameter_digest=response.parameter_digest,
        input_digest=response.input_digest,
        duration_ms=duration_ms,
        exit_code=exit_code,
    )


def _failed_record(
    request: WorkerRequest,
    *,
    plugin_id: str,
    code: WorkerFailureCode,
    message: str,
    duration_ms: float,
    exit_code: int | None,
) -> WorkerRunRecord:
    return WorkerRunRecord(
        run_id=request.run_id,
        status=WorkerStatus.FAILED,
        failure_code=code,
        failure_message=message,
        reason_codes=(code.value,),
        package_version=request.package_version,
        strategy_name=request.strategy.name,
        strategy_version=request.strategy.version,
        plugin_id=plugin_id,
        plugin_version=request.strategy.plugin_version,
        source_commit=request.strategy.source_commit,
        parameter_digest=request.parameter_digest,
        input_digest=request.input_digest,
        duration_ms=duration_ms,
        exit_code=exit_code,
    )


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    """Terminate the worker and its process group after a wall-clock timeout."""
    try:
        if proc.pid is not None:
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(OSError):
            proc.kill()


def _reap_after_timeout(
    proc: subprocess.Popen[str],
) -> tuple[str, str, int | None]:
    """Reap a timed-out worker without raising or orphaning the process group.

    Nested ``TimeoutExpired`` from ``communicate`` or ``wait`` is caught, the
    process group is killed again, and we always return so the caller can emit a
    fail-closed ``TIMEOUT``/``CRASH`` record — this function never raises.
    """
    stdout = ""
    stderr = ""
    try:
        stdout, stderr = proc.communicate(timeout=_REAP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            stdout, stderr = proc.communicate(timeout=_REAP_TIMEOUT_SECONDS)
        if proc.poll() is None:
            _kill_process_group(proc)
            # ``wait`` raises TimeoutExpired (a SubprocessError), not OSError.
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                proc.wait(timeout=_REAP_TIMEOUT_SECONDS)
    return stdout, stderr, proc.returncode


def run_worker(
    request: WorkerRequest,
    *,
    plugin_id: str | None = None,
    python_executable: str | None = None,
) -> WorkerRunRecord:
    """Spawn one isolated worker child and classify the outcome fail-closed."""
    started = time.perf_counter()
    resolved_plugin_id = plugin_id or request.strategy.plugin_id
    workdir = create_worker_workdir()
    env = scrub_environ()
    inject_limit_environ(env, request.limits, workdir=workdir)
    env.setdefault("PYTHONPATH", os.environ.get("PYTHONPATH", ""))

    payload = request.model_dump_json()
    cmd = [
        python_executable or sys.executable,
        "-m",
        "ainvest.strategies.worker",
    ]
    timed_out = False
    stdout = ""
    returncode: int | None = None
    try:
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            return _failed_record(
                request,
                plugin_id=resolved_plugin_id,
                code=WorkerFailureCode.INTERNAL_ERROR,
                message=f"failed to spawn strategy worker: {exc}"[:512],
                duration_ms=duration_ms,
                exit_code=None,
            )

        try:
            stdout, _stderr = proc.communicate(
                input=payload,
                timeout=request.limits.wall_timeout_seconds,
            )
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(proc)
            stdout, _stderr, returncode = _reap_after_timeout(proc)
    finally:
        cleanup_worker_workdir(workdir)

    duration_ms = (time.perf_counter() - started) * 1000.0

    signal_class = _classify_exit(
        returncode=returncode,
        timed_out=timed_out,
        memory_limit=request.limits.memory_limit_bytes,
    )
    if signal_class is not None:
        code, message = signal_class
        return _failed_record(
            request,
            plugin_id=resolved_plugin_id,
            code=code,
            message=message,
            duration_ms=duration_ms,
            exit_code=returncode,
        )

    text = (stdout or "").strip()
    if not text:
        return _failed_record(
            request,
            plugin_id=resolved_plugin_id,
            code=WorkerFailureCode.INVALID_OUTPUT,
            message="strategy worker produced empty stdout",
            duration_ms=duration_ms,
            exit_code=returncode,
        )

    line = text.splitlines()[-1]
    try:
        data = json.loads(line)
        response = WorkerResponse.model_validate(data)
    except Exception as exc:
        return _failed_record(
            request,
            plugin_id=resolved_plugin_id,
            code=WorkerFailureCode.INVALID_OUTPUT,
            message=f"strategy worker returned invalid JSON output: {exc}"[:512],
            duration_ms=duration_ms,
            exit_code=returncode,
        )

    if response.run_id != request.run_id:
        return _failed_record(
            request,
            plugin_id=resolved_plugin_id,
            code=WorkerFailureCode.INVALID_OUTPUT,
            message="strategy worker run_id mismatch",
            duration_ms=duration_ms,
            exit_code=returncode,
        )

    return _record_from_response(
        response,
        plugin_id=resolved_plugin_id,
        exit_code=returncode,
        duration_ms=duration_ms,
    )


def evaluate_in_worker(
    definition: StrategyDefinition,
    *,
    params: Mapping[str, Any] | BaseModel | None = None,
    context: StrategyContext,
    limits: WorkerLimits | None = None,
    run_id: str | None = None,
) -> WorkerRunRecord:
    """Validate params, build a JSON request, and evaluate in a worker process."""
    request = build_request(
        definition=definition,
        params={} if params is None else params,
        context=context,
        limits=limits,
        run_id=run_id,
    )
    return run_worker(request, plugin_id=definition.metadata.plugin_id)


def evaluate_many_in_workers(
    specs: Sequence[WorkerRunSpec],
) -> list[WorkerRunRecord]:
    """Evaluate many strategies; a failure affects only that strategy run."""
    records: list[WorkerRunRecord] = []
    for spec in specs:
        try:
            record = evaluate_in_worker(
                spec.definition,
                params=spec.params,
                context=spec.context,
                limits=spec.limits,
                run_id=spec.run_id,
            )
        except Exception as exc:
            request = build_request(
                definition=spec.definition,
                params=spec.params,
                context=spec.context,
                limits=spec.limits,
                run_id=spec.run_id,
            )
            record = _failed_record(
                request,
                plugin_id=spec.definition.metadata.plugin_id,
                code=WorkerFailureCode.INTERNAL_ERROR,
                message=f"host-side worker orchestration error: {exc}"[:512],
                duration_ms=0.0,
                exit_code=None,
            )
        records.append(record)
    return records


__all__ = [
    "WorkerRunSpec",
    "build_request",
    "evaluate_in_worker",
    "evaluate_many_in_workers",
    "package_version",
    "run_worker",
    "strategy_ref_from_definition",
]
