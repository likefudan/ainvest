"""Broker order, fill, reconciliation, and cancel schemas (P02-T3).

Cancellation is modeled as a separate command path. There is no in-place
replace operation: a replacement always becomes a new proposal and hash.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints, model_validator

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    DomainModel,
    PositiveDecimal,
    Price,
    SchemaVersion,
    StableId,
    UtcDateTime,
)
from ainvest.schemas.orders import OrderHashDigest, OrderSide
from ainvest.schemas.portfolio import AccountScope


class BrokerOrderStatus(StrEnum):
    """Broker-visible order statuses for first-release reconciliation."""

    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class CancelStatus(StrEnum):
    """Cancel-command outcomes (design §8 cancellation machine)."""

    REQUESTED = "REQUESTED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    NOT_APPLIED = "NOT_APPLIED"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class ReconciliationOutcome(StrEnum):
    """Result of reconciling local state against broker truth."""

    MATCHED = "MATCHED"
    DIVERGED = "DIVERGED"
    UNKNOWN = "UNKNOWN"
    MANUAL_REVIEW = "MANUAL_REVIEW"


CancelReasonCode = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,63}$", min_length=2, max_length=64),
]


class BrokerOrder(DomainModel):
    """Broker acknowledgement / working order snapshot."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    broker_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    client_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    proposal_id: StableId
    order_hash: OrderHashDigest
    account_scope: AccountScope
    side: OrderSide
    status: BrokerOrderStatus
    submitted_at: UtcDateTime
    updated_at: UtcDateTime

    @model_validator(mode="after")
    def _time_order(self) -> BrokerOrder:
        if self.updated_at < self.submitted_at:
            raise ValueError("updated_at must be >= submitted_at")
        return self


class BrokerFill(DomainModel):
    """Individual fill against a broker order."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    fill_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    broker_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    quantity: PositiveDecimal
    price: Price
    filled_at: UtcDateTime


class CancelCommand(DomainModel):
    """Separate cancel intent. Never an in-place replace of a live order."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    cancel_id: StableId
    proposal_id: StableId
    broker_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    order_hash: OrderHashDigest
    account_scope: AccountScope
    reason_code: CancelReasonCode
    idempotency_key: Annotated[str, StringConstraints(min_length=8, max_length=128)]
    requested_at: UtcDateTime


class CancelResult(DomainModel):
    """Outcome of a cancel command, including uncertain paths."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    cancel_id: StableId
    broker_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    status: CancelStatus
    reason_code: CancelReasonCode
    observed_at: UtcDateTime


class ReconciliationResult(DomainModel):
    """Local vs broker reconciliation for an order or cancel."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    reconciliation_id: StableId
    proposal_id: StableId | None = None
    broker_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)] | None = None
    cancel_id: StableId | None = None
    outcome: ReconciliationOutcome
    reason_code: CancelReasonCode
    observed_at: UtcDateTime

    @model_validator(mode="after")
    def _require_subject(self) -> ReconciliationResult:
        if self.broker_order_id is None and self.cancel_id is None:
            raise ValueError("reconciliation requires broker_order_id or cancel_id")
        return self


__all__ = [
    "BrokerFill",
    "BrokerOrder",
    "BrokerOrderStatus",
    "CancelCommand",
    "CancelReasonCode",
    "CancelResult",
    "CancelStatus",
    "ReconciliationOutcome",
    "ReconciliationResult",
]
