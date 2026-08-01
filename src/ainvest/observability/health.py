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


class DependencySetMode(StrEnum):
    DECLARED = "declared"
    NONE = "none"


class ObservationResult(StrEnum):
    APPLIED = "applied"
    APPLIED_CONFLICT = "applied_conflict"
    IGNORED_DUPLICATE = "ignored_duplicate"
    IGNORED_STALE = "ignored_stale"


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
    OBSERVATION_CONFLICT = "observation_conflict"


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


def _validate_dependency_identity(
    name: str,
    kind: DependencyKind,
    requirement: DependencyRequirement,
) -> None:
    if type(name) is not str or _DEPENDENCY_NAME_RE.fullmatch(name) is None:
        raise ValueError("dependency name must be a stable low-cardinality identifier")
    if type(kind) is not DependencyKind:
        raise TypeError("kind must be DependencyKind")
    if type(requirement) is not DependencyRequirement:
        raise TypeError("requirement must be DependencyRequirement")


@dataclass(frozen=True, slots=True)
class DependencySpec:
    """A dependency identity declared before readiness evaluation begins."""

    name: str
    kind: DependencyKind
    requirement: DependencyRequirement

    def __post_init__(self) -> None:
        _validate_dependency_identity(self.name, self.kind, self.requirement)


@dataclass(frozen=True, slots=True)
class DependencyHealth:
    """A bounded dependency observation without free-form error details."""

    name: str
    kind: DependencyKind
    requirement: DependencyRequirement
    status: DependencyStatus
    reason: HealthReason
    observed_at: datetime
    sequence: int

    def __post_init__(self) -> None:
        _validate_dependency_identity(self.name, self.kind, self.requirement)
        for value, enum_type, field in (
            (self.status, DependencyStatus, "status"),
            (self.reason, HealthReason, "reason"),
        ):
            if type(value) is not enum_type:
                raise TypeError(f"{field} must be {enum_type.__name__}")
        _utc_iso(self.observed_at)
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or not 0 <= self.sequence < (1 << 63)
        ):
            raise ValueError("sequence must be a non-negative 63-bit integer")
        if self.status is DependencyStatus.READY and self.reason is not HealthReason.NONE:
            raise ValueError("ready dependencies must use reason=none")
        if self.status is not DependencyStatus.READY and self.reason is HealthReason.NONE:
            raise ValueError("unhealthy dependencies require a bounded reason")

    def as_dict(self) -> dict[str, str | int]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "requirement": self.requirement.value,
            "status": self.status.value,
            "reason": self.reason.value,
            "observed_at": _utc_iso(self.observed_at),
            "sequence": self.sequence,
        }


class HealthAggregator:
    """Aggregate readiness while keeping liveness independent of dependencies."""

    def __init__(
        self,
        posture: RuntimePosture,
        *,
        expected_dependencies: tuple[DependencySpec, ...],
        dependency_mode: DependencySetMode = DependencySetMode.DECLARED,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(posture) is not RuntimePosture:
            raise TypeError("posture must be RuntimePosture")
        if type(expected_dependencies) is not tuple or any(
            type(item) is not DependencySpec for item in expected_dependencies
        ):
            raise TypeError("expected_dependencies must be a tuple of DependencySpec")
        if type(dependency_mode) is not DependencySetMode:
            raise TypeError("dependency_mode must be DependencySetMode")
        if dependency_mode is DependencySetMode.DECLARED and not expected_dependencies:
            raise ValueError("declared dependency mode requires at least one dependency")
        if dependency_mode is DependencySetMode.NONE and expected_dependencies:
            raise ValueError("no-dependency mode cannot declare dependencies")
        specs = {item.name: item for item in expected_dependencies}
        if len(specs) != len(expected_dependencies):
            raise ValueError("dependency names must be unique")
        self._posture = posture
        self._clock = clock or (lambda: datetime.now(UTC))
        started_at = self._clock()
        _utc_iso(started_at)
        self._specs = specs
        self._dependencies = {
            name: DependencyHealth(
                name=name,
                kind=spec.kind,
                requirement=spec.requirement,
                status=DependencyStatus.NOT_READY,
                reason=HealthReason.STARTING,
                observed_at=started_at,
                sequence=0,
            )
            for name, spec in specs.items()
        }
        self._liveness = LivenessStatus.ALIVE
        self._lock = Lock()

    def update(self, dependency: DependencyHealth) -> ObservationResult:
        if type(dependency) is not DependencyHealth:
            raise TypeError("dependency must be DependencyHealth")
        with self._lock:
            spec = self._specs.get(dependency.name)
            if spec is None:
                raise ValueError("dependency was not declared at construction")
            if dependency.kind is not spec.kind or dependency.requirement is not spec.requirement:
                raise ValueError("dependency identity does not match its declaration")
            current = self._dependencies[dependency.name]
            if dependency.sequence < current.sequence:
                return ObservationResult.IGNORED_STALE
            if dependency.sequence > current.sequence:
                self._dependencies[dependency.name] = dependency
                return ObservationResult.APPLIED
            return self._resolve_equal_sequence(current, dependency)

    def remove(
        self,
        name: str,
        *,
        sequence: int,
        observed_at: datetime | None = None,
    ) -> ObservationResult:
        """Record loss of a declared dependency without erasing its contract."""
        if type(name) is not str or _DEPENDENCY_NAME_RE.fullmatch(name) is None:
            raise ValueError("invalid dependency name")
        with self._lock:
            spec = self._specs.get(name)
        if spec is None:
            raise ValueError("dependency was not declared at construction")
        return self.update(
            DependencyHealth(
                name=name,
                kind=spec.kind,
                requirement=spec.requirement,
                status=DependencyStatus.NOT_READY,
                reason=HealthReason.UNAVAILABLE,
                observed_at=observed_at or self._clock(),
                sequence=sequence,
            )
        )

    def _resolve_equal_sequence(
        self,
        current: DependencyHealth,
        candidate: DependencyHealth,
    ) -> ObservationResult:
        if current == candidate:
            return ObservationResult.IGNORED_DUPLICATE
        observed_at = max(current.observed_at, candidate.observed_at)
        if current.status is candidate.status and current.reason is candidate.reason:
            if observed_at == current.observed_at:
                return ObservationResult.IGNORED_STALE
            self._dependencies[current.name] = DependencyHealth(
                name=current.name,
                kind=current.kind,
                requirement=current.requirement,
                status=current.status,
                reason=current.reason,
                observed_at=observed_at,
                sequence=current.sequence,
            )
            return ObservationResult.APPLIED

        # Conflicting results for one logical observation are unsafe. The
        # result is commutative, so thread completion order cannot restore
        # readiness or change the exported snapshot.
        conflict = DependencyHealth(
            name=current.name,
            kind=current.kind,
            requirement=current.requirement,
            status=DependencyStatus.NOT_READY,
            reason=HealthReason.OBSERVATION_CONFLICT,
            observed_at=observed_at,
            sequence=current.sequence,
        )
        if current == conflict:
            return ObservationResult.IGNORED_STALE
        self._dependencies[current.name] = conflict
        return ObservationResult.APPLIED_CONFLICT

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
    "DependencySetMode",
    "DependencySpec",
    "DependencyStatus",
    "ExecutionPosture",
    "HealthAggregator",
    "HealthReason",
    "HealthStatus",
    "LivenessStatus",
    "ObservationResult",
    "RuntimePosture",
]
