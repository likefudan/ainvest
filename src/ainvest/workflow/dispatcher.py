"""Command dispatcher: in-process now, durable-queue/Temporal-ready later.

The dispatcher is the only place that remembers prior command results for
idempotent replay. Handlers must be pure with respect to business outcomes:
given the same command envelope they return the same business result. They
must not hide workflow state in process-local globals.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from typing import Protocol, runtime_checkable

from ainvest.workflow.commands import WorkflowCommand
from ainvest.workflow.events import (
    COMMAND_TO_EVENT_TYPE,
    CommandOutcome,
    CommandRejectedEvent,
    EventType,
    WorkflowEvent,
)
from ainvest.workflow.ids import require_same_correlation
from ainvest.workflow.semantics import CommandType, is_broker_write

_UNCERTAIN_BROKER_OUTCOMES = frozenset(
    {
        CommandOutcome.UNKNOWN,
        CommandOutcome.SUBMIT_UNKNOWN,
        CommandOutcome.CANCEL_UNKNOWN,
    }
)

# Per-attempt transport metadata: must not affect idempotent business identity.
_DIGEST_EXCLUDE_FIELDS = frozenset({"command_id", "issued_at"})


class DuplicateCommandError(ValueError):
    """Raised when an idempotency key is reused with a different command body."""


class UnknownCommandHandlerError(KeyError):
    """Raised when no handler is registered for a command type."""


class BlindBrokerRetryError(RuntimeError):
    """Raised when a broker-write would be blindly retried after uncertainty.

    True idempotent replay (same idempotency ID + same command digest) is
    handled by the dispatcher and returns the stored event. After an uncertain
    broker outcome, orchestrators must dispatch ``ReconcileCommand`` before any
    new broker write — never mint a fresh idempotency ID for the same order.
    """


@runtime_checkable
class IdempotencyStore(Protocol):
    """Durable (or test-double) store for command → event idempotent replay.

    Production deployments should back this with the outbox / DB. The default
    in-process implementation is for tests and single-process Paper MVP only.
    """

    def get(self, idempotency_id: str) -> tuple[str, WorkflowEvent] | None:
        """Return ``(command_digest, event)`` when the key was seen, else None."""

    def put(self, idempotency_id: str, command_digest: str, event: WorkflowEvent) -> None:
        """Record the first successful result for ``idempotency_id``."""


@runtime_checkable
class CommandDispatcher(Protocol):
    """Port preserved for a future durable queue or Temporal worker.

    Current implementation: :class:`InProcessCommandDispatcher`.
    """

    def dispatch(self, command: WorkflowCommand) -> WorkflowEvent:
        """Execute or replay ``command``; return the business result event."""

    def register(
        self,
        command_type: CommandType,
        handler: Callable[[WorkflowCommand], WorkflowEvent],
    ) -> None:
        """Bind a handler for ``command_type``."""


class InMemoryIdempotencyStore:
    """Explicit process-local store for tests / single-process Paper MVP.

    This is *not* hidden business state inside handlers — it is the dispatcher
    port. Multi-process or crash-safe deployments must replace it with a
    durable implementation.
    """

    def __init__(self) -> None:
        self._entries: MutableMapping[str, tuple[str, WorkflowEvent]] = {}

    def get(self, idempotency_id: str) -> tuple[str, WorkflowEvent] | None:
        return self._entries.get(idempotency_id)

    def put(self, idempotency_id: str, command_digest: str, event: WorkflowEvent) -> None:
        existing = self._entries.get(idempotency_id)
        if existing is not None:
            prior_digest, prior_event = existing
            if prior_digest != command_digest:
                raise DuplicateCommandError(
                    f"idempotency_id {idempotency_id!r} reused with different command"
                )
            if prior_event != event:
                raise DuplicateCommandError(
                    f"idempotency_id {idempotency_id!r} already bound to a different event"
                )
            return
        self._entries[idempotency_id] = (command_digest, event)

    def __len__(self) -> int:
        return len(self._entries)


def command_digest(command: WorkflowCommand) -> str:
    """Stable digest of business intent for idempotency conflict detection.

    Excludes attempt-scoped transport fields (``command_id``, ``issued_at``) so a
    retry that keeps ``idempotency_id`` and the business payload but mints a new
    ``command_id`` still replays. Uses canonical JSON for stable encoding.
    """
    from ainvest.audit.digests import digest_json

    payload = command.model_dump(mode="json")
    for key in _DIGEST_EXCLUDE_FIELDS:
        payload.pop(key, None)
    return digest_json(payload)


def ensure_not_blind_broker_retry(
    command_type: CommandType,
    *,
    prior_outcome: CommandOutcome | None,
) -> None:
    """Fail closed before a new broker write after an uncertain prior outcome.

    Idempotent replay of the *same* command is allowed via the dispatcher store.
    This guard is for orchestrators considering a *new* write attempt.
    """
    if prior_outcome is None or not is_broker_write(command_type):
        return
    if prior_outcome in _UNCERTAIN_BROKER_OUTCOMES:
        raise BlindBrokerRetryError(
            f"refusing blind retry of {command_type.value} after {prior_outcome.value}; "
            "reconcile by idempotency/client order ID first"
        )


class InProcessCommandDispatcher:
    """Synchronous in-process bus with explicit idempotent replay.

    Interface-compatible with a future Temporal/queue worker: register handlers
    by :class:`CommandType`, dispatch envelopes, get result events. No Temporal
    or durable-queue dependency is introduced here.
    """

    def __init__(
        self,
        *,
        store: IdempotencyStore | None = None,
        handlers: Mapping[CommandType, Callable[[WorkflowCommand], WorkflowEvent]] | None = None,
    ) -> None:
        self._store: IdempotencyStore = store if store is not None else InMemoryIdempotencyStore()
        self._handlers: dict[CommandType, Callable[[WorkflowCommand], WorkflowEvent]] = dict(
            handlers or {}
        )

    def register(
        self,
        command_type: CommandType,
        handler: Callable[[WorkflowCommand], WorkflowEvent],
    ) -> None:
        self._handlers[command_type] = handler

    def dispatch(self, command: WorkflowCommand) -> WorkflowEvent:
        digest = command_digest(command)
        prior = self._store.get(command.idempotency_id)
        if prior is not None:
            prior_digest, prior_event = prior
            if prior_digest != digest:
                raise DuplicateCommandError(
                    f"idempotency_id {command.idempotency_id!r} reused with different command"
                )
            require_same_correlation(command.correlation_id, prior_event.correlation_id)
            return prior_event

        handler = self._handlers.get(command.command_type)
        if handler is None:
            raise UnknownCommandHandlerError(command.command_type)

        event = handler(command)
        _validate_handler_result(command, event)
        self._store.put(command.idempotency_id, digest, event)
        return event


def _validate_handler_result(command: WorkflowCommand, event: WorkflowEvent) -> None:
    """Fail closed when a handler breaks the correlation/causation/type contract."""
    require_same_correlation(command.correlation_id, event.correlation_id)
    if event.idempotency_id != command.idempotency_id:
        msg = f"event idempotency_id {event.idempotency_id!r} != command {command.idempotency_id!r}"
        raise ValueError(msg)
    if event.causation_id != command.command_id:
        msg = f"event causation_id {event.causation_id!r} != command_id {command.command_id!r}"
        raise ValueError(msg)

    expected = COMMAND_TO_EVENT_TYPE[command.command_type]
    if event.event_type is EventType.COMMAND_REJECTED:
        if not isinstance(event, CommandRejectedEvent):
            raise ValueError("COMMAND_REJECTED event_type requires CommandRejectedEvent")
        if event.command_type is not command.command_type:
            raise ValueError(
                f"COMMAND_REJECTED command_type {event.command_type.value} "
                f"!= dispatched {command.command_type.value}"
            )
        return
    if event.event_type is not expected:
        raise ValueError(
            f"handler returned {event.event_type.value} for {command.command_type.value}; "
            f"expected {expected.value} or COMMAND_REJECTED"
        )


__all__ = [
    "BlindBrokerRetryError",
    "CommandDispatcher",
    "DuplicateCommandError",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "InProcessCommandDispatcher",
    "UnknownCommandHandlerError",
    "command_digest",
    "ensure_not_blind_broker_retry",
]
