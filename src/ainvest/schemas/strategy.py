"""StrategyContext and TradeSignal schemas (P02-T2).

A strategy may only read an immutable :class:`StrategyContext` and return zero
or more :class:`TradeSignal` intents. Signals are not broker orders: HOLD never
becomes an order, and ``strength`` is an internal score in ``[-1, 1]``, not a
success or profit probability.

Downstream Strategy API / B3 sizing must consume signals only through
:func:`parse_trade_signal` (requires ``as_of``) or
:func:`parse_trade_signal_for_context`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import StrictBool, StrictInt, StringConstraints, field_validator, model_validator

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    DomainModel,
    MachineCode,
    PnL,
    SchemaVersion,
    SignedRatio,
    StableId,
    Symbol,
    UtcDateTime,
    Weight,
    ensure_utc,
)
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.research import ResearchPacket

StrategyName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$", min_length=2, max_length=64),
]

StrategyVersion = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", min_length=5, max_length=32),
]

ReasonCode = MachineCode

StateKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$", min_length=2, max_length=64),
]


class SignalIntent(StrEnum):
    """Strategy intent. Only BUY/SELL may later become candidate orders."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class StrategyStateValueKind(StrEnum):
    """Typed values for explicit strategy state entries."""

    TEXT = "TEXT"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    INTEGER = "INTEGER"


class StrategyStateItem(DomainModel):
    """One immutable key/value entry in strategy state.

    Scalar fields use strict JSON types so BOOLEAN/INTEGER kinds cannot coerce
    across each other (e.g. ``true`` must not become integer ``1``).
    """

    key: StateKey
    kind: StrategyStateValueKind
    text_value: Annotated[str, StringConstraints(min_length=1, max_length=512)] | None = None
    decimal_value: PnL | None = None
    boolean_value: StrictBool | None = None
    integer_value: StrictInt | None = None

    @model_validator(mode="after")
    def _one_value_for_kind(self) -> StrategyStateItem:
        if self.kind is StrategyStateValueKind.TEXT:
            if (
                self.text_value is None
                or self.decimal_value is not None
                or self.boolean_value is not None
                or self.integer_value is not None
            ):
                raise ValueError("TEXT state requires text_value only")
        elif self.kind is StrategyStateValueKind.DECIMAL:
            if (
                self.decimal_value is None
                or self.text_value is not None
                or self.boolean_value is not None
                or self.integer_value is not None
            ):
                raise ValueError("DECIMAL state requires decimal_value only")
        elif self.kind is StrategyStateValueKind.BOOLEAN:
            if (
                self.boolean_value is None
                or self.text_value is not None
                or self.decimal_value is not None
                or self.integer_value is not None
            ):
                raise ValueError("BOOLEAN state requires boolean_value only")
        elif self.kind is StrategyStateValueKind.INTEGER and (
            self.integer_value is None
            or self.text_value is not None
            or self.decimal_value is not None
            or self.boolean_value is not None
        ):
            raise ValueError("INTEGER state requires integer_value only")
        return self


class StrategyState(DomainModel):
    """Explicit strategy state bound to a strategy version.

    Strategies must not rely on class/global process state. Any retained state
    is read from here and returned as ``next_state`` by later runtime cards.
    """

    strategy: StrategyName
    strategy_version: StrategyVersion
    updated_at: UtcDateTime
    entries: tuple[StrategyStateItem, ...] = ()

    @field_validator("entries", mode="before")
    @classmethod
    def _coerce_entries(cls, value: object) -> object:
        if value is None:
            return ()
        return value

    @model_validator(mode="after")
    def _unique_keys(self) -> StrategyState:
        keys = [item.key for item in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("strategy state keys must be unique")
        return self


class TradeSignal(DomainModel):
    """Strategy trading intent (design.md §6.2).

    ``strength`` is a signed internal score in ``[-1, 1]``. It is not a success
    probability, profit probability, or sizing weight. ``HOLD`` signals are
    informational only and must never become broker orders.

    Structural construction via ``model_validate`` does not prove the signal is
    active. Downstream consumers must use :func:`parse_trade_signal` or
    :func:`parse_trade_signal_for_context`, which require an evaluation ``as_of``.
    """

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    signal_id: StableId
    research_id: StableId
    strategy: StrategyName
    strategy_version: StrategyVersion
    symbol: Symbol
    intent: SignalIntent
    strength: SignedRatio
    target_weight: Weight | None = None
    generated_at: UtcDateTime
    expires_at: UtcDateTime
    reason_codes: tuple[ReasonCode, ...] = ()

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _coerce_reason_codes(cls, value: object) -> object:
        if value is None:
            return ()
        return value

    @field_validator("strategy_version")
    @classmethod
    def _require_strategy_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("strategy_version is required")
        return value

    @model_validator(mode="after")
    def _signal_consistency(self) -> TradeSignal:
        if self.expires_at <= self.generated_at:
            raise ValueError("expires_at must be > generated_at")
        if self.intent is SignalIntent.HOLD and self.target_weight is not None:
            raise ValueError("HOLD signals cannot carry target_weight")
        if not self.reason_codes:
            raise ValueError("at least one reason_code is required")
        return self

    def is_expired(self, as_of: datetime) -> bool:
        """Return True when ``as_of`` is at or after ``expires_at``."""
        return as_of >= self.expires_at

    def may_become_order(self) -> bool:
        """HOLD never becomes an order; BUY/SELL may proceed to sizing."""
        return self.intent is not SignalIntent.HOLD

    def require_active(self, as_of: datetime) -> None:
        """Fail closed when the signal is not active at ``as_of``."""
        if as_of < self.generated_at:
            raise ValueError("signal generated_at is in the future relative to as_of")
        if self.is_expired(as_of):
            raise ValueError("signal is expired")


class StrategyContext(DomainModel):
    """Immutable read-only inputs available to a strategy evaluation."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    as_of: UtcDateTime
    research: ResearchPacket
    portfolio: PortfolioSnapshot
    strategy_state: StrategyState | None = None

    @model_validator(mode="after")
    def _no_future_inputs(self) -> StrategyContext:
        if self.research.as_of > self.as_of:
            raise ValueError("research.as_of must be <= context as_of")
        if self.portfolio.as_of > self.as_of:
            raise ValueError("portfolio.as_of must be <= context as_of")
        if self.strategy_state is not None and self.strategy_state.updated_at > self.as_of:
            raise ValueError("strategy_state.updated_at must be <= context as_of")
        return self

    @property
    def symbol(self) -> Symbol:
        """Symbol under evaluation, taken from the research packet."""
        return self.research.symbol


def trade_signal_example() -> dict[str, Any]:
    """Return the design.md §6.2 TradeSignal example."""
    return {
        "schema_version": "1.0",
        "signal_id": "sig_01HZYEXAMPLE0001",
        "research_id": "res_01HZYEXAMPLE0001",
        "strategy": "sma_crossover",
        "strategy_version": "1.2.0",
        "symbol": "AAPL",
        "intent": "BUY",
        "strength": "0.73",
        "target_weight": "0.10",
        "generated_at": "2026-07-24T18:30:10Z",
        "expires_at": "2026-07-24T19:00:10Z",
        "reason_codes": ["SMA20_CROSSED_ABOVE_SMA50"],
    }


def parse_trade_signal(data: dict[str, Any], *, as_of: datetime | str) -> TradeSignal:
    """Validate a TradeSignal and require it to be active at ``as_of``.

    This is the mandatory public entry point for Strategy API / B3 consumers.
    Structural-only construction via ``TradeSignal.model_validate`` must not be
    used to accept signals for sizing or order generation.
    """
    signal = TradeSignal.model_validate(data)
    signal.require_active(ensure_utc(as_of))
    return signal


def parse_trade_signal_for_context(
    data: dict[str, Any],
    context: StrategyContext,
) -> TradeSignal:
    """Validate a signal against a StrategyContext evaluation clock and identity.

    Deterministic binding rules:
    - ``generated_at`` must equal ``context.as_of`` (evaluation clock)
    - ``symbol`` / ``research_id`` must match the context research packet
    - when ``strategy_state`` is present, ``strategy`` and ``strategy_version``
      must match that state
    """
    signal = parse_trade_signal(data, as_of=context.as_of)
    if signal.generated_at != context.as_of:
        raise ValueError("signal.generated_at must equal context.as_of")
    if signal.symbol != context.symbol:
        raise ValueError("signal.symbol must match strategy context symbol")
    if signal.research_id != context.research.research_id:
        raise ValueError("signal.research_id must match context research_id")
    if context.strategy_state is not None:
        if signal.strategy != context.strategy_state.strategy:
            raise ValueError("signal.strategy must match context.strategy_state.strategy")
        if signal.strategy_version != context.strategy_state.strategy_version:
            raise ValueError(
                "signal.strategy_version must match context.strategy_state.strategy_version"
            )
    return signal


def parse_strategy_context(data: dict[str, Any]) -> StrategyContext:
    """Validate and construct a StrategyContext from a mapping."""
    return StrategyContext.model_validate(data)


__all__ = [
    "ReasonCode",
    "SignalIntent",
    "StrategyContext",
    "StrategyName",
    "StrategyState",
    "StrategyStateItem",
    "StrategyStateValueKind",
    "StrategyVersion",
    "TradeSignal",
    "parse_strategy_context",
    "parse_trade_signal",
    "parse_trade_signal_for_context",
    "trade_signal_example",
]
