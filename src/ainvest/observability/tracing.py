"""Safe OpenTelemetry helpers with a deliberately narrow metadata surface."""

from __future__ import annotations

import importlib
import re
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType
from typing import Protocol, cast

from ainvest.config import TradingMode
from ainvest.observability.metrics import ObservabilityDependencyError, Outcome, Workflow

_CORRELATION_RE = re.compile(r"^corr_[A-Za-z0-9_-]{8,128}$")
_CAUSATION_RE = re.compile(r"^(?:cmd|evt|corr)_[A-Za-z0-9_-]{8,128}$")
_PROPOSAL_RE = re.compile(r"^ordp_[A-Za-z0-9_-]{4,128}$")
_STRATEGY_RUN_RE = re.compile(r"^srun_[A-Za-z0-9_-]{4,128}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


class SpanName(StrEnum):
    DATA_FETCH = "ainvest.data.fetch"
    AGENT_RUN = "ainvest.agent.run"
    STRATEGY_RUN = "ainvest.strategy.run"
    RISK_EVALUATE = "ainvest.risk.evaluate"
    APPROVAL_WAIT = "ainvest.approval.wait"
    BROKER_CALL = "ainvest.broker.call"
    RECONCILE = "ainvest.reconcile"


class _StatusCode(StrEnum):
    OK = "OK"
    ERROR = "ERROR"


class _Span(Protocol):
    def set_attribute(self, key: str, value: str) -> None: ...

    def add_event(self, name: str, attributes: Mapping[str, str] | None = None) -> None: ...

    def set_status(self, status: object) -> None: ...


class _Tracer(Protocol):
    def start_as_current_span(
        self,
        name: str,
        *,
        record_exception: bool,
        set_status_on_exception: bool,
    ) -> AbstractContextManager[_Span]: ...


class _StatusCodeType(Protocol):
    OK: object
    ERROR: object


class _OtelTraceModule(Protocol):
    StatusCode: _StatusCodeType

    def Status(self, status_code: object) -> object: ...

    def get_tracer(self, name: str) -> object: ...


def _otel_trace() -> _OtelTraceModule:
    try:
        module = importlib.import_module("opentelemetry.trace")
    except ModuleNotFoundError as exc:
        raise ObservabilityDependencyError(
            "Tracing requires the 'observability' dependency profile"
        ) from exc
    return cast(_OtelTraceModule, module)


class _NullSpan:
    def set_attribute(self, key: str, value: str) -> None:
        del key, value

    def add_event(self, name: str, attributes: Mapping[str, str] | None = None) -> None:
        del name, attributes

    def set_status(self, status: object) -> None:
        del status


class _NullSpanContext(AbstractContextManager[_Span]):
    def __enter__(self) -> _Span:
        return _NullSpan()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


class _NullTracer:
    def start_as_current_span(
        self,
        name: str,
        *,
        record_exception: bool,
        set_status_on_exception: bool,
    ) -> AbstractContextManager[_Span]:
        del name, record_exception, set_status_on_exception
        return _NullSpanContext()


def _validated(value: str | None, pattern: re.Pattern[str], field: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")
    return value


@dataclass(frozen=True, slots=True)
class TraceMetadata:
    """Allowlisted trace metadata; payloads and free-form attributes are absent."""

    workflow: Workflow
    mode: TradingMode
    correlation_id: str | None = None
    causation_id: str | None = None
    proposal_id: str | None = None
    strategy_run_id: str | None = None
    input_digest: str | None = None
    output_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.workflow) is not Workflow:
            raise TypeError("workflow must be Workflow")
        if type(self.mode) is not TradingMode:
            raise TypeError("mode must be TradingMode")
        _validated(self.correlation_id, _CORRELATION_RE, "correlation_id")
        _validated(self.causation_id, _CAUSATION_RE, "causation_id")
        _validated(self.proposal_id, _PROPOSAL_RE, "proposal_id")
        _validated(self.strategy_run_id, _STRATEGY_RUN_RE, "strategy_run_id")
        _validated(self.input_digest, _DIGEST_RE, "input_digest")
        _validated(self.output_digest, _DIGEST_RE, "output_digest")

    def attributes(self) -> dict[str, str]:
        attributes = {
            "ainvest.workflow": self.workflow.value,
            "ainvest.mode": self.mode.value,
        }
        optional = {
            "ainvest.correlation_id": self.correlation_id,
            "ainvest.causation_id": self.causation_id,
            "ainvest.proposal_id": self.proposal_id,
            "ainvest.strategy_run_id": self.strategy_run_id,
            "ainvest.input_digest": self.input_digest,
            "ainvest.output_digest": self.output_digest,
        }
        attributes.update({key: value for key, value in optional.items() if value is not None})
        return attributes


def _otel_status(code: _StatusCode) -> object:
    try:
        trace = _otel_trace()
    except ObservabilityDependencyError:
        return code.value
    return trace.Status(trace.StatusCode.OK if code is _StatusCode.OK else trace.StatusCode.ERROR)


class SafeTracer:
    """Create bounded spans without recording exceptions or arbitrary values."""

    def __init__(self, tracer: _Tracer | None = None, *, disabled: bool = False) -> None:
        if disabled:
            if tracer is not None:
                raise ValueError("disabled tracing cannot accept a tracer")
            self._tracer: _Tracer = _NullTracer()
            return
        if tracer is not None:
            self._tracer = tracer
            return
        self._tracer = cast(_Tracer, _otel_trace().get_tracer("ainvest"))

    @contextmanager
    def span(
        self,
        name: SpanName,
        metadata: TraceMetadata,
        *,
        outcome: Outcome = Outcome.SUCCESS,
    ) -> Iterator[_Span]:
        if type(name) is not SpanName:
            raise TypeError("name must be SpanName")
        if type(metadata) is not TraceMetadata:
            raise TypeError("metadata must be TraceMetadata")
        if type(outcome) is not Outcome:
            raise TypeError("outcome must be Outcome")

        with self._tracer.start_as_current_span(
            name.value,
            record_exception=False,
            set_status_on_exception=False,
        ) as active_span:
            for key, value in metadata.attributes().items():
                active_span.set_attribute(key, value)
            try:
                yield active_span
            except BaseException as exc:
                # OpenTelemetry's record_exception includes str(exc), which can
                # contain provider payloads or credentials.  Retain only type.
                exception_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
                active_span.add_event(
                    "exception",
                    {"exception.type": exception_type[:256]},
                )
                active_span.set_attribute("ainvest.outcome", Outcome.ERROR.value)
                active_span.set_status(_otel_status(_StatusCode.ERROR))
                raise
            else:
                active_span.set_attribute("ainvest.outcome", outcome.value)
                status = _StatusCode.OK if outcome is Outcome.SUCCESS else _StatusCode.ERROR
                active_span.set_status(_otel_status(status))


__all__ = ["SafeTracer", "SpanName", "TraceMetadata"]
