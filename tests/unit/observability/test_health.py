"""Tests for deterministic dependency-aware health snapshots."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from ainvest.config import TradingMode
from ainvest.observability.health import (
    DependencyHealth,
    DependencyKind,
    DependencyRequirement,
    DependencyStatus,
    ExecutionPosture,
    HealthAggregator,
    HealthReason,
    RuntimePosture,
)

NOW = datetime(2026, 8, 1, 17, 30, tzinfo=UTC)


def _dependency(
    name: str,
    *,
    status: DependencyStatus,
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
        observed_at=NOW,
    )


@pytest.mark.unit
def test_paper_health_is_explicitly_read_only_with_paper_execution() -> None:
    health = HealthAggregator(RuntimePosture.from_mode(TradingMode.PAPER), clock=lambda: NOW)

    snapshot = health.snapshot()
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
def test_required_external_outage_removes_readiness_but_not_liveness() -> None:
    health = HealthAggregator(RuntimePosture.from_mode(TradingMode.PAPER), clock=lambda: NOW)
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
    optional = HealthAggregator(RuntimePosture.from_mode(TradingMode.RESEARCH), clock=lambda: NOW)
    optional.update(
        _dependency(
            "news_feed",
            status=DependencyStatus.NOT_READY,
            requirement=DependencyRequirement.DEGRADED_ALLOWED,
        )
    )
    assert optional.snapshot()["readiness"] == "degraded"

    required = HealthAggregator(RuntimePosture.from_mode(TradingMode.PAPER), clock=lambda: NOW)
    required.update(_dependency("market_data", status=DependencyStatus.DEGRADED))
    assert required.snapshot()["readiness"] == "degraded"


@pytest.mark.unit
def test_required_not_ready_takes_precedence_over_degraded() -> None:
    health = HealthAggregator(RuntimePosture.from_mode(TradingMode.PAPER), clock=lambda: NOW)
    health.update(_dependency("news_feed", status=DependencyStatus.DEGRADED))
    health.update(_dependency("broker_read", status=DependencyStatus.NOT_READY))
    assert health.snapshot()["status"] == "not_ready"


@pytest.mark.unit
def test_application_failure_is_the_only_explicit_liveness_failure() -> None:
    health = HealthAggregator(RuntimePosture.from_mode(TradingMode.PAPER), clock=lambda: NOW)
    health.update(_dependency("broker_read", status=DependencyStatus.NOT_READY))
    assert health.snapshot()["liveness"] == "alive"

    health.set_application_alive(False)
    snapshot = health.snapshot()
    assert snapshot["liveness"] == "failed"
    assert snapshot["readiness"] == "not_ready"


@pytest.mark.unit
def test_snapshot_is_deterministic_sorted_and_json_safe() -> None:
    health = HealthAggregator(RuntimePosture.from_mode(TradingMode.PAPER), clock=lambda: NOW)
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
def test_dependency_contract_rejects_free_form_names_reasons_and_naive_time() -> None:
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
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        DependencyHealth(
            name="market_data",
            kind=DependencyKind.EXTERNAL,
            requirement=DependencyRequirement.REQUIRED,
            status=DependencyStatus.READY,
            reason=HealthReason.NONE,
            observed_at=NOW.replace(tzinfo=None),
        )
