"""Low-cardinality Prometheus metrics for operational outcomes.

Callers select labels from enums defined here.  Values derived from requests,
accounts, instruments, exceptions, or other payloads are deliberately not part
of this API, so they cannot accidentally become metric labels.
"""

from __future__ import annotations

import importlib
import math
from enum import StrEnum
from typing import Final, Protocol, cast


class ObservabilityDependencyError(RuntimeError):
    """Raised when an explicitly requested observability backend is absent."""


class MetricRegistrationError(RuntimeError):
    """Raised when the selected registry already owns these metric names."""


class _MetricChild(Protocol):
    def inc(self, amount: int | float = 1) -> None: ...

    def observe(self, amount: int | float) -> None: ...

    def set(self, value: int | float) -> None: ...


class _MetricFamily(Protocol):
    def labels(self, *label_values: str) -> _MetricChild: ...


class _PrometheusModule(Protocol):
    CollectorRegistry: type[object]

    def Counter(
        self, name: str, documentation: str, labelnames: tuple[str, ...], *, registry: object
    ) -> _MetricFamily: ...

    def Gauge(
        self, name: str, documentation: str, labelnames: tuple[str, ...], *, registry: object
    ) -> _MetricFamily: ...

    def Histogram(
        self,
        name: str,
        documentation: str,
        labelnames: tuple[str, ...],
        *,
        buckets: tuple[float, ...],
        registry: object,
    ) -> _MetricFamily: ...

    def generate_latest(self, registry: object) -> bytes: ...


def _prometheus() -> _PrometheusModule:
    try:
        module = importlib.import_module("prometheus_client")
    except ModuleNotFoundError as exc:
        raise ObservabilityDependencyError(
            "Prometheus support requires the 'observability' dependency profile"
        ) from exc
    return cast(_PrometheusModule, module)


class Workflow(StrEnum):
    DATA = "data"
    AGENT = "agent"
    STRATEGY = "strategy"
    RISK = "risk"
    APPROVAL = "approval"
    BROKER = "broker"
    RECONCILIATION = "reconciliation"


class Outcome(StrEnum):
    SUCCESS = "success"
    REJECTED = "rejected"
    EXPIRED = "expired"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    UNKNOWN = "unknown"
    ERROR = "error"


class ProviderOperation(StrEnum):
    QUOTE = "quote"
    HISTORY = "history"
    FUNDAMENTALS = "fundamentals"
    NEWS = "news"
    PORTFOLIO = "portfolio"
    ORDERS = "orders"
    MCP_DISCOVERY = "mcp_discovery"
    MCP_CALL = "mcp_call"


class DataKind(StrEnum):
    QUOTE = "quote"
    MARKET_BAR = "market_bar"
    FUNDAMENTALS = "fundamentals"
    NEWS = "news"
    PORTFOLIO = "portfolio"


class TokenDirection(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class OrderState(StrEnum):
    PENDING = "pending"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    MANUAL_REVIEW = "manual_review"


class PnlThreshold(StrEnum):
    WARNING = "warning"
    STOP = "stop"


_DURATION_BUCKETS: Final[tuple[float, ...]] = (
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
)
_MAX_EXACT_INTEGER: Final[int] = (1 << 53) - 1


def _enum_value[E: StrEnum](value: E, enum_type: type[E]) -> str:
    if type(value) is not enum_type:
        raise TypeError(f"label must be {enum_type.__name__}, not a free-form value")
    return value.value


def _non_negative(value: int | float, *, field: str) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field} must be a non-negative number")
    return value


def _non_negative_int(value: int, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_EXACT_INTEGER
    ):
        raise ValueError(f"{field} must be a non-negative exact integer")
    return value


class AinvestMetrics:
    """Metrics bound to one explicitly owned Prometheus registry.

    A private registry is created by default, which keeps tests and embedded
    processes isolated from Prometheus' module-global registry.  Supplying the
    same registry to a second instance fails with a stable project exception
    instead of silently creating duplicate collectors.
    """

    def __init__(self, registry: object | None = None) -> None:
        prometheus = _prometheus()
        self.registry = registry if registry is not None else prometheus.CollectorRegistry()
        try:
            self._workflow_outcomes = prometheus.Counter(
                "ainvest_workflow_outcomes_total",
                "Completed workflow operations by stable outcome.",
                ("workflow", "outcome"),
                registry=self.registry,
            )
            self._workflow_duration = prometheus.Histogram(
                "ainvest_workflow_duration_seconds",
                "Workflow duration by stable workflow category.",
                ("workflow",),
                buckets=_DURATION_BUCKETS,
                registry=self.registry,
            )
            self._provider_outcomes = prometheus.Counter(
                "ainvest_provider_requests_total",
                "Provider-neutral request outcomes by stable operation.",
                ("operation", "outcome"),
                registry=self.registry,
            )
            self._provider_duration = prometheus.Histogram(
                "ainvest_provider_request_duration_seconds",
                "Provider-neutral request duration by stable operation.",
                ("operation",),
                buckets=_DURATION_BUCKETS,
                registry=self.registry,
            )
            self._data_freshness = prometheus.Gauge(
                "ainvest_data_freshness_seconds",
                "Age of the most recently accepted input by stable data kind.",
                ("data_kind",),
                registry=self.registry,
            )
            self._agent_tokens = prometheus.Counter(
                "ainvest_agent_tokens_total",
                "Model token usage by input or output direction.",
                ("direction",),
                registry=self.registry,
            )
            self._order_states = prometheus.Gauge(
                "ainvest_orders",
                "Current order count by stable state.",
                ("state",),
                registry=self.registry,
            )
            self._pnl_threshold = prometheus.Gauge(
                "ainvest_pnl_threshold_breached",
                "Whether a configured P&L threshold class is breached (0 or 1).",
                ("threshold",),
                registry=self.registry,
            )
        except ValueError as exc:
            raise MetricRegistrationError(
                "ainvest metrics are already registered in this registry"
            ) from exc

    def observe_workflow(
        self,
        workflow: Workflow,
        outcome: Outcome,
        *,
        duration_seconds: float,
    ) -> None:
        workflow_value = _enum_value(workflow, Workflow)
        outcome_value = _enum_value(outcome, Outcome)
        duration = _non_negative(duration_seconds, field="duration_seconds")
        self._workflow_outcomes.labels(workflow_value, outcome_value).inc()
        self._workflow_duration.labels(workflow_value).observe(duration)

    def observe_provider(
        self,
        operation: ProviderOperation,
        outcome: Outcome,
        *,
        duration_seconds: float,
    ) -> None:
        operation_value = _enum_value(operation, ProviderOperation)
        outcome_value = _enum_value(outcome, Outcome)
        duration = _non_negative(duration_seconds, field="duration_seconds")
        self._provider_outcomes.labels(operation_value, outcome_value).inc()
        self._provider_duration.labels(operation_value).observe(duration)

    def set_data_freshness(self, data_kind: DataKind, *, age_seconds: float) -> None:
        kind_value = _enum_value(data_kind, DataKind)
        age = _non_negative(age_seconds, field="age_seconds")
        self._data_freshness.labels(kind_value).set(age)

    def add_agent_tokens(self, direction: TokenDirection, *, count: int) -> None:
        direction_value = _enum_value(direction, TokenDirection)
        token_count = _non_negative_int(count, field="count")
        self._agent_tokens.labels(direction_value).inc(token_count)

    def set_order_state_count(self, state: OrderState, *, count: int) -> None:
        state_value = _enum_value(state, OrderState)
        order_count = _non_negative_int(count, field="count")
        self._order_states.labels(state_value).set(order_count)

    def set_pnl_threshold(self, threshold: PnlThreshold, *, breached: bool) -> None:
        threshold_value = _enum_value(threshold, PnlThreshold)
        if type(breached) is not bool:
            raise TypeError("breached must be bool")
        self._pnl_threshold.labels(threshold_value).set(int(breached))

    def render(self) -> bytes:
        """Render only this instance's registry in Prometheus text format."""
        return _prometheus().generate_latest(self.registry)


__all__ = [
    "AinvestMetrics",
    "DataKind",
    "MetricRegistrationError",
    "ObservabilityDependencyError",
    "OrderState",
    "Outcome",
    "PnlThreshold",
    "ProviderOperation",
    "TokenDirection",
    "Workflow",
]
