"""Candidate order and OrderProposal schemas (P02-T3).

These are the only money-moving intent objects after a TradeSignal. Strategies
never construct them; the Position Sizer emits candidates, and risk+hashing
produce an OrderProposal.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import StringConstraints, model_validator

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    AssetType,
    CurrencyCode,
    DomainModel,
    ExchangeMic,
    Money,
    PositiveDecimal,
    Price,
    SchemaVersion,
    StableId,
    Symbol,
    UtcDateTime,
    enforce_bounded_decimal,
)
from ainvest.schemas.portfolio import AccountScope
from ainvest.schemas.strategy import StrategyName, StrategyVersion

OrderHashDigest = Annotated[
    str,
    StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$", min_length=71, max_length=71),
]

ReasonCode = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,63}$", min_length=2, max_length=64),
]


class OrderSide(StrEnum):
    """First-release order sides. Short selling is not permitted."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    """First-release order types. Only LIMIT is allowed."""

    LIMIT = "LIMIT"


class TimeInForce(StrEnum):
    """First-release time-in-force. Only DAY is allowed."""

    DAY = "DAY"


class CandidateOrder(DomainModel):
    """Sizer output before risk approval. Not yet approval-bound."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    candidate_id: StableId
    signal_id: StableId
    account_scope: AccountScope
    instrument_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    symbol: Symbol
    exchange: ExchangeMic
    currency: CurrencyCode
    asset_type: AssetType
    side: OrderSide
    quantity: PositiveDecimal
    quantity_increment: PositiveDecimal
    order_type: OrderType = OrderType.LIMIT
    limit_price: Price
    price_increment: PositiveDecimal
    time_in_force: TimeInForce = TimeInForce.DAY
    maximum_notional: Money
    strategy: StrategyName
    strategy_version: StrategyVersion
    created_at: UtcDateTime
    expires_at: UtcDateTime
    reason_codes: tuple[ReasonCode, ...] = ()

    @model_validator(mode="after")
    def _candidate_consistency(self) -> CandidateOrder:
        if self.asset_type not in {AssetType.EQUITY, AssetType.ETF}:
            raise ValueError("only EQUITY and ETF are allowed")
        if self.order_type is not OrderType.LIMIT:
            raise ValueError("only LIMIT orders are allowed")
        if self.time_in_force is not TimeInForce.DAY:
            raise ValueError("only DAY time_in_force is allowed")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be > created_at")
        if self.quantity_increment <= 0:
            raise ValueError("quantity_increment must be > 0")
        if self.price_increment <= 0:
            raise ValueError("price_increment must be > 0")
        _require_increment_multiple("quantity", self.quantity, self.quantity_increment)
        _require_increment_multiple("limit_price", self.limit_price, self.price_increment)
        _require_notional_within_limit(self.quantity, self.limit_price, self.maximum_notional)
        return self


class OrderProposal(DomainModel):
    """Risk-approved, hash-bound order intent (design.md §6.3).

    Structural validation lives here. Consumers must construct proposals through
    :func:`ainvest.approval.order_hash.parse_order_proposal` (or call
    :func:`ainvest.approval.order_hash.verify_order_hash`) so the digest is
    checked against protected fields before approval/execution.
    """

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    proposal_id: StableId
    signal_id: StableId
    candidate_id: StableId | None = None
    account_scope: AccountScope
    instrument_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    symbol: Symbol
    exchange: ExchangeMic
    currency: CurrencyCode
    asset_type: AssetType
    side: OrderSide
    quantity: PositiveDecimal
    quantity_increment: PositiveDecimal
    order_type: OrderType = OrderType.LIMIT
    limit_price: Price
    price_increment: PositiveDecimal
    time_in_force: TimeInForce = TimeInForce.DAY
    maximum_notional: Money
    strategy: StrategyName
    strategy_version: StrategyVersion
    created_at: UtcDateTime
    expires_at: UtcDateTime
    risk_decision_id: StableId
    order_hash: OrderHashDigest

    @model_validator(mode="after")
    def _proposal_consistency(self) -> OrderProposal:
        if self.asset_type not in {AssetType.EQUITY, AssetType.ETF}:
            raise ValueError("only EQUITY and ETF are allowed")
        if self.order_type is not OrderType.LIMIT:
            raise ValueError("only LIMIT orders are allowed")
        if self.time_in_force is not TimeInForce.DAY:
            raise ValueError("only DAY time_in_force is allowed")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be > created_at")
        if self.quantity_increment <= 0:
            raise ValueError("quantity_increment must be > 0")
        if self.price_increment <= 0:
            raise ValueError("price_increment must be > 0")
        _require_increment_multiple("quantity", self.quantity, self.quantity_increment)
        _require_increment_multiple("limit_price", self.limit_price, self.price_increment)
        _require_notional_within_limit(self.quantity, self.limit_price, self.maximum_notional)
        return self


def _decimal_coeff_exp(value: Decimal) -> tuple[int, int]:
    """Return ``(coefficient, exponent)`` for exact integer-scaled arithmetic."""
    enforce_bounded_decimal(value)
    sign, digits, exp = value.as_tuple()
    if not isinstance(exp, int):
        raise ValueError("NaN and Infinity are not allowed")
    coefficient = int("".join(str(digit) for digit in digits) or "0")
    if sign:
        coefficient = -coefficient
    return coefficient, exp


def _require_increment_multiple(label: str, value: object, increment: object) -> None:
    """Require ``value`` to be an exact integer multiple of ``increment``.

    Uses integer scaling instead of Decimal division so the default 28-digit
    context cannot round a non-multiple into an apparently integral ratio.
    """
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    step = increment if isinstance(increment, Decimal) else Decimal(str(increment))
    if step <= 0:
        raise ValueError(f"{label} increment must be > 0")
    amount_coeff, amount_exp = _decimal_coeff_exp(amount)
    step_coeff, step_exp = _decimal_coeff_exp(step)
    scale = min(amount_exp, step_exp)
    amount_int = amount_coeff * (10 ** (amount_exp - scale))
    step_int = step_coeff * (10 ** (step_exp - scale))
    if amount_int % step_int != 0:
        raise ValueError(f"{label} must be an integer multiple of its increment")


def _require_notional_within_limit(
    quantity: object,
    limit_price: object,
    maximum_notional: object,
) -> None:
    """Require ``quantity * limit_price <= maximum_notional`` without rounding."""
    qty = quantity if isinstance(quantity, Decimal) else Decimal(str(quantity))
    price = limit_price if isinstance(limit_price, Decimal) else Decimal(str(limit_price))
    limit = (
        maximum_notional
        if isinstance(maximum_notional, Decimal)
        else Decimal(str(maximum_notional))
    )
    qty_coeff, qty_exp = _decimal_coeff_exp(qty)
    price_coeff, price_exp = _decimal_coeff_exp(price)
    limit_coeff, limit_exp = _decimal_coeff_exp(limit)
    left_coeff = qty_coeff * price_coeff
    left_exp = qty_exp + price_exp
    scale = min(left_exp, limit_exp)
    left_int = left_coeff * (10 ** (left_exp - scale))
    right_int = limit_coeff * (10 ** (limit_exp - scale))
    if left_int > right_int:
        raise ValueError("quantity * limit_price must be <= maximum_notional")


def order_proposal_example() -> dict[str, Any]:
    """Return the design.md §6.3 OrderProposal example with required fields."""
    return {
        "schema_version": "1.0",
        "proposal_id": "ordp_01HZYEXAMPLE0001",
        "signal_id": "sig_01HZYEXAMPLE0001",
        "candidate_id": "cand_01HZYEXAMPLE0001",
        "account_scope": "agentic",
        "instrument_id": "rh_inst_aapl_xnas",
        "symbol": "AAPL",
        "exchange": "XNAS",
        "currency": "USD",
        "asset_type": "EQUITY",
        "side": "BUY",
        "quantity": "2",
        "quantity_increment": "1",
        "order_type": "LIMIT",
        "limit_price": "214.50",
        "price_increment": "0.01",
        "time_in_force": "DAY",
        "maximum_notional": "429.00",
        "strategy": "sma_crossover",
        "strategy_version": "1.2.0",
        "created_at": "2026-07-24T18:30:12Z",
        "expires_at": "2026-07-24T18:32:12Z",
        "risk_decision_id": "risk_01HZYEXAMPLE0001",
        # Placeholder; tests replace with the canonical digest from order_hash.
        "order_hash": "sha256:" + ("0" * 64),
    }


__all__ = [
    "CandidateOrder",
    "OrderHashDigest",
    "OrderProposal",
    "OrderSide",
    "OrderType",
    "ReasonCode",
    "TimeInForce",
    "order_proposal_example",
]
