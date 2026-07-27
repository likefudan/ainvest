"""Retry and side-effect semantics for domain commands.

Three classes (P02-T10):

1. ``PURE_RETRYABLE`` — deterministic work on snapshotted inputs; safe to retry.
2. ``READ_ONLY_EXTERNAL`` — outbound reads (broker history, quotes); retry with
   backoff is allowed, but never treat a read as a write.
3. ``BROKER_WRITE`` — submit/cancel against a broker; **never** blind-retry.
   Reconcile by idempotency / client order ID / broker history first.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final


class RetrySemantics(StrEnum):
    """How a failed or replayed command may be retried."""

    PURE_RETRYABLE = "PURE_RETRYABLE"
    READ_ONLY_EXTERNAL = "READ_ONLY_EXTERNAL"
    BROKER_WRITE = "BROKER_WRITE"


class CommandType(StrEnum):
    """Stable command type codes for orchestrators, workers, and APIs."""

    EVALUATE_STRATEGY = "EVALUATE_STRATEGY"
    SIZE_POSITION = "SIZE_POSITION"
    EVALUATE_RISK = "EVALUATE_RISK"
    CREATE_PROPOSAL = "CREATE_PROPOSAL"
    REQUEST_APPROVAL = "REQUEST_APPROVAL"
    CONSUME_APPROVAL = "CONSUME_APPROVAL"
    EXECUTE_ORDER = "EXECUTE_ORDER"
    CANCEL_ORDER = "CANCEL_ORDER"
    RESOLVE_MANUAL_REVIEW = "RESOLVE_MANUAL_REVIEW"
    RECONCILE = "RECONCILE"


# Classification is exhaustive for every CommandType.
COMMAND_RETRY_SEMANTICS: Final[Mapping[CommandType, RetrySemantics]] = {
    CommandType.EVALUATE_STRATEGY: RetrySemantics.PURE_RETRYABLE,
    CommandType.SIZE_POSITION: RetrySemantics.PURE_RETRYABLE,
    CommandType.EVALUATE_RISK: RetrySemantics.PURE_RETRYABLE,
    CommandType.CREATE_PROPOSAL: RetrySemantics.PURE_RETRYABLE,
    # Approval challenge create/consume are durable-idempotent by key; not broker.
    CommandType.REQUEST_APPROVAL: RetrySemantics.PURE_RETRYABLE,
    CommandType.CONSUME_APPROVAL: RetrySemantics.PURE_RETRYABLE,
    CommandType.EXECUTE_ORDER: RetrySemantics.BROKER_WRITE,
    CommandType.CANCEL_ORDER: RetrySemantics.BROKER_WRITE,
    CommandType.RESOLVE_MANUAL_REVIEW: RetrySemantics.PURE_RETRYABLE,
    # Reconciliation reads broker truth; local state updates follow the read.
    CommandType.RECONCILE: RetrySemantics.READ_ONLY_EXTERNAL,
}


def retry_semantics_for(command_type: CommandType) -> RetrySemantics:
    """Return the retry class for ``command_type`` (fail closed if unknown)."""
    try:
        return COMMAND_RETRY_SEMANTICS[command_type]
    except KeyError as exc:  # pragma: no cover - enum exhaustiveness
        msg = f"unknown command type: {command_type!r}"
        raise ValueError(msg) from exc


def allows_blind_retry(command_type: CommandType) -> bool:
    """Return True only when a crash/retry may re-invoke the handler freely.

    Broker writes always return False — callers must reconcile first.
    """
    return retry_semantics_for(command_type) is not RetrySemantics.BROKER_WRITE


def is_broker_write(command_type: CommandType) -> bool:
    """Return True when the command may mutate broker state."""
    return retry_semantics_for(command_type) is RetrySemantics.BROKER_WRITE


__all__ = [
    "COMMAND_RETRY_SEMANTICS",
    "CommandType",
    "RetrySemantics",
    "allows_blind_retry",
    "is_broker_write",
    "retry_semantics_for",
]
