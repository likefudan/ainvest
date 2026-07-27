"""Stable machine-readable codes for strategy worker outcomes."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class WorkerFailureCode(StrEnum):
    """Fail-closed classification for a single strategy worker run."""

    TIMEOUT = "WORKER_TIMEOUT"
    CRASH = "WORKER_CRASH"
    OOM = "WORKER_OOM"
    INVALID_OUTPUT = "WORKER_INVALID_OUTPUT"
    INVALID_INPUT = "WORKER_INVALID_INPUT"
    NETWORK_DENIED = "WORKER_NETWORK_DENIED"
    SECRET_ACCESS = "WORKER_SECRET_ACCESS"
    EVALUATION_ERROR = "WORKER_EVALUATION_ERROR"
    INTERNAL_ERROR = "WORKER_INTERNAL_ERROR"


class WorkerStatus(StrEnum):
    """High-level status for one isolated strategy evaluation."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


# Environment variables injected for child self-configuration (never secrets).
ENV_WALL_TIMEOUT: Final[str] = "AINVEST_WORKER_WALL_TIMEOUT_SECONDS"
ENV_CPU_SECONDS: Final[str] = "AINVEST_WORKER_CPU_SECONDS"
ENV_MEMORY_LIMIT: Final[str] = "AINVEST_WORKER_MEMORY_LIMIT_BYTES"
ENV_BLOCK_NETWORK: Final[str] = "AINVEST_WORKER_BLOCK_NETWORK"
ENV_READ_ONLY_WORKDIR: Final[str] = "AINVEST_WORKER_READ_ONLY_WORKDIR"
ENV_WORKDIR: Final[str] = "AINVEST_WORKER_WORKDIR"

__all__ = [
    "ENV_BLOCK_NETWORK",
    "ENV_CPU_SECONDS",
    "ENV_MEMORY_LIMIT",
    "ENV_READ_ONLY_WORKDIR",
    "ENV_WALL_TIMEOUT",
    "ENV_WORKDIR",
    "WorkerFailureCode",
    "WorkerStatus",
]
