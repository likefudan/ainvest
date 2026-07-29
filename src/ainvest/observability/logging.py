"""Structured JSON logging with workflow correlation and fail-closed redaction.

The logging boundary deliberately accepts summaries and identifiers, not domain
payloads. Audit records remain the replayable source of truth; logs are an
operational index that can be shipped to a less-trusted telemetry system.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import re
import sys
import traceback
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, Final, TextIO, cast

import structlog
from structlog.contextvars import (
    bind_contextvars,
    bound_contextvars,
    clear_contextvars,
    get_contextvars,
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
    "client_order_id",
    "broker_order_id",
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
        "blind_retry_blocked",
        "broker_submit_unknown",
        "consume_challenge",
        "create_challenge",
        "create_proposal",
        "duplicate_order_detected",
        "evaluate_pretrade",
        "evaluate_risk",
        "execute_order",
        "inject_market_event",
        "kill_switch_activated",
        "pretrade_rejected",
        "reconcile",
        "reconcile_after_unknown",
        "risk_rejected",
        "size_position",
        "unexpected_live_component",
    }
)

_MAX_SANITIZE_DEPTH: Final[int] = 10
_MAX_COLLECTION_ITEMS: Final[int] = 64
_MAX_STRING_CHARS: Final[int] = 2_048
_CYCLE: Final[str] = "<cycle>"
_TRUNCATED: Final[str] = "<truncated>"
_UNAVAILABLE: Final[str] = "<unavailable>"

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


class _NoRaisePrintLogger(structlog.PrintLogger):
    """Print logger that never lets a telemetry sink break business control flow."""

    def msg(self, message: str) -> None:
        try:
            super().msg(message)
        except Exception:
            return

    log = debug = info = warn = warning = msg
    fatal = failure = err = error = critical = exception = msg


class _NoRaisePrintLoggerFactory:
    def __init__(self, file: TextIO | None = None) -> None:
        self._file = file

    def __call__(self, *_args: object) -> _NoRaisePrintLogger:
        return _NoRaisePrintLogger(self._file)


@dataclass(slots=True)
class _SanitizeState:
    active_ids: set[int] = field(default_factory=set)


def _package_version() -> str:
    try:
        return importlib.metadata.version("ainvest")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _normalized_key(key: object) -> str:
    if type(key) is not str:
        return ""
    return key.strip().lower().replace("-", "_")


def is_funds_safety_event(event: object, *, explicit: object = False) -> bool:
    """Return whether an event must bypass ordinary level and sampling policy."""
    if explicit is True:
        return True
    return type(event) is str and event in FUNDS_SAFETY_EVENTS


def _audit_redacts_key(key: str) -> bool:
    """Ask the shared audit policy whether a structured key is sensitive."""
    try:
        probe = redact({key: "visible-probe"})
        return isinstance(probe, dict) and probe.get(key) == REDACTED
    except Exception:
        return True


def _sanitize_string(value: str) -> str:
    if type(value) is not str:
        return _UNAVAILABLE
    try:
        bounded = value[:_MAX_STRING_CHARS]
        sanitized = redact(bounded)
        if type(sanitized) is not str:
            return _UNAVAILABLE
        sanitized = _INLINE_SECRET_RE.sub(
            lambda match: f"{match.group(1)}={REDACTED}",
            sanitized,
        )
        sanitized = _OPENAI_KEY_RE.sub(REDACTED, sanitized)
        sanitized = _JWT_RE.sub(REDACTED, sanitized)
        sanitized = _APPROVAL_URL_RE.sub(REDACTED, sanitized)
        if len(value) > _MAX_STRING_CHARS:
            return sanitized + _TRUNCATED
        return sanitized
    except Exception:
        return _UNAVAILABLE


def _type_placeholder(value: object) -> str:
    try:
        name = type(value).__name__
    except Exception:
        name = "object"
    return f"<{name}>"


def _sanitize_key(key: object, *, index: int) -> tuple[str, str | None]:
    if type(key) is not str:
        return f"<key:{_type_placeholder(key)}:{index}>", None
    sanitized = _sanitize_string(key)
    if sanitized != key:
        return f"{REDACTED}_KEY_{index}", key
    return sanitized, key


def _mapping_items(value: Mapping[object, object]) -> tuple[list[tuple[object, object]], bool]:
    items: list[tuple[object, object]] = []
    try:
        iterator = iter(value.items())
    except Exception:
        return items, True
    for _index in range(_MAX_COLLECTION_ITEMS):
        try:
            items.append(next(iterator))
        except StopIteration:
            return items, False
        except Exception:
            return items, True
    try:
        next(iterator)
    except StopIteration:
        return items, False
    except Exception:
        return items, True
    return items, True


def _sequence_items(value: Sequence[object]) -> tuple[list[object], bool]:
    items: list[object] = []
    try:
        iterator = iter(value)
    except Exception:
        return items, True
    for _index in range(_MAX_COLLECTION_ITEMS):
        try:
            items.append(next(iterator))
        except StopIteration:
            return items, False
        except Exception:
            return items, True
    try:
        next(iterator)
    except StopIteration:
        return items, False
    except Exception:
        return items, True
    return items, True


def _sanitize_headers(value: object) -> dict[str, object] | str:
    if not isinstance(value, Mapping):
        return REDACTED
    headers: dict[str, object] = {}
    items, truncated = _mapping_items(value)
    for index, (key, item) in enumerate(items):
        safe_key, original_key = _sanitize_key(key, index=index)
        normalized = original_key.strip().lower() if original_key is not None else ""
        headers[safe_key] = _sanitize(item) if normalized in _ALLOWED_HTTP_HEADERS else REDACTED
    if truncated:
        headers[_TRUNCATED] = REDACTED
    return headers


def _safe_traceback(
    tb: TracebackType | None,
) -> list[dict[str, str | int]]:
    frames: list[dict[str, str | int]] = []
    if tb is None:
        return frames
    try:
        iterator = traceback.walk_tb(tb)
        for index, (frame, line_number) in enumerate(iterator):
            if index >= _MAX_COLLECTION_ITEMS:
                frames.append({"frame": _TRUNCATED, "line": 0})
                break
            frames.append(
                {
                    "file": _sanitize_string(frame.f_code.co_filename),
                    "function": _sanitize_string(frame.f_code.co_name),
                    "line": line_number,
                }
            )
    except Exception:
        frames.append({"frame": _UNAVAILABLE, "line": 0})
    return frames


def _sanitize_exception(
    exc: BaseException,
    *,
    tb: TracebackType | None,
    state: _SanitizeState,
    depth: int,
) -> dict[str, Any]:
    exc_id = id(exc)
    type_name = _type_placeholder(exc).strip("<>")
    if depth >= _MAX_SANITIZE_DEPTH:
        return {"type": type_name, "detail": _TRUNCATED}
    if exc_id in state.active_ids:
        return {"type": type_name, "detail": _CYCLE}

    state.active_ids.add(exc_id)
    try:
        try:
            raw_args = exc.args
        except Exception:
            raw_args = (_UNAVAILABLE,)
        safe_args = _sanitize(raw_args, state=state, depth=depth + 1)
        try:
            message = json.dumps(
                safe_args,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )[:_MAX_STRING_CHARS]
        except Exception:
            message = _UNAVAILABLE
        result: dict[str, Any] = {
            "type": type_name,
            "args": safe_args,
            "message": message,
            "stack": _safe_traceback(tb),
        }

        try:
            notes = getattr(exc, "__notes__", None)
        except Exception:
            notes = _UNAVAILABLE
        if notes:
            result["notes"] = _sanitize(notes, state=state, depth=depth + 1)

        try:
            cause = exc.__cause__
            context = exc.__context__
            suppress_context = exc.__suppress_context__
        except Exception:
            cause = context = None
            suppress_context = True
        if isinstance(cause, BaseException):
            result["cause"] = _sanitize_exception(
                cause,
                tb=cause.__traceback__,
                state=state,
                depth=depth + 1,
            )
        elif isinstance(context, BaseException) and not suppress_context:
            result["context"] = _sanitize_exception(
                context,
                tb=context.__traceback__,
                state=state,
                depth=depth + 1,
            )
        return result
    except Exception:
        return {"type": type_name, "detail": _UNAVAILABLE}
    finally:
        state.active_ids.discard(exc_id)


def _sanitize(
    value: Any,
    *,
    key: str | None = None,
    state: _SanitizeState | None = None,
    depth: int = 0,
) -> Any:
    current_state = state or _SanitizeState()
    try:
        if depth >= _MAX_SANITIZE_DEPTH:
            return _TRUNCATED
        normalized = _normalized_key(key) if key is not None else None
        if key is not None and _audit_redacts_key(key):
            return REDACTED
        if normalized in _FORBIDDEN_CONTENT_KEYS:
            return REDACTED
        if normalized in _HEADER_CONTAINER_KEYS:
            return _sanitize_headers(value)
        if normalized in _DIGEST_ONLY_KEYS:
            safe_value = _sanitize(
                value,
                state=current_state,
                depth=depth + 1,
            )
            try:
                digest = digest_json(safe_value)
            except Exception:
                digest = "sha256:" + ("0" * 64)
            return {"digest": digest, "content": REDACTED}
        if isinstance(value, BaseException):
            return _sanitize_exception(
                value,
                tb=value.__traceback__,
                state=current_state,
                depth=depth,
            )
        if value is None or type(value) in {bool, int, float}:
            return value
        if type(value) is str:
            return _sanitize_string(value)
        if isinstance(value, str):
            return _UNAVAILABLE
        if isinstance(value, (bytes, bytearray)):
            try:
                return f"<bytes:{len(value)}>"
            except Exception:
                return "<bytes>"
        if isinstance(value, Decimal):
            try:
                return format(value, "f")
            except Exception:
                return _UNAVAILABLE
        if isinstance(value, datetime):
            try:
                return value.isoformat()
            except Exception:
                return _UNAVAILABLE
        if isinstance(value, date):
            try:
                return value.isoformat()
            except Exception:
                return _UNAVAILABLE
        if isinstance(value, Mapping):
            value_id = id(value)
            if value_id in current_state.active_ids:
                return _CYCLE
            current_state.active_ids.add(value_id)
            try:
                mapping_items, mapping_truncated = _mapping_items(value)
                mapping_result: dict[str, Any] = {}
                for index, (item_key, item) in enumerate(mapping_items):
                    safe_key, original_key = _sanitize_key(item_key, index=index)
                    while safe_key in mapping_result:
                        safe_key += f"_{index}"
                    mapping_result[safe_key] = _sanitize(
                        item,
                        key=original_key,
                        state=current_state,
                        depth=depth + 1,
                    )
                if mapping_truncated:
                    mapping_result[_TRUNCATED] = REDACTED
                return mapping_result
            finally:
                current_state.active_ids.discard(value_id)
        if isinstance(value, Sequence):
            value_id = id(value)
            if value_id in current_state.active_ids:
                return _CYCLE
            current_state.active_ids.add(value_id)
            try:
                sequence_items, sequence_truncated = _sequence_items(value)
                sequence_result = [
                    _sanitize(item, state=current_state, depth=depth + 1) for item in sequence_items
                ]
                if sequence_truncated:
                    sequence_result.append(_TRUNCATED)
                return sequence_result
            finally:
                current_state.active_ids.discard(value_id)
        return _type_placeholder(value)
    except Exception:
        return _UNAVAILABLE


def _render_exception(event_dict: EventDict) -> None:
    try:
        exc_info = event_dict.pop("exc_info", None)
        if not exc_info:
            return
        if exc_info is True:
            _exc_type, exc, tb = sys.exc_info()
            if exc is None:
                event_dict["exception"] = {
                    "type": "UnknownException",
                    "detail": _UNAVAILABLE,
                }
            else:
                event_dict["exception"] = _sanitize_exception(
                    exc,
                    tb=tb,
                    state=_SanitizeState(),
                    depth=0,
                )
        elif isinstance(exc_info, BaseException):
            event_dict["exception"] = _sanitize_exception(
                exc_info,
                tb=exc_info.__traceback__,
                state=_SanitizeState(),
                depth=0,
            )
        elif type(exc_info) is tuple and len(exc_info) == 3:
            exc = exc_info[1]
            tb = exc_info[2]
            if isinstance(exc, BaseException) and (tb is None or isinstance(tb, TracebackType)):
                event_dict["exception"] = _sanitize_exception(
                    exc,
                    tb=tb,
                    state=_SanitizeState(),
                    depth=0,
                )
            else:
                event_dict["exception"] = REDACTED
        else:
            event_dict["exception"] = REDACTED
    except Exception:
        event_dict["exception"] = {"type": "UnknownException", "detail": _UNAVAILABLE}


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
        event_name = event_dict.get("event")
        funds_safety = is_funds_safety_event(
            event_name,
            explicit=event_dict.get("funds_safety"),
        )
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
    def sample_part(value: object) -> str:
        if type(value) is str:
            return value[:256]
        if type(value) in {bool, int, float} or value is None:
            return repr(value)
        return _UNAVAILABLE

    def sample(
        _logger: WrappedLogger,
        _method_name: str,
        event_dict: EventDict,
    ) -> EventDict:
        if event_dict.get("funds_safety") is True or event_dict.get("level") in {
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
            sample_part(event_dict.get(field)) for field in ("service", "correlation_id", "event")
        )
        bucket = int(hashlib.sha256(sample_key.encode("utf-8")).hexdigest(), 16) / (1 << 256)
        if bucket >= sample_rate:
            raise structlog.DropEvent
        return event_dict

    return sample


_SAFE_EVENT_CODE_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SAFE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


def _safe_fallback_field(value: object, *, identifier: bool = False) -> str | None:
    if type(value) is not str:
        return None
    matcher = _SAFE_ID_RE if identifier else _SAFE_EVENT_CODE_RE
    return value if matcher.fullmatch(value) else None


def _fallback_event(event_dict: EventDict) -> EventDict:
    original_event = event_dict.get("event")
    funds_safety = is_funds_safety_event(
        original_event,
        explicit=event_dict.get("funds_safety"),
    )
    event_name = _safe_fallback_field(original_event) or "logging_sanitization_failed"
    fallback: EventDict = {
        "event": event_name,
        "level": "critical" if funds_safety else "error",
        "funds_safety": funds_safety,
        "logging_error_code": "LOG_SANITIZATION_FAILED",
    }
    for field_name in ("service", "environment", "version", "timestamp"):
        fallback[field_name] = _safe_fallback_field(event_dict.get(field_name), identifier=True)
    for field_name in _STABLE_ID_FIELDS:
        fallback[field_name] = _safe_fallback_field(
            event_dict.get(field_name),
            identifier=True,
        )
    return fallback


def _redaction_processor(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    try:
        _render_exception(event_dict)
        sanitized = _sanitize(event_dict)
        if not isinstance(sanitized, dict):
            return _fallback_event(event_dict)
        return sanitized
    except Exception:
        return _fallback_event(event_dict)


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
        logger_factory=_NoRaisePrintLoggerFactory(file=stream),
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
    client_order_id: str | None = None,
    broker_order_id: str | None = None,
) -> None:
    """Bind stable workflow identifiers to the current async/thread context."""
    bind_contextvars(
        **_workflow_context(
            correlation_id=correlation_id,
            causation_id=causation_id,
            proposal_id=proposal_id,
            strategy_run_id=strategy_run_id,
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
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
    client_order_id: str | None,
    broker_order_id: str | None,
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "proposal_id": proposal_id,
            "strategy_run_id": strategy_run_id,
            "client_order_id": client_order_id,
            "broker_order_id": broker_order_id,
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
    client_order_id: str | None = None,
    broker_order_id: str | None = None,
) -> Iterator[None]:
    """Temporarily bind workflow identifiers and restore prior values on exit."""
    with bound_contextvars(
        **_workflow_context(
            correlation_id=correlation_id,
            causation_id=causation_id,
            proposal_id=proposal_id,
            strategy_run_id=strategy_run_id,
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
        )
    ):
        yield


@contextmanager
def isolated_log_context(
    *,
    correlation_id: str,
    strategy_run_id: str | None = None,
) -> Iterator[None]:
    """Run one workflow without inheriting another workflow's stable IDs."""
    previous = get_contextvars()
    clear_contextvars()
    bind_contextvars(**dict.fromkeys(_STABLE_ID_FIELDS))
    bind_contextvars(
        correlation_id=correlation_id,
        strategy_run_id=strategy_run_id,
    )
    try:
        yield
    finally:
        clear_contextvars()
        bind_contextvars(**previous)


__all__ = [
    "FUNDS_SAFETY_EVENTS",
    "bind_log_context",
    "clear_log_context",
    "configure_logging",
    "get_logger",
    "is_funds_safety_event",
    "isolated_log_context",
    "log_context",
]
