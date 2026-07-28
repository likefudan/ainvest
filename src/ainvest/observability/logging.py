"""Structured JSON logging with workflow correlation and fail-closed redaction.

The logging boundary deliberately accepts summaries and identifiers, not domain
payloads. Audit records remain the replayable source of truth; logs are an
operational index that can be shipped to a less-trusted telemetry system.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import logging
import re
import sys
import traceback
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from types import TracebackType
from typing import Any, Final, TextIO, cast

import structlog
from structlog.contextvars import (
    bind_contextvars,
    bound_contextvars,
    clear_contextvars,
    merge_contextvars,
)
from structlog.typing import EventDict, Processor, WrappedLogger

from ainvest.audit.digests import digest_json
from ainvest.audit.redact import REDACTED, redact

_STABLE_ID_FIELDS: Final[tuple[str, ...]] = (
    "correlation_id",
    "causation_id",
    "proposal_id",
    "strategy_run_id",
)
_logging_configured = False
_LEVELS: Final[Mapping[str, int]] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "exception": logging.ERROR,
    "critical": logging.CRITICAL,
}

# These events must bypass both ordinary level filtering and sampling because
# losing one could hide a funds-safety incident.
FUNDS_SAFETY_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "account_state_mismatch",
        "approved_order_mismatch",
        "broker_submit_unknown",
        "duplicate_order_detected",
        "kill_switch_activated",
        "unexpected_live_component",
    }
)

_FORBIDDEN_CONTENT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "approval_link",
        "approval_url",
        "chat_history",
        "messages",
        "model_input",
        "model_output",
        "model_prompt",
        "prompt",
        "raw_prompt",
        "system_prompt",
        "tool_prompt",
        "user_prompt",
    }
)
_DIGEST_ONLY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "account",
        "broker_submit_request",
        "candidate_order",
        "money_payload",
        "order",
        "order_proposal",
        "payload",
        "portfolio",
        "portfolio_snapshot",
        "proposal",
        "request_body",
        "response_body",
        "trade_payload",
    }
)
_HEADER_CONTAINER_KEYS: Final[frozenset[str]] = frozenset(
    {"headers", "http_headers", "request_headers", "response_headers"}
)
_ALLOWED_HTTP_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "accept",
        "content-type",
        "traceparent",
        "tracestate",
        "user-agent",
        "x-correlation-id",
        "x-request-id",
    }
)

# Audit redaction handles structured sensitive keys and common bearer/cookie/bot
# token formats. Logs additionally need to sanitize secrets embedded inside
# exception messages and free-form strings, where no mapping key is available.
_INLINE_SECRET_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b("
    r"api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|"
    r"password|secret|cookie|account[_ -]?(?:number|no)|session[_ -]?id"
    r")\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_OPENAI_KEY_RE: Final[re.Pattern[str]] = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_JWT_RE: Final[re.Pattern[str]] = re.compile(
    r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"
)
_APPROVAL_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\bhttps?://[^\s]*(?:approv|challenge|nonce|token)[^\s]*"
)


class _SafeBoundLogger(structlog.BoundLogger):
    """Generic structlog logger whose exception method captures ``exc_info``."""

    def exception(self, event: object | None = None, *args: object, **kw: object) -> Any:
        kw.setdefault("exc_info", True)
        return self.error(event, *args, **kw)


def _package_version() -> str:
    try:
        return importlib.metadata.version("ainvest")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _normalized_key(key: object) -> str:
    return str(key).strip().lower().replace("-", "_")


def _audit_redacts_key(key: str) -> bool:
    """Ask the shared audit policy whether a structured key is sensitive."""
    probe = redact({key: "visible-probe"})
    return isinstance(probe, dict) and probe.get(key) == REDACTED


def _sanitize_string(value: str) -> str:
    sanitized = redact(value)
    assert isinstance(sanitized, str)
    sanitized = _INLINE_SECRET_RE.sub(lambda match: f"{match.group(1)}={REDACTED}", sanitized)
    sanitized = _OPENAI_KEY_RE.sub(REDACTED, sanitized)
    sanitized = _JWT_RE.sub(REDACTED, sanitized)
    return _APPROVAL_URL_RE.sub(REDACTED, sanitized)


def _sanitize_headers(value: object) -> dict[str, object] | str:
    if not isinstance(value, Mapping):
        return REDACTED
    headers: dict[str, object] = {}
    for key, item in value.items():
        key_text = str(key)
        normalized = key_text.strip().lower()
        headers[key_text] = _sanitize(item) if normalized in _ALLOWED_HTTP_HEADERS else REDACTED
    return headers


def _exception_parts(
    value: BaseException
    | tuple[type[BaseException] | None, BaseException | None, TracebackType | None],
) -> dict[str, str]:
    if isinstance(value, BaseException):
        return _format_exception(type(value), value, value.__traceback__)

    exc_type, exc, tb = value
    if exc_type is None or exc is None:
        return {"type": "UnknownException", "message": REDACTED, "stack": REDACTED}
    return _format_exception(exc_type, exc, tb)


def _format_exception(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
) -> dict[str, str]:
    rendered = "".join(traceback.format_exception(exc_type, exc, tb))
    return {
        "type": exc_type.__name__,
        "message": _sanitize_string(str(exc)),
        "stack": _sanitize_string(rendered),
    }


def _sanitize(value: Any, *, key: str | None = None) -> Any:
    normalized = _normalized_key(key) if key is not None else None
    if key is not None and _audit_redacts_key(key):
        return REDACTED
    if normalized in _FORBIDDEN_CONTENT_KEYS:
        return REDACTED
    if normalized in _HEADER_CONTAINER_KEYS:
        return _sanitize_headers(value)
    if normalized in _DIGEST_ONLY_KEYS:
        # A digest preserves equality/correlation value without exposing
        # quantities, prices, buying power, positions, or account scope.
        return {"digest": digest_json(redact(value)), "content": REDACTED}
    if isinstance(value, BaseException):
        return _exception_parts(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize(item, key=str(item_key)) for item_key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value)
    return redact(value)


def _render_exception(event_dict: EventDict) -> None:
    exc_info = event_dict.pop("exc_info", None)
    if not exc_info:
        return
    if exc_info is True:
        current = sys.exc_info()
        event_dict["exception"] = _exception_parts(current)
    elif isinstance(exc_info, BaseException) or (
        isinstance(exc_info, tuple)
        and len(exc_info) == 3
        and (exc_info[1] is None or isinstance(exc_info[1], BaseException))
    ):
        event_dict["exception"] = _exception_parts(exc_info)
    else:
        event_dict["exception"] = REDACTED


def _context_processor(
    *,
    service: str,
    environment: str,
    version: str,
) -> Processor:
    def add_context(
        _logger: WrappedLogger,
        _method_name: str,
        event_dict: EventDict,
    ) -> EventDict:
        event_dict["service"] = service
        event_dict["environment"] = environment
        event_dict["version"] = version
        for field_name in _STABLE_ID_FIELDS:
            event_dict.setdefault(field_name, None)
        return event_dict

    return add_context


def _level_processor(min_level: int) -> Processor:
    def filter_level(
        _logger: WrappedLogger,
        method_name: str,
        event_dict: EventDict,
    ) -> EventDict:
        event_name = str(event_dict.get("event", ""))
        funds_safety = bool(event_dict.get("funds_safety")) or event_name in FUNDS_SAFETY_EVENTS
        event_dict["funds_safety"] = funds_safety
        if funds_safety:
            event_dict["level"] = "critical"
            return event_dict
        level = _LEVELS.get(method_name, logging.INFO)
        if level < min_level:
            raise structlog.DropEvent
        event_dict["level"] = "error" if method_name == "exception" else method_name
        return event_dict

    return filter_level


def _sampling_processor(sample_rate: float) -> Processor:
    def sample(
        _logger: WrappedLogger,
        _method_name: str,
        event_dict: EventDict,
    ) -> EventDict:
        if event_dict.get("funds_safety") or event_dict.get("level") in {
            "warning",
            "error",
            "critical",
        }:
            return event_dict
        if sample_rate <= 0:
            raise structlog.DropEvent
        if sample_rate >= 1:
            return event_dict
        sample_key = ":".join(
            str(event_dict.get(field) or "") for field in ("service", "correlation_id", "event")
        )
        bucket = int(hashlib.sha256(sample_key.encode("utf-8")).hexdigest(), 16) / (1 << 256)
        if bucket >= sample_rate:
            raise structlog.DropEvent
        return event_dict

    return sample


def _redaction_processor(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    _render_exception(event_dict)
    sanitized = _sanitize(event_dict)
    assert isinstance(sanitized, dict)
    return sanitized


def configure_logging(
    *,
    service: str = "ainvest",
    environment: str = "development",
    version: str | None = None,
    level: int = logging.INFO,
    sample_rate: float = 1.0,
    stream: TextIO | None = None,
) -> None:
    """Configure process-wide structlog JSON output.

    ``sample_rate`` is deterministic per service/correlation/event and applies
    only below warning. Funds-safety events bypass both sampling and the
    configured minimum level.
    """
    global _logging_configured

    if not service.strip() or not environment.strip():
        raise ValueError("service and environment must be non-empty")
    if not 0 <= sample_rate <= 1:
        raise ValueError("sample_rate must be between 0 and 1")
    if level not in _LEVELS.values():
        raise ValueError("level must be a standard Python logging level")

    structlog.configure(
        processors=[
            merge_contextvars,
            _context_processor(
                service=service.strip(),
                environment=environment.strip(),
                version=version or _package_version(),
            ),
            _level_processor(level),
            _sampling_processor(sample_rate),
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            _redaction_processor,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=stream),
        wrapper_class=_SafeBoundLogger,
        cache_logger_on_first_use=False,
    )
    _logging_configured = True


def get_logger(component: str | None = None) -> structlog.BoundLogger:
    """Return a logger optionally bound to a non-secret component name."""
    if not _logging_configured:
        configure_logging()
    logger = structlog.get_logger(component=component) if component else structlog.get_logger()
    return cast(structlog.BoundLogger, logger)


def bind_log_context(
    *,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    proposal_id: str | None = None,
    strategy_run_id: str | None = None,
) -> None:
    """Bind stable workflow identifiers to the current async/thread context."""
    bind_contextvars(
        **_workflow_context(
            correlation_id=correlation_id,
            causation_id=causation_id,
            proposal_id=proposal_id,
            strategy_run_id=strategy_run_id,
        )
    )


def clear_log_context() -> None:
    """Clear all logging context variables in the current context."""
    clear_contextvars()


def _workflow_context(
    *,
    correlation_id: str | None,
    causation_id: str | None,
    proposal_id: str | None,
    strategy_run_id: str | None,
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "proposal_id": proposal_id,
            "strategy_run_id": strategy_run_id,
        }.items()
        if value is not None
    }


@contextmanager
def log_context(
    *,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    proposal_id: str | None = None,
    strategy_run_id: str | None = None,
) -> Iterator[None]:
    """Temporarily bind workflow identifiers and restore prior values on exit."""
    with bound_contextvars(
        **_workflow_context(
            correlation_id=correlation_id,
            causation_id=causation_id,
            proposal_id=proposal_id,
            strategy_run_id=strategy_run_id,
        )
    ):
        yield


__all__ = [
    "FUNDS_SAFETY_EVENTS",
    "bind_log_context",
    "clear_log_context",
    "configure_logging",
    "get_logger",
    "log_context",
]
