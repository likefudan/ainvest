"""Versioned JSON protocol exchanged with strategy worker child processes.

The host and child exchange only JSON-serializable payloads. ORM objects,
sockets, credentials, and live broker handles must never cross this boundary.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from ainvest.schemas.common import SCHEMA_VERSION_V1, DomainModel, MachineCode, SchemaVersion
from ainvest.schemas.strategy import StrategyContext
from ainvest.strategies.definitions import StrategyResult
from ainvest.strategies.worker.codes import WorkerFailureCode, WorkerStatus


class WorkerLimits(DomainModel):
    """Resource boundaries applied to one strategy worker process."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    wall_timeout_seconds: float = Field(default=5.0, gt=0, le=600)
    cpu_seconds: float | None = Field(default=5.0, gt=0, le=600)
    memory_limit_bytes: int | None = Field(default=256 * 1024 * 1024, gt=0)
    block_network: bool = True
    read_only_workdir: bool = True


class StrategyRef(DomainModel):
    """Import path and identity for a strategy class (no live Python objects)."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    module: str = Field(min_length=1, max_length=256)
    qualname: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=2, max_length=64)
    version: str = Field(min_length=5, max_length=32)
    plugin_id: str = Field(min_length=2, max_length=64)
    plugin_version: str = Field(min_length=5, max_length=32)
    ainvest_strategy_api: str = Field(min_length=1, max_length=64)
    source_commit: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("module", "qualname")
    @classmethod
    def _no_dunder_import(cls, value: str) -> str:
        text = value.strip()
        if not text or ".." in text:
            raise ValueError("invalid strategy import path")
        return text


class WorkerRequest(DomainModel):
    """Parent -> child request. Context and params are versioned JSON only."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    run_id: str = Field(min_length=8, max_length=128)
    package_version: str = Field(min_length=1, max_length=64)
    strategy: StrategyRef
    context: StrategyContext
    limits: WorkerLimits = Field(default_factory=WorkerLimits)
    parameter_digest: str = Field(min_length=16, max_length=80)
    input_digest: str = Field(min_length=16, max_length=80)


class WorkerSuccessPayload(DomainModel):
    """Successful evaluation body returned by the child."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    result: StrategyResult
    duration_ms: float = Field(ge=0)


class WorkerFailurePayload(DomainModel):
    """Structured failure body returned by the child when it can still speak JSON."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    code: WorkerFailureCode
    message: str = Field(min_length=1, max_length=512)
    duration_ms: float = Field(ge=0)


class WorkerResponse(DomainModel):
    """Child -> parent response envelope."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    run_id: str = Field(min_length=8, max_length=128)
    status: WorkerStatus
    package_version: str = Field(min_length=1, max_length=64)
    strategy_name: str = Field(min_length=2, max_length=64)
    strategy_version: str = Field(min_length=5, max_length=32)
    plugin_version: str = Field(min_length=5, max_length=32)
    source_commit: str = Field(min_length=1, max_length=64)
    parameter_digest: str = Field(min_length=16, max_length=80)
    input_digest: str = Field(min_length=16, max_length=80)
    success: WorkerSuccessPayload | None = None
    failure: WorkerFailurePayload | None = None

    @field_validator("parameter_digest", "input_digest")
    @classmethod
    def _digest_prefix(cls, value: str) -> str:
        if not value.startswith("sha256:"):
            raise ValueError("digests must use sha256:<hex> form")
        return value


class WorkerRunRecord(DomainModel):
    """Host-side record for one strategy run (success or fail-closed failure)."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    run_id: str
    status: WorkerStatus
    failure_code: WorkerFailureCode | None = None
    failure_message: str | None = None
    reason_codes: tuple[MachineCode, ...] = ()
    result: StrategyResult | None = None
    package_version: str
    strategy_name: str
    strategy_version: str
    plugin_id: str
    plugin_version: str
    source_commit: str
    parameter_digest: str
    input_digest: str
    duration_ms: float = Field(ge=0)
    exit_code: int | None = None


__all__ = [
    "StrategyRef",
    "WorkerFailurePayload",
    "WorkerLimits",
    "WorkerRequest",
    "WorkerResponse",
    "WorkerRunRecord",
    "WorkerSuccessPayload",
]
