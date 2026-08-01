"""Deterministic, provider-neutral liveness and readiness aggregation."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Final

from ainvest.config import TradingMode

_DEPENDENCY_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")


class HealthStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    NOT_READY = "not_ready"


class LivenessStatus(StrEnum):
    ALIVE = "alive"
    FAILED = "failed"


class DependencyStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    NOT_READY = "not_ready"


class DependencyKind(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class DependencyRequirement(StrEnum):
    REQUIRED = "required"
    DEGRADED_ALLOWED = "degraded_allowed"


class HealthReason(StrEnum):
    NONE = "none"
    STARTING = "starting"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    STALE = "stale"
    MISCONFIGURED = "misconfigured"
    AUTHENTICATION = "authentication"
    CONTRACT_MISMATCH = "contract_mismatch"
    INTERNAL_FAILURE = "internal_failure"


class ExecutionPosture(StrEnum):
    DISABLED = "disabled"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class RuntimePosture:
    """Descriptive posture derived from the authoritative runtime mode."""

    mode: TradingMode
    read_only: bool
    execution: ExecutionPosture

    def __post_init__(self) -> None:
        if type(self.mode) is not TradingMode:
            raise TypeError("mode must be TradingMode")
        if type(self.read_only) is not bool:
            raise TypeError("read_only must be bool")
        if type(self.execution) is not ExecutionPosture:
            raise TypeError("execution must be ExecutionPosture")
        expected_execution = {
            TradingMode.RESEARCH: ExecutionPosture.DISABLED,
            TradingMode.PAPER: ExecutionPosture.PAPER,
            TradingMode.LIVE: ExecutionPosture.LIVE,
        }[self.mode]
        if self.execution is not expected_execution:
            raise ValueError("execution posture must match runtime mode")
        if self.read_only is (self.mode is TradingMode.LIVE):
            raise ValueError("read_only posture must match runtime mode")

    @classmethod
    def from_mode(cls, mode: TradingMode) -> RuntimePosture:
        if type(mode) is not TradingMode:
            raise TypeError("mode must be TradingMode")
        execution = {
            TradingMode.RESEARCH: ExecutionPosture.DISABLED,
            TradingMode.PAPER: ExecutionPosture.PAPER,
            TradingMode.LIVE: ExecutionPosture.LIVE,
        }[mode]
        # Paper may simulate writes, but has no authority to send live writes.
        return cls(mode=mode, read_only=mode is not TradingMode.LIVE, execution=execution)

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "read_only": self.read_only,
            "execution": self.execution.value,
        }


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("health timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class DependencyHealth:
    """A bounded dependency observation without free-form error details."""

    name: str
    kind: DependencyKind
    requirement: DependencyRequirement
    status: DependencyStatus
    reason: HealthReason
    observed_at: datetime

    def __post_init__(self) -> None:
        if type(self.name) is not str or _DEPENDENCY_NAME_RE.fullmatch(self.name) is None:
            raise ValueError("dependency name must be a stable low-cardinality identifier")
        for value, enum_type, field in (
            (self.kind, DependencyKind, "kind"),
            (self.requirement, DependencyRequirement, "requirement"),
            (self.status, DependencyStatus, "status"),
            (self.reason, HealthReason, "reason"),
        ):
            if type(value) is not enum_type:
                raise TypeError(f"{field} must be {enum_type.__name__}")
        _utc_iso(self.observed_at)
        if self.status is DependencyStatus.READY and self.reason is not HealthReason.NONE:
            raise ValueError("ready dependencies must use reason=none")
        if self.status is not DependencyStatus.READY and self.reason is HealthReason.NONE:
            raise ValueError("unhealthy dependencies require a bounded reason")

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "requirement": self.requirement.value,
            "status": self.status.value,
            "reason": self.reason.value,
            "observed_at": _utc_iso(self.observed_at),
        }


class HealthAggregator:
    """Aggregate readiness while keeping liveness independent of dependencies."""

    def __init__(
        self,
        posture: RuntimePosture,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(posture) is not RuntimePosture:
            raise TypeError("posture must be RuntimePosture")
        self._posture = posture
        self._clock = clock or (lambda: datetime.now(UTC))
        self._dependencies: dict[str, DependencyHealth] = {}
        self._liveness = LivenessStatus.ALIVE
        self._lock = Lock()

    def update(self, dependency: DependencyHealth) -> None:
        if type(dependency) is not DependencyHealth:
            raise TypeError("dependency must be DependencyHealth")
        with self._lock:
            self._dependencies[dependency.name] = dependency

    def remove(self, name: str) -> None:
        if type(name) is not str or _DEPENDENCY_NAME_RE.fullmatch(name) is None:
            raise ValueError("invalid dependency name")
        with self._lock:
            self._dependencies.pop(name, None)

    def set_application_alive(self, alive: bool) -> None:
        """Set process liveness; dependency failures must never call this."""
        if type(alive) is not bool:
            raise TypeError("alive must be bool")
        with self._lock:
            self._liveness = LivenessStatus.ALIVE if alive else LivenessStatus.FAILED

    def snapshot(self) -> dict[str, object]:
        checked_at = self._clock()
        checked_at_json = _utc_iso(checked_at)
        with self._lock:
            dependencies = tuple(sorted(self._dependencies.values(), key=lambda item: item.name))
            liveness = self._liveness

        readiness = self._readiness(dependencies, liveness)
        return {
            "status": readiness.value,
            "liveness": liveness.value,
            "readiness": readiness.value,
            "checked_at": checked_at_json,
            "posture": self._posture.as_dict(),
            "dependencies": [item.as_dict() for item in dependencies],
        }

    @staticmethod
    def _readiness(
        dependencies: tuple[DependencyHealth, ...],
        liveness: LivenessStatus,
    ) -> HealthStatus:
        if liveness is LivenessStatus.FAILED:
            return HealthStatus.NOT_READY
        if any(
            item.status is DependencyStatus.NOT_READY
            and item.requirement is DependencyRequirement.REQUIRED
            for item in dependencies
        ):
            return HealthStatus.NOT_READY
        if any(item.status is not DependencyStatus.READY for item in dependencies):
            return HealthStatus.DEGRADED
        return HealthStatus.READY


__all__ = [
    "DependencyHealth",
    "DependencyKind",
    "DependencyRequirement",
    "DependencyStatus",
    "ExecutionPosture",
    "HealthAggregator",
    "HealthReason",
    "HealthStatus",
    "LivenessStatus",
    "RuntimePosture",
]
