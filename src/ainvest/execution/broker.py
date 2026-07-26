"""Broker read/write port and error taxonomy (P03-T13).

Paper and Robinhood adapters share these domain protocols. MCP session details
must not leak into business logic.

Hard rules (design.md §5.6, DEC-007):

- Read capability is a separate protocol from write capability so read-only
  processes cannot receive submit/cancel.
- Submit and cancel require distinct idempotency / client-order identifiers.
- There is **no** in-place replace operation. A replacement is always:
  cancel the working order, then create a new approved proposal (new risk
  decision, new ``order_hash``, new human approval).
- Confirmed rejection and unknown write outcomes are distinct; unknown must
  reconcile before any further write attempt (never blind retry).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, ClassVar, Final, Literal, Protocol, runtime_checkable

from pydantic import StringConstraints, model_validator

from ainvest.schemas.approval import (
    ApprovalEvent,
    ApprovalEventOutcome,
    ApprovalScope,
)
from ainvest.schemas.broker import (
    BrokerFill,
    BrokerOrder,
    CancelCommand,
    CancelResult,
    CancelStatus,
)
from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    DomainModel,
    MachineCode,
    SchemaVersion,
    UtcDateTime,
)
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.orders import OrderProposal
from ainvest.schemas.portfolio import AccountScope, PortfolioSnapshot, PositionSnapshot

# ---------------------------------------------------------------------------
# Stable error taxonomy
# ---------------------------------------------------------------------------

BrokerErrorCode = Literal[
    "AUTH",
    "TIMEOUT",
    "RATE_LIMIT",
    "INVALID_ORDER",
    "REJECTED",
    "UNKNOWN_OUTCOME",
]

# Paper approvals authorize paper accounts only. Live (webauthn) approvals
# authorize the Robinhood agentic account scope (design.md §6.3 / rule 26).
_APPROVAL_SCOPE_TO_ACCOUNT: Final[Mapping[ApprovalScope, AccountScope]] = {
    ApprovalScope.PAPER: AccountScope.PAPER,
    ApprovalScope.LIVE: AccountScope.AGENTIC,
}


class BrokerError(Exception):
    """Base broker failure with a stable machine-readable code.

    Control flow must branch on :attr:`code` (or exception subclass), never on
    parsed human message text. Instantiate a concrete subclass — never this base.
    """

    code: ClassVar[BrokerErrorCode]
    reason_code: MachineCode

    def __init__(
        self,
        message: str,
        *,
        reason_code: MachineCode,
        details: Mapping[str, str] | None = None,
    ) -> None:
        if type(self) is BrokerError:
            raise TypeError("BrokerError is abstract; instantiate a concrete subclass")
        super().__init__(message)
        self.reason_code = reason_code
        self.details: Mapping[str, str] = dict(details or {})

    @property
    def is_confirmed_rejection(self) -> bool:
        """True only for a broker-confirmed rejection (safe terminal failure)."""
        return self.code == "REJECTED"

    @property
    def is_unknown_outcome(self) -> bool:
        """True when the write may have been applied; must reconcile first."""
        return self.code == "UNKNOWN_OUTCOME"


class BrokerAuthError(BrokerError):
    """Authentication or authorization failure against the broker."""

    code: ClassVar[BrokerErrorCode] = "AUTH"


class BrokerTimeoutError(BrokerError):
    """Deadline exceeded on a **read** (or pre-flight) call.

    Write-path timeouts must surface as :class:`BrokerUnknownOutcomeError` (or
    a result with outcome ``UNKNOWN``), never as a confirmed rejection.
    """

    code: ClassVar[BrokerErrorCode] = "TIMEOUT"


class BrokerRateLimitError(BrokerError):
    """Broker rate limit / back-pressure."""

    code: ClassVar[BrokerErrorCode] = "RATE_LIMIT"


class BrokerInvalidOrderError(BrokerError):
    """Locally invalid order or cancel request rejected before broker write."""

    code: ClassVar[BrokerErrorCode] = "INVALID_ORDER"


class BrokerRejectedError(BrokerError):
    """Broker **confirmed** that the submit or cancel was rejected.

    Distinct from :class:`BrokerUnknownOutcomeError`. Callers may treat this as
    a terminal negative result without reconciliation.
    """

    code: ClassVar[BrokerErrorCode] = "REJECTED"


class BrokerUnknownOutcomeError(BrokerError):
    """Write result is ambiguous (timeout, disconnect, non-idempotent reply).

    Must **not** trigger an immediate resubmit or re-cancel. Reconcile by
    client order ID / cancel idempotency key and broker history first.
    """

    code: ClassVar[BrokerErrorCode] = "UNKNOWN_OUTCOME"

    def __init__(
        self,
        message: str,
        *,
        reason_code: MachineCode,
        operation: Literal["submit", "cancel"],
        idempotency_key: str,
        details: Mapping[str, str] | None = None,
    ) -> None:
        merged = dict(details or {})
        merged.setdefault("operation", operation)
        merged.setdefault("idempotency_key", idempotency_key)
        super().__init__(message, reason_code=reason_code, details=merged)
        self.operation: Literal["submit", "cancel"] = operation
        self.idempotency_key = idempotency_key


# ---------------------------------------------------------------------------
# Write request / result models (domain schemas; no MCP types)
# ---------------------------------------------------------------------------

ClientOrderId = Annotated[str, StringConstraints(min_length=3, max_length=128)]


class BrokerSubmitOutcome(StrEnum):
    """Terminal classification of a submit attempt at the port boundary."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class BrokerSubmitRequest(DomainModel):
    """Validated submit intent for the write capability.

    ``client_order_id`` is the **submit** idempotency key. Cancel uses a
    distinct ``CancelCommand.idempotency_key``; the two must never be reused for
    the opposite operation.

    Replacement is not supported on this port. To change a working order:
    cancel it, then submit a **new** approved proposal (DEC-007).
    """

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    proposal: OrderProposal
    approval: ApprovalEvent
    client_order_id: ClientOrderId

    @model_validator(mode="after")
    def _approval_binds_proposal(self) -> BrokerSubmitRequest:
        if self.approval.outcome is not ApprovalEventOutcome.APPROVED:
            raise ValueError("submit requires an APPROVED approval event")
        if self.approval.proposal_id != self.proposal.proposal_id:
            raise ValueError("approval.proposal_id must match proposal.proposal_id")
        if self.approval.order_hash != self.proposal.order_hash:
            raise ValueError("approval.order_hash must match proposal.order_hash")
        expected_account = _APPROVAL_SCOPE_TO_ACCOUNT[self.approval.scope]
        if self.proposal.account_scope is not expected_account:
            raise ValueError(
                "approval.scope "
                f"{self.approval.scope.value!r} requires proposal.account_scope "
                f"{expected_account.value!r}, got {self.proposal.account_scope.value!r}"
            )
        return self


class BrokerSubmitResult(DomainModel):
    """Submit outcome distinguishing acceptance, rejection, and unknown."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    outcome: BrokerSubmitOutcome
    client_order_id: ClientOrderId
    observed_at: UtcDateTime
    broker_order: BrokerOrder | None = None
    reason_code: MachineCode | None = None

    @model_validator(mode="after")
    def _outcome_consistency(self) -> BrokerSubmitResult:
        if self.outcome is BrokerSubmitOutcome.ACCEPTED:
            if self.broker_order is None:
                raise ValueError("ACCEPTED submit requires broker_order")
            if self.broker_order.client_order_id != self.client_order_id:
                raise ValueError("broker_order.client_order_id must match client_order_id")
        elif self.outcome is BrokerSubmitOutcome.REJECTED:
            if self.broker_order is not None:
                raise ValueError("REJECTED submit must not include broker_order")
            if self.reason_code is None:
                raise ValueError("REJECTED submit requires reason_code")
        elif self.outcome is BrokerSubmitOutcome.UNKNOWN:
            # May optionally include a partial broker_order if the adapter saw
            # an ack before disconnect; callers must still reconcile.
            if self.reason_code is None:
                raise ValueError("UNKNOWN submit requires reason_code")
        return self

    @property
    def is_confirmed_rejection(self) -> bool:
        return self.outcome is BrokerSubmitOutcome.REJECTED

    @property
    def is_unknown_outcome(self) -> bool:
        return self.outcome is BrokerSubmitOutcome.UNKNOWN


def cancel_is_confirmed_rejection(result: CancelResult) -> bool:
    """True when the cancel command was definitively rejected by the broker."""
    return result.status is CancelStatus.REJECTED


def cancel_is_unknown_outcome(result: CancelResult) -> bool:
    """True when cancel application is ambiguous and must be reconciled."""
    return result.status is CancelStatus.UNKNOWN


# Method names intentionally absent from the read protocol / write capability.
FORBIDDEN_REPLACE_METHOD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "replace",
        "replace_order",
        "modify",
        "modify_order",
        "amend",
        "amend_order",
        "update_order",
    }
)

WRITE_METHOD_NAMES: Final[frozenset[str]] = frozenset({"submit", "cancel"})
READ_METHOD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "get_account",
        "get_positions",
        "get_quotes",
        "get_orders",
        "get_fills",
    }
)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class BrokerReadPort(Protocol):
    """Read-only broker observations for research, risk, and reconciliation.

    Implementations of this protocol alone must not expose submit/cancel.
    """

    def get_account(self, account_scope: AccountScope) -> PortfolioSnapshot:
        """Return the latest account / portfolio snapshot for ``account_scope``."""
        ...

    def get_positions(self, account_scope: AccountScope) -> tuple[PositionSnapshot, ...]:
        """Return open positions for ``account_scope``."""
        ...

    def get_quotes(self, instrument_ids: tuple[str, ...]) -> tuple[MarketQuote, ...]:
        """Return quotes keyed by canonical broker instrument IDs."""
        ...

    def get_orders(
        self,
        account_scope: AccountScope,
        *,
        broker_order_ids: tuple[str, ...] | None = None,
        client_order_ids: tuple[str, ...] | None = None,
    ) -> tuple[BrokerOrder, ...]:
        """Return broker order snapshots, optionally filtered by IDs."""
        ...

    def get_fills(
        self,
        account_scope: AccountScope,
        *,
        broker_order_ids: tuple[str, ...] | None = None,
    ) -> tuple[BrokerFill, ...]:
        """Return fills for the account, optionally filtered by broker order ID."""
        ...


@runtime_checkable
class BrokerWritePort(Protocol):
    """Broker write capability: submit and cancel only (no in-place replace).

    Holders of :class:`BrokerReadPort` must not receive this capability.
    Replacement policy: cancel the existing order, then submit a newly approved
    proposal with a new ``order_hash`` (DEC-007). Never reuse the prior approval.
    """

    def submit(self, request: BrokerSubmitRequest) -> BrokerSubmitResult:
        """Submit one approved limit order using ``request.client_order_id``.

        Returns :attr:`BrokerSubmitOutcome.UNKNOWN` (or raises
        :class:`BrokerUnknownOutcomeError`) when the broker outcome is
        ambiguous. Never treat that case as :attr:`BrokerSubmitOutcome.REJECTED`.
        """
        ...

    def cancel(self, command: CancelCommand) -> CancelResult:
        """Cancel using ``command.idempotency_key`` (distinct from submit IDs).

        ``CancelStatus.UNKNOWN`` requires reconciliation before another cancel.
        """
        ...


def assert_no_replace_operation(port: object) -> None:
    """Fail closed if a broker port exposes an in-place replace-style method."""
    for name in FORBIDDEN_REPLACE_METHOD_NAMES:
        if callable(getattr(port, name, None)):
            raise AssertionError(
                f"broker port must not expose in-place replace method {name!r}; "
                "replacement is cancel + new approved proposal (DEC-007)"
            )


def assert_read_port_has_no_write_methods(port: object) -> None:
    """Fail closed if a purported read-only port exposes submit/cancel."""
    for name in WRITE_METHOD_NAMES:
        if callable(getattr(port, name, None)):
            raise AssertionError(f"BrokerReadPort must not expose write method {name!r}")


__all__ = [
    "FORBIDDEN_REPLACE_METHOD_NAMES",
    "READ_METHOD_NAMES",
    "WRITE_METHOD_NAMES",
    "BrokerAuthError",
    "BrokerError",
    "BrokerErrorCode",
    "BrokerInvalidOrderError",
    "BrokerRateLimitError",
    "BrokerReadPort",
    "BrokerRejectedError",
    "BrokerSubmitOutcome",
    "BrokerSubmitRequest",
    "BrokerSubmitResult",
    "BrokerTimeoutError",
    "BrokerUnknownOutcomeError",
    "BrokerWritePort",
    "ClientOrderId",
    "assert_no_replace_operation",
    "assert_read_port_has_no_write_methods",
    "cancel_is_confirmed_rejection",
    "cancel_is_unknown_outcome",
]
