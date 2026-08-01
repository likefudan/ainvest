"""Tests for deterministic dependency-aware health snapshots."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any, cast

import pytest

from ainvest.config import TradingMode
from ainvest.observability.health import (
    DependencyHealth,
    DependencyKind,
    DependencyRequirement,
    DependencySetMode,
    DependencySpec,
    DependencyStatus,
    ExecutionPosture,
    HealthAggregator,
    HealthReason,
    ObservationResult,
    RuntimePosture,
)

NOW = datetime(2026, 8, 1, 17, 30, tzinfo=UTC)


def _spec(
    name: str,
    *,
    requirement: DependencyRequirement = DependencyRequirement.REQUIRED,
    kind: DependencyKind = DependencyKind.EXTERNAL,
) -> DependencySpec:
    return DependencySpec(name=name, kind=kind, requirement=requirement)


def _health(
    *dependencies: DependencySpec,
    mode: TradingMode = TradingMode.PAPER,
) -> HealthAggregator:
    return HealthAggregator(
        RuntimePosture.from_mode(mode),
        expected_dependencies=dependencies,
        clock=lambda: NOW,
    )


def _no_dependency_health(*, mode: TradingMode = TradingMode.PAPER) -> HealthAggregator:
    return HealthAggregator(
        RuntimePosture.from_mode(mode),
        expected_dependencies=(),
        dependency_mode=DependencySetMode.NONE,
        clock=lambda: NOW,
    )


def _dependency(
    name: str,
    *,
    status: DependencyStatus,
    sequence: int = 1,
    observed_at: datetime = NOW,
    requirement: DependencyRequirement = DependencyRequirement.REQUIRED,
    kind: DependencyKind = DependencyKind.EXTERNAL,
    reason: HealthReason | None = None,
) -> DependencyHealth:
    return DependencyHealth(
        name=name,
        kind=kind,
        requirement=requirement,
        status=status,
        reason=(
            HealthReason.NONE
            if status is DependencyStatus.READY
            else reason or HealthReason.UNAVAILABLE
        ),
        observed_at=observed_at,
        sequence=sequence,
    )


@pytest.mark.unit
def test_paper_health_is_explicitly_read_only_with_paper_execution() -> None:
    snapshot = _no_dependency_health().snapshot()
    assert snapshot["status"] == "ready"
    assert snapshot["liveness"] == "alive"
    assert snapshot["readiness"] == "ready"
    assert snapshot["posture"] == {
        "mode": "paper",
        "read_only": True,
        "execution": "paper",
    }


@pytest.mark.unit
def test_runtime_posture_rejects_a_forged_mode_combination() -> None:
    with pytest.raises(ValueError, match="read_only"):
        RuntimePosture(
            mode=TradingMode.LIVE,
            read_only=True,
            execution=ExecutionPosture.LIVE,
        )


@pytest.mark.unit
def test_declared_dependencies_start_not_ready_until_observed() -> None:
    snapshot = _health(_spec("market_data")).snapshot()
    assert snapshot["readiness"] == "not_ready"
    assert snapshot["liveness"] == "alive"
    assert snapshot["dependencies"] == [
        {
            "name": "market_data",
            "kind": "external",
            "requirement": "required",
            "status": "not_ready",
            "reason": "starting",
            "observed_at": "2026-08-01T17:30:00Z",
            "sequence": 0,
        }
    ]


@pytest.mark.unit
def test_empty_dependency_set_requires_explicit_no_dependency_mode() -> None:
    with pytest.raises(ValueError, match="requires at least one"):
        HealthAggregator(
            RuntimePosture.from_mode(TradingMode.RESEARCH),
            expected_dependencies=(),
            clock=lambda: NOW,
        )

    assert _no_dependency_health(mode=TradingMode.RESEARCH).snapshot()["readiness"] == "ready"
    with pytest.raises(ValueError, match="cannot declare"):
        HealthAggregator(
            RuntimePosture.from_mode(TradingMode.RESEARCH),
            expected_dependencies=(_spec("market_data"),),
            dependency_mode=DependencySetMode.NONE,
            clock=lambda: NOW,
        )


@pytest.mark.unit
def test_required_external_outage_removes_readiness_but_not_liveness() -> None:
    health = _health(_spec("market_data"))
    health.update(
        _dependency(
            "market_data",
            status=DependencyStatus.NOT_READY,
            reason=HealthReason.TIMEOUT,
        )
    )

    snapshot = health.snapshot()
    assert snapshot["status"] == "not_ready"
    assert snapshot["readiness"] == "not_ready"
    assert snapshot["liveness"] == "alive"


@pytest.mark.unit
def test_optional_outage_and_required_degradation_produce_degraded_readiness() -> None:
    optional = _health(
        _spec("news_feed", requirement=DependencyRequirement.DEGRADED_ALLOWED),
        mode=TradingMode.RESEARCH,
    )
    optional.update(
        _dependency(
            "news_feed",
            status=DependencyStatus.NOT_READY,
            requirement=DependencyRequirement.DEGRADED_ALLOWED,
        )
    )
    assert optional.snapshot()["readiness"] == "degraded"

    required = _health(_spec("market_data"))
    required.update(_dependency("market_data", status=DependencyStatus.DEGRADED))
    assert required.snapshot()["readiness"] == "degraded"


@pytest.mark.unit
def test_required_not_ready_takes_precedence_over_degraded() -> None:
    health = _health(_spec("news_feed"), _spec("broker_read"))
    health.update(_dependency("news_feed", status=DependencyStatus.DEGRADED))
    health.update(_dependency("broker_read", status=DependencyStatus.NOT_READY))
    assert health.snapshot()["status"] == "not_ready"


@pytest.mark.unit
def test_out_of_order_completion_cannot_restore_readiness() -> None:
    health = _health(_spec("market_data"))
    newer_failure = _dependency(
        "market_data",
        status=DependencyStatus.NOT_READY,
        sequence=2,
        observed_at=NOW,
        reason=HealthReason.TIMEOUT,
    )
    older_success = _dependency(
        "market_data",
        status=DependencyStatus.READY,
        sequence=1,
        observed_at=NOW - timedelta(minutes=5),
    )

    newer_completed = Event()

    def complete_newer() -> ObservationResult:
        result = health.update(newer_failure)
        newer_completed.set()
        return result

    def complete_older_late() -> ObservationResult:
        assert newer_completed.wait(timeout=5)
        return health.update(older_success)

    with ThreadPoolExecutor(max_workers=2) as executor:
        failure = executor.submit(complete_newer)
        success = executor.submit(complete_older_late)

    assert failure.result() is ObservationResult.APPLIED
    assert success.result() is ObservationResult.IGNORED_STALE
    snapshot = health.snapshot()
    assert snapshot["readiness"] == "not_ready"
    assert cast(list[dict[str, Any]], snapshot["dependencies"])[0]["sequence"] == 2


@pytest.mark.unit
@pytest.mark.parametrize("failure_first", [False, True])
def test_equal_sequence_conflict_fails_closed_independent_of_completion_order(
    failure_first: bool,
) -> None:
    health = _health(_spec("market_data"))
    success = _dependency("market_data", status=DependencyStatus.READY, sequence=7)
    failure = _dependency(
        "market_data",
        status=DependencyStatus.NOT_READY,
        sequence=7,
        reason=HealthReason.TIMEOUT,
    )
    first, second = (failure, success) if failure_first else (success, failure)

    health.update(first)
    assert health.update(second) is ObservationResult.APPLIED_CONFLICT

    dependency = cast(list[dict[str, Any]], health.snapshot()["dependencies"])[0]
    assert dependency["status"] == "not_ready"
    assert dependency["reason"] == "observation_conflict"
    assert dependency["sequence"] == 7


@pytest.mark.unit
def test_equal_sequence_duplicate_is_idempotent_and_timestamp_tie_is_deterministic() -> None:
    health = _health(_spec("market_data"))
    ready = _dependency("market_data", status=DependencyStatus.READY, sequence=3)
    assert health.update(ready) is ObservationResult.APPLIED
    assert health.update(ready) is ObservationResult.IGNORED_DUPLICATE

    same_result_later_clock = _dependency(
        "market_data",
        status=DependencyStatus.READY,
        sequence=3,
        observed_at=NOW + timedelta(seconds=1),
    )
    assert health.update(same_result_later_clock) is ObservationResult.APPLIED
    assert health.update(ready) is ObservationResult.IGNORED_STALE
    dependency = cast(list[dict[str, Any]], health.snapshot()["dependencies"])[0]
    assert dependency["observed_at"] == "2026-08-01T17:30:01Z"


@pytest.mark.unit
def test_removing_last_required_dependency_stays_not_ready_and_can_recover() -> None:
    health = _health(_spec("broker_read"))
    health.update(_dependency("broker_read", status=DependencyStatus.READY, sequence=1))
    assert health.snapshot()["readiness"] == "ready"

    assert health.remove("broker_read", sequence=2) is ObservationResult.APPLIED
    removed = health.snapshot()
    assert removed["readiness"] == "not_ready"
    assert removed["liveness"] == "alive"
    dependency = cast(list[dict[str, Any]], removed["dependencies"])[0]
    assert dependency["reason"] == "unavailable"

    assert (
        health.update(_dependency("broker_read", status=DependencyStatus.READY, sequence=3))
        is ObservationResult.APPLIED
    )
    assert health.snapshot()["readiness"] == "ready"


@pytest.mark.unit
def test_application_failure_is_the_only_explicit_liveness_failure() -> None:
    health = _health(_spec("broker_read"))
    health.update(_dependency("broker_read", status=DependencyStatus.NOT_READY))
    assert health.snapshot()["liveness"] == "alive"

    health.set_application_alive(False)
    snapshot = health.snapshot()
    assert snapshot["liveness"] == "failed"
    assert snapshot["readiness"] == "not_ready"


@pytest.mark.unit
def test_snapshot_is_deterministic_sorted_and_json_safe() -> None:
    health = _health(
        _spec("zeta_service"),
        _spec("alpha_service", requirement=DependencyRequirement.DEGRADED_ALLOWED),
    )
    health.update(_dependency("zeta_service", status=DependencyStatus.READY))
    health.update(
        _dependency(
            "alpha_service",
            status=DependencyStatus.DEGRADED,
            requirement=DependencyRequirement.DEGRADED_ALLOWED,
            reason=HealthReason.STALE,
        )
    )

    first = health.snapshot()
    assert first == health.snapshot()
    dependencies = cast(list[dict[str, Any]], first["dependencies"])
    assert [item["name"] for item in dependencies] == [
        "alpha_service",
        "zeta_service",
    ]
    assert first["checked_at"] == "2026-08-01T17:30:00Z"
    assert json.loads(json.dumps(first)) == first


@pytest.mark.unit
def test_dependency_contract_rejects_invalid_identity_sequence_and_time() -> None:
    with pytest.raises(ValueError, match="stable low-cardinality"):
        _dependency("account/123456", status=DependencyStatus.READY)

    with pytest.raises(ValueError, match="bounded reason"):
        DependencyHealth(
            name="market_data",
            kind=DependencyKind.EXTERNAL,
            requirement=DependencyRequirement.REQUIRED,
            status=DependencyStatus.NOT_READY,
            reason=HealthReason.NONE,
            observed_at=NOW,
            sequence=1,
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        _dependency(
            "market_data",
            status=DependencyStatus.READY,
            observed_at=NOW.replace(tzinfo=None),
        )

    with pytest.raises(ValueError, match="63-bit"):
        _dependency("market_data", status=DependencyStatus.READY, sequence=-1)


@pytest.mark.unit
def test_unknown_or_mismatched_dependency_cannot_expand_declared_health_surface() -> None:
    health = _health(_spec("market_data"))
    with pytest.raises(ValueError, match="not declared"):
        health.update(_dependency("news_feed", status=DependencyStatus.READY))
    with pytest.raises(ValueError, match="does not match"):
        health.update(
            _dependency(
                "market_data",
                status=DependencyStatus.READY,
                requirement=DependencyRequirement.DEGRADED_ALLOWED,
            )
        )
