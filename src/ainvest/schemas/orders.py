"""Candidate order and OrderProposal schemas (P02-T3).

These are the only money-moving intent objects after a TradeSignal. Strategies
never construct them; the Position Sizer emits candidates, and risk+hashing
produce an OrderProposal.
"""

from __future__ import annotations

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
        # Whole-increment quantity check in Decimal space.
        ratio = self.quantity / self.quantity_increment
        if ratio != ratio.to_integral_value():
            raise ValueError("quantity must be an integer multiple of quantity_increment")
        notional = self.quantity * self.limit_price
        if notional > self.maximum_notional:
            raise ValueError("quantity * limit_price must be <= maximum_notional")
        return self


class OrderProposal(DomainModel):
    """Risk-approved, hash-bound order intent (design.md §6.3)."""

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
        ratio = self.quantity / self.quantity_increment
        if ratio != ratio.to_integral_value():
            raise ValueError("quantity must be an integer multiple of quantity_increment")
        notional = self.quantity * self.limit_price
        if notional > self.maximum_notional:
            raise ValueError("quantity * limit_price must be <= maximum_notional")
        return self


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
