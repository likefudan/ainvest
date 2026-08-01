"""Tests for bounded, payload-free tracing helpers."""

from __future__ import annotations

from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any, cast

import pytest

from ainvest.config import TradingMode
from ainvest.observability.metrics import Outcome, Workflow
from ainvest.observability.tracing import SafeTracer, SpanName, TraceMetadata


class _CapturedSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, str] = {}
        self.events: list[tuple[str, dict[str, str]]] = []
        self.statuses: list[object] = []

    def set_attribute(self, key: str, value: str) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict[str, str] | None = None) -> None:
        self.events.append((name, attributes or {}))

    def set_status(self, status: object) -> None:
        self.statuses.append(status)


class _SpanContext(AbstractContextManager[_CapturedSpan]):
    def __init__(self, span: _CapturedSpan) -> None:
        self._span = span

    def __enter__(self) -> _CapturedSpan:
        return self._span

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


class _CapturedTracer:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.spans: list[_CapturedSpan] = []
        self.options: list[tuple[bool, bool]] = []

    def start_as_current_span(
        self,
        name: str,
        *,
        record_exception: bool,
        set_status_on_exception: bool,
    ) -> AbstractContextManager[_CapturedSpan]:
        self.names.append(name)
        self.options.append((record_exception, set_status_on_exception))
        span = _CapturedSpan()
        self.spans.append(span)
        return _SpanContext(span)


def _metadata() -> TraceMetadata:
    return TraceMetadata(
        workflow=Workflow.RISK,
        mode=TradingMode.PAPER,
        correlation_id="corr_12345678",
        causation_id="cmd_12345678",
        proposal_id="ordp_12345678",
        strategy_run_id="srun_12345678",
        input_digest="sha256:" + "a" * 64,
        output_digest="sha256:" + "b" * 64,
    )


@pytest.mark.unit
def test_trace_contains_only_allowlisted_metadata() -> None:
    captured = _CapturedTracer()
    tracer = SafeTracer(cast(Any, captured))

    with tracer.span(SpanName.RISK_EVALUATE, _metadata()):
        pass

    span = captured.spans[0]
    assert captured.names == ["ainvest.risk.evaluate"]
    assert captured.options == [(False, False)]
    assert span.attributes == {
        "ainvest.workflow": "risk",
        "ainvest.mode": "paper",
        "ainvest.correlation_id": "corr_12345678",
        "ainvest.causation_id": "cmd_12345678",
        "ainvest.proposal_id": "ordp_12345678",
        "ainvest.strategy_run_id": "srun_12345678",
        "ainvest.input_digest": "sha256:" + "a" * 64,
        "ainvest.output_digest": "sha256:" + "b" * 64,
        "ainvest.outcome": "success",
    }
    assert span.events == []
    assert len(span.statuses) == 1


@pytest.mark.unit
def test_trace_error_records_type_without_exception_message_or_payload() -> None:
    captured = _CapturedTracer()
    tracer = SafeTracer(cast(Any, captured))
    secret = "Bearer synthetic-secret-token"

    with (
        pytest.raises(RuntimeError, match="synthetic-secret"),
        tracer.span(SpanName.BROKER_CALL, _metadata()),
    ):
        raise RuntimeError(secret)

    span = captured.spans[0]
    assert span.attributes["ainvest.outcome"] == "error"
    assert captured.options == [(False, False)]
    assert span.events == [("exception", {"exception.type": "builtins.RuntimeError"})]
    assert secret not in repr(span.attributes)
    assert secret not in repr(span.events)
    assert secret not in repr(span.statuses)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("correlation_id", "account-123456"),
        ("causation_id", "token-secret-value"),
        ("proposal_id", "AAPL"),
        ("input_digest", "sha256:not-a-digest"),
    ],
)
def test_trace_metadata_rejects_unbounded_values(field: str, value: str) -> None:
    values: dict[str, object] = {
        "workflow": Workflow.DATA,
        "mode": TradingMode.PAPER,
        field: value,
    }
    with pytest.raises(ValueError, match=f"invalid {field}"):
        TraceMetadata(**values)  # type: ignore[arg-type]


@pytest.mark.unit
def test_disabled_tracer_is_deterministic_without_exporter() -> None:
    tracer = SafeTracer(disabled=True)
    with tracer.span(SpanName.DATA_FETCH, _metadata(), outcome=Outcome.UNAVAILABLE):
        pass
