"""Shared types, constants, and small helpers for the paper broker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final, Protocol

from pydantic import model_validator

from ainvest.schemas.broker import BrokerFill, BrokerOrder
from ainvest.schemas.common import (
    DomainModel,
    InstrumentIdentity,
    NonNegativeDecimal,
    OrderSide,
    PositiveDecimal,
    Price,
    UtcDateTime,
    canonicalize_decimal,
    ensure_utc,
)
from ainvest.schemas.orders import OrderProposal

ZERO: Final[Decimal] = Decimal("0")
BPS_DENOM: Final[Decimal] = Decimal("10000")
_PAPER_SOURCE: Final[str] = "ainvest.paper.broker"
_WEIGHT_QUANT: Final[Decimal] = Decimal("0.00000001")
_MONEY_QUANT: Final[Decimal] = Decimal("0.000001")


class PaperRejectReason(StrEnum):
    """Stable machine codes for paper submit / cancel rejections."""

    ACCOUNT_SCOPE_NOT_PAPER = "ACCOUNT_SCOPE_NOT_PAPER"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    INSUFFICIENT_POSITION = "INSUFFICIENT_POSITION"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    ORDER_NOT_CANCELABLE = "ORDER_NOT_CANCELABLE"
    ORDER_EXPIRED = "ORDER_EXPIRED"
    INVALID_MARKET_EVENT = "INVALID_MARKET_EVENT"


class PaperClock(Protocol):
    """Injected clock; implementations must be deterministic under test."""

    def __call__(self) -> datetime: ...


class PaperCostModel(DomainModel):
    """Explicit trading-cost parameters (never omitted as implicit zero).

    All three fields are required. Callers that intentionally want zero cost
    must pass ``Decimal(\"0\")`` explicitly for each component.
    """

    fee_bps: NonNegativeDecimal
    half_spread_bps: NonNegativeDecimal
    slippage_bps: NonNegativeDecimal


class PaperMarketEvent(DomainModel):
    """Injected market observation used to drive limit fills.

    ``liquidity`` is the maximum share quantity this event may fill against
    working orders (FIFO by submission time).
    """

    event_id: str
    instrument_id: str
    bid: Price
    ask: Price
    last: Price
    liquidity: PositiveDecimal
    observed_at: UtcDateTime

    @model_validator(mode="after")
    def _quote_invariants(self) -> PaperMarketEvent:
        if self.bid <= ZERO or self.ask <= ZERO or self.last <= ZERO:
            raise ValueError("bid, ask, and last must be > 0")
        if self.bid > self.ask:
            raise ValueError("bid must be <= ask")
        return self


@dataclass(frozen=True)
class _SubmitFingerprint:
    proposal_id: str
    order_hash: str
    side: OrderSide
    quantity: Decimal
    limit_price: Decimal
    instrument_id: str


@dataclass
class _WorkingOrder:
    broker_order: BrokerOrder
    proposal: OrderProposal
    remaining: Decimal
    reserved_cash: Decimal
    reserved_qty: Decimal
    filled_qty: Decimal = ZERO
    fills: list[BrokerFill] = field(default_factory=list)


@dataclass
class _PositionBook:
    instrument: InstrumentIdentity
    quantity: Decimal
    average_cost: Decimal


def _money(value: Decimal) -> Decimal:
    quantized = Decimal(value).quantize(_MONEY_QUANT)
    return canonicalize_decimal(quantized)


def _weight(value: Decimal) -> Decimal:
    quantized = Decimal(value).quantize(_WEIGHT_QUANT)
    return canonicalize_decimal(quantized)


def _require_utc(moment: datetime) -> datetime:
    return ensure_utc(moment)


def _fingerprint(proposal: OrderProposal) -> _SubmitFingerprint:
    return _SubmitFingerprint(
        proposal_id=proposal.proposal_id,
        order_hash=proposal.order_hash,
        side=proposal.side,
        quantity=_money(proposal.quantity),
        limit_price=_money(proposal.limit_price),
        instrument_id=proposal.instrument_id,
    )


def _broker_order_id(client_order_id: str) -> str:
    return f"paper_{client_order_id}"


def _fill_id(*, broker_order_id: str, event_id: str, seq: int) -> str:
    return f"paper_fill_{broker_order_id}_{event_id}_{seq}"
