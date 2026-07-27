"""Correlation, causation, and idempotency identifiers for workflow tracing.

One correlation ID connects an entire trading workflow (signal → size → risk →
proposal → approval → execution → reconcile). Causation IDs link a child
command/event to its immediate parent. Idempotency IDs make command dispatch
replay-safe without relying on hidden in-memory business state.
"""

from __future__ import annotations

import secrets
import string
from typing import Annotated, Final

from pydantic import StringConstraints

from ainvest.schemas.common import DomainModel, UtcDateTime

# Opaque, URL-safe identifiers. Prefixes keep logs and audits greppable.
_ID_ALPHABET: Final[str] = string.ascii_letters + string.digits + "-_"
_ID_RANDOM_LEN: Final[int] = 22

CorrelationId = Annotated[
    str,
    StringConstraints(
        pattern=r"^corr_[A-Za-z0-9_-]{8,128}$",
        min_length=13,
        max_length=133,
    ),
]
CausationId = Annotated[
    str,
    StringConstraints(
        pattern=r"^(cmd|evt|corr)_[A-Za-z0-9_-]{8,128}$",
        min_length=12,
        max_length=133,
    ),
]
IdempotencyId = Annotated[
    str,
    StringConstraints(
        pattern=r"^idem_[A-Za-z0-9_-]{8,128}$",
        min_length=13,
        max_length=133,
    ),
]
CommandId = Annotated[
    str,
    StringConstraints(
        pattern=r"^cmd_[A-Za-z0-9_-]{8,128}$",
        min_length=12,
        max_length=133,
    ),
]
EventId = Annotated[
    str,
    StringConstraints(
        pattern=r"^evt_[A-Za-z0-9_-]{8,128}$",
        min_length=12,
        max_length=133,
    ),
]


def _random_token(length: int = _ID_RANDOM_LEN) -> str:
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(length))


def new_correlation_id() -> str:
    """Allocate a fresh workflow-scoped correlation ID."""
    return f"corr_{_random_token()}"


def new_command_id() -> str:
    """Allocate a fresh command ID (also usable as a causation parent)."""
    return f"cmd_{_random_token()}"


def new_event_id() -> str:
    """Allocate a fresh event ID (also usable as a causation parent)."""
    return f"evt_{_random_token()}"


def new_idempotency_id() -> str:
    """Allocate a fresh idempotency key for a command."""
    return f"idem_{_random_token()}"


class TraceContext(DomainModel):
    """Immutable trace triple carried on every command and result event.

    ``correlation_id`` is constant for one business workflow. ``causation_id``
    is the parent command/event that triggered this step. ``idempotency_id``
    is unique per intended side-effect and must be stable across retries.
    """

    correlation_id: CorrelationId
    causation_id: CausationId | None = None
    idempotency_id: IdempotencyId


def start_trace(*, idempotency_id: str | None = None) -> TraceContext:
    """Begin a new workflow trace (no causation parent)."""
    return TraceContext(
        correlation_id=new_correlation_id(),
        causation_id=None,
        idempotency_id=idempotency_id or new_idempotency_id(),
    )


def continue_trace(
    parent: TraceContext,
    *,
    parent_id: str,
    idempotency_id: str | None = None,
) -> TraceContext:
    """Derive a child trace that keeps correlation and records causation.

    ``parent_id`` should be the parent's ``command_id`` or ``event_id``.
    """
    return TraceContext(
        correlation_id=parent.correlation_id,
        causation_id=parent_id,
        idempotency_id=idempotency_id or new_idempotency_id(),
    )


def require_same_correlation(left: str, right: str) -> None:
    """Fail closed when two IDs claim different workflows."""
    if left != right:
        msg = f"correlation mismatch: {left!r} != {right!r}"
        raise ValueError(msg)


class TimedTrace(DomainModel):
    """Trace context plus the UTC instant the step was recorded."""

    correlation_id: CorrelationId
    causation_id: CausationId | None = None
    idempotency_id: IdempotencyId
    occurred_at: UtcDateTime


__all__ = [
    "CausationId",
    "CommandId",
    "CorrelationId",
    "EventId",
    "IdempotencyId",
    "TimedTrace",
    "TraceContext",
    "continue_trace",
    "new_command_id",
    "new_correlation_id",
    "new_event_id",
    "new_idempotency_id",
    "require_same_correlation",
    "start_trace",
]
