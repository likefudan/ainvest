"""Tests for isolated, bounded Prometheus metrics."""

from __future__ import annotations

import importlib
import math
import sys
from collections.abc import Iterator
from types import ModuleType
from typing import Any, cast

import pytest

import ainvest.observability.metrics as metrics_module
from ainvest.observability.metrics import (
    AinvestMetrics,
    DataKind,
    MetricRegistrationError,
    OrderState,
    Outcome,
    PnlThreshold,
    ProviderOperation,
    TokenDirection,
    Workflow,
)


class _FakeRegistry:
    def __init__(self) -> None:
        self.families: dict[str, _FakeFamily] = {}


class _FakeChild:
    def __init__(self, family: _FakeFamily, labels: tuple[str, ...]) -> None:
        self._family = family
        self._labels = labels

    def inc(self, amount: int | float = 1) -> None:
        self._family.values[self._labels] = self._family.values.get(self._labels, 0) + amount

    def observe(self, amount: int | float) -> None:
        self._family.values[self._labels] = self._family.values.get(self._labels, 0) + amount

    def set(self, value: int | float) -> None:
        self._family.values[self._labels] = value


class _FakeFamily:
    def __init__(
        self,
        name: str,
        labelnames: tuple[str, ...],
        registry: _FakeRegistry,
    ) -> None:
        if name in registry.families:
            raise ValueError("duplicate")
        self.name = name
        self.labelnames = labelnames
        self.values: dict[tuple[str, ...], int | float] = {}
        registry.families[name] = self

    def labels(self, *label_values: str) -> _FakeChild:
        return _FakeChild(self, label_values)


def _family(
    name: str,
    documentation: str,
    labelnames: tuple[str, ...],
    *,
    registry: object,
    buckets: tuple[float, ...] | None = None,
) -> _FakeFamily:
    del documentation, buckets
    return _FakeFamily(name, labelnames, cast(_FakeRegistry, registry))


def _generate_latest(registry: object) -> bytes:
    lines: list[str] = []
    for family in cast(_FakeRegistry, registry).families.values():
        for labels, value in family.values.items():
            rendered_labels = ",".join(
                f'{key}="{label}"' for key, label in zip(family.labelnames, labels, strict=True)
            )
            lines.append(f"{family.name}{{{rendered_labels}}} {float(value)}")
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture(autouse=True)
def _fake_prometheus(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    module = ModuleType("prometheus_client")
    module.CollectorRegistry = _FakeRegistry  # type: ignore[attr-defined]
    module.Counter = _family  # type: ignore[attr-defined]
    module.Gauge = _family  # type: ignore[attr-defined]
    module.Histogram = _family  # type: ignore[attr-defined]
    module.generate_latest = _generate_latest  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "prometheus_client", module)
    yield


@pytest.mark.unit
def test_metrics_update_and_render_from_injected_registry() -> None:
    registry = _FakeRegistry()
    metrics = AinvestMetrics(registry)

    metrics.observe_workflow(Workflow.RISK, Outcome.REJECTED, duration_seconds=0.25)
    metrics.observe_provider(ProviderOperation.QUOTE, Outcome.SUCCESS, duration_seconds=0.1)
    metrics.set_data_freshness(DataKind.QUOTE, age_seconds=3.5)
    metrics.add_agent_tokens(TokenDirection.INPUT, count=42)
    metrics.set_order_state_count(OrderState.SUBMITTED, count=2)
    metrics.set_pnl_threshold(PnlThreshold.WARNING, breached=True)

    output = metrics.render().decode("utf-8")
    assert 'ainvest_workflow_outcomes_total{workflow="risk",outcome="rejected"} 1.0' in output
    assert 'ainvest_provider_requests_total{operation="quote",outcome="success"} 1.0' in output
    assert 'ainvest_data_freshness_seconds{data_kind="quote"} 3.5' in output
    assert 'ainvest_agent_tokens_total{direction="input"} 42.0' in output
    assert 'ainvest_orders{state="submitted"} 2.0' in output
    assert 'ainvest_pnl_threshold_breached{threshold="warning"} 1.0' in output


@pytest.mark.unit
def test_default_registry_is_private_and_duplicate_explicit_registry_fails() -> None:
    first = AinvestMetrics()
    second = AinvestMetrics()
    assert first.registry is not second.registry

    shared = _FakeRegistry()
    AinvestMetrics(shared)
    with pytest.raises(MetricRegistrationError, match="already registered"):
        AinvestMetrics(shared)


@pytest.mark.unit
def test_free_form_or_sensitive_metric_labels_are_rejected() -> None:
    metrics = AinvestMetrics()

    with pytest.raises(TypeError, match="Workflow"):
        metrics.observe_workflow(cast(Any, "account-123456"), Outcome.SUCCESS, duration_seconds=1)
    with pytest.raises(TypeError, match="ProviderOperation"):
        metrics.observe_provider(cast(Any, "AAPL"), Outcome.SUCCESS, duration_seconds=1)
    with pytest.raises(TypeError, match="breached"):
        metrics.set_pnl_threshold(PnlThreshold.STOP, breached=1)  # type: ignore[arg-type]

    assert b"account-123456" not in metrics.render()
    assert b"AAPL" not in metrics.render()


@pytest.mark.unit
@pytest.mark.parametrize("value", [-1.0, math.nan, math.inf])
def test_metric_observations_require_finite_non_negative_values(value: float) -> None:
    metrics = AinvestMetrics()
    with pytest.raises(ValueError, match="non-negative"):
        metrics.set_data_freshness(DataKind.QUOTE, age_seconds=value)


@pytest.mark.unit
def test_metric_counts_reject_floats_and_unbounded_integers() -> None:
    metrics = AinvestMetrics()
    with pytest.raises(ValueError, match="exact integer"):
        metrics.add_agent_tokens(TokenDirection.INPUT, count=cast(Any, 1.5))
    with pytest.raises(ValueError, match="exact integer"):
        metrics.set_order_state_count(OrderState.PENDING, count=1 << 60)


@pytest.mark.unit
def test_missing_optional_dependency_has_stable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> ModuleType:
        assert name == "prometheus_client"
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(metrics_module.ObservabilityDependencyError, match="observability"):
        AinvestMetrics()
