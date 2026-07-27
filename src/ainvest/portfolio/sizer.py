"""Single-strategy Position Sizer (P03-T6).

Converts a ``TradeSignal`` target-weight intent into at most one whole-share
``CandidateOrder``. This module never approves risk and never submits orders.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from ainvest.schemas.common import (
    DomainModel,
    MachineCode,
    Money,
    OrderSide,
    PositiveDecimal,
    StableId,
    UtcDateTime,
    canonicalize_decimal,
    ensure_utc,
    parse_decimal,
)
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.orders import CandidateOrder, OrderType, TimeInForce
from ainvest.schemas.portfolio import PortfolioSnapshot, PositionSnapshot
from ainvest.schemas.strategy import SignalIntent, TradeSignal

ZERO = Decimal("0")


class SizerReasonCode(StrEnum):
    """Stable machine-readable outcomes from the single-strategy sizer."""

    SIZED_TO_TARGET_WEIGHT = "SIZED_TO_TARGET_WEIGHT"
    HOLD_SIGNAL = "HOLD_SIGNAL"
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
    SIGNAL_NOT_ACTIVE = "SIGNAL_NOT_ACTIVE"
    MISSING_TARGET_WEIGHT = "MISSING_TARGET_WEIGHT"
    MISSING_PRICE = "MISSING_PRICE"
    INVALID_PRICE_INCREMENT = "INVALID_PRICE_INCREMENT"
    INVALID_QUANTITY_INCREMENT = "INVALID_QUANTITY_INCREMENT"
    NON_POSITIVE_BUYING_POWER = "NON_POSITIVE_BUYING_POWER"
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    INSTRUMENT_MISMATCH = "INSTRUMENT_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    INTENT_DELTA_MISMATCH = "INTENT_DELTA_MISMATCH"
    ZERO_SHARE_DELTA = "ZERO_SHARE_DELTA"
    BELOW_MIN_NOTIONAL = "BELOW_MIN_NOTIONAL"
    INSUFFICIENT_POSITION = "INSUFFICIENT_POSITION"
    CASH_RESERVE_BLOCKS_BUY = "CASH_RESERVE_BLOCKS_BUY"
    OPEN_BUY_BLOCKS = "OPEN_BUY_BLOCKS"
    OPEN_BUY_MISSING_LIMIT = "OPEN_BUY_MISSING_LIMIT"
    MAX_NOTIONAL_BLOCKS = "MAX_NOTIONAL_BLOCKS"


class SizingConfig(DomainModel):
    """Deterministic sizing limits and broker precision metadata.

    Tick and quantity increments are supplied here because instrument identity
    alone does not carry broker precision. Missing or non-positive increments
    fail closed at construction.
    """

    quantity_increment: PositiveDecimal
    price_increment: PositiveDecimal
    min_notional: Money
    max_notional: Money
    cash_reserve: Money = ZERO
    candidate_ttl_seconds: Annotated[int, Field(ge=1, le=86_400)] = 120

    @model_validator(mode="after")
    def _limits_consistent(self) -> SizingConfig:
        if self.min_notional > self.max_notional:
            raise ValueError("min_notional must be <= max_notional")
        if self.max_notional <= ZERO:
            raise ValueError("max_notional must be > 0")
        return self


class SizingResult(DomainModel):
    """Sizer output: either one candidate order or an explicit no-trade reason."""

    reason_code: MachineCode
    candidate: CandidateOrder | None = None
    as_of: UtcDateTime

    @model_validator(mode="after")
    def _candidate_matches_reason(self) -> SizingResult:
        if self.candidate is None:
            if self.reason_code == SizerReasonCode.SIZED_TO_TARGET_WEIGHT:
                raise ValueError("successful sizing requires a candidate order")
        elif self.reason_code != SizerReasonCode.SIZED_TO_TARGET_WEIGHT:
            raise ValueError("candidate orders require SIZED_TO_TARGET_WEIGHT")
        return self


CandidateId = Annotated[
    str,
    StringConstraints(
        pattern=r"^cand_[A-Za-z0-9_-]{4,128}$",
        min_length=8,
        max_length=160,
    ),
]


def size_position(
    *,
    signal: TradeSignal,
    quote: MarketQuote,
    portfolio: PortfolioSnapshot,
    config: SizingConfig,
    as_of: datetime,
    candidate_id: CandidateId | StableId,
) -> SizingResult:
    """Convert target-weight intent into a whole-share candidate order.

    Fail-closed: HOLD, expired/inactive signals, missing price, invalid
    increments, and non-positive buying power on BUY return no order with a
    stable reason. Open BUY notionals reserve cash; open SELLs reduce sellable
    quantity. Arithmetic uses :class:`~decimal.Decimal` only.
    """
    clock = ensure_utc(as_of)
    early = _early_reject(
        signal=signal, quote=quote, portfolio=portfolio, config=config, as_of=clock
    )
    if early is not None:
        return SizingResult(reason_code=early, candidate=None, as_of=clock)

    assert signal.target_weight is not None  # guarded above
    target_weight = parse_decimal(signal.target_weight)
    equity = parse_decimal(portfolio.equity)
    last_price = parse_decimal(quote.last_price)
    qty_inc = parse_decimal(config.quantity_increment)
    price_inc = parse_decimal(config.price_increment)

    position = _find_position(portfolio, quote)
    filled_qty = parse_decimal(position.quantity) if position is not None else ZERO
    open_buy_qty, open_sell_qty = _open_order_quantities(portfolio, quote.instrument.instrument_id)
    effective_qty = canonicalize_decimal(filled_qty + open_buy_qty - open_sell_qty)
    if effective_qty < ZERO:
        return SizingResult(
            reason_code=SizerReasonCode.INSUFFICIENT_POSITION,
            candidate=None,
            as_of=clock,
        )

    # Mark with last when open orders affect exposure; otherwise trust snapshot MV.
    if open_buy_qty == ZERO and open_sell_qty == ZERO and position is not None:
        current_value = parse_decimal(position.market_value)
    else:
        current_value = canonicalize_decimal(effective_qty * last_price)

    target_value = canonicalize_decimal(equity * target_weight)
    delta_value = canonicalize_decimal(target_value - current_value)

    if delta_value > ZERO:
        side = OrderSide.BUY
    elif delta_value < ZERO:
        side = OrderSide.SELL
    else:
        return SizingResult(
            reason_code=SizerReasonCode.ZERO_SHARE_DELTA,
            candidate=None,
            as_of=clock,
        )

    if (side is OrderSide.BUY and signal.intent is not SignalIntent.BUY) or (
        side is OrderSide.SELL and signal.intent is not SignalIntent.SELL
    ):
        return SizingResult(
            reason_code=SizerReasonCode.INTENT_DELTA_MISMATCH,
            candidate=None,
            as_of=clock,
        )

    # Buying power gates buys only; fully invested accounts must still be able to sell.
    if side is OrderSide.BUY and parse_decimal(portfolio.buying_power) <= ZERO:
        return SizingResult(
            reason_code=SizerReasonCode.NON_POSITIVE_BUYING_POWER,
            candidate=None,
            as_of=clock,
        )

    reference_price = _reference_price(quote=quote, side=side)
    if reference_price is None:
        return SizingResult(
            reason_code=SizerReasonCode.MISSING_PRICE,
            candidate=None,
            as_of=clock,
        )

    limit_price = _normalize_limit_price(reference_price, price_inc, side=side)
    if limit_price <= ZERO:
        return SizingResult(
            reason_code=SizerReasonCode.MISSING_PRICE,
            candidate=None,
            as_of=clock,
        )

    raw_qty = canonicalize_decimal(abs(delta_value) / limit_price)
    quantity = _floor_to_increment(raw_qty, qty_inc)
    if quantity <= ZERO:
        return SizingResult(
            reason_code=SizerReasonCode.ZERO_SHARE_DELTA,
            candidate=None,
            as_of=clock,
        )

    if side is OrderSide.SELL:
        # Never sell shares already committed on open sell orders.
        sellable = _floor_to_increment(filled_qty - open_sell_qty, qty_inc)
        if sellable <= ZERO:
            return SizingResult(
                reason_code=SizerReasonCode.INSUFFICIENT_POSITION,
                candidate=None,
                as_of=clock,
            )
        quantity = min(quantity, sellable)
    else:
        spendable = _spendable_buying_power(portfolio=portfolio, config=config)
        if spendable is None:
            return SizingResult(
                reason_code=SizerReasonCode.OPEN_BUY_MISSING_LIMIT,
                candidate=None,
                as_of=clock,
            )
        if spendable <= ZERO:
            if parse_decimal(portfolio.buying_power) <= ZERO:
                reason = SizerReasonCode.NON_POSITIVE_BUYING_POWER
            elif parse_decimal(_open_buy_reserved_notional(portfolio) or ZERO) > ZERO:
                reason = SizerReasonCode.OPEN_BUY_BLOCKS
            else:
                reason = SizerReasonCode.CASH_RESERVE_BLOCKS_BUY
            return SizingResult(
                reason_code=reason,
                candidate=None,
                as_of=clock,
            )
        max_by_cash = _floor_to_increment(spendable / limit_price, qty_inc)
        if max_by_cash <= ZERO:
            return SizingResult(
                reason_code=SizerReasonCode.CASH_RESERVE_BLOCKS_BUY,
                candidate=None,
                as_of=clock,
            )
        quantity = min(quantity, max_by_cash)

    max_notional = parse_decimal(config.max_notional)
    min_notional = parse_decimal(config.min_notional)
    max_by_notional = _floor_to_increment(max_notional / limit_price, qty_inc)
    if max_by_notional <= ZERO:
        return SizingResult(
            reason_code=SizerReasonCode.MAX_NOTIONAL_BLOCKS,
            candidate=None,
            as_of=clock,
        )
    quantity = min(quantity, max_by_notional)
    if quantity <= ZERO:
        return SizingResult(
            reason_code=SizerReasonCode.ZERO_SHARE_DELTA,
            candidate=None,
            as_of=clock,
        )

    notional = canonicalize_decimal(quantity * limit_price)
    if notional < min_notional:
        return SizingResult(
            reason_code=SizerReasonCode.BELOW_MIN_NOTIONAL,
            candidate=None,
            as_of=clock,
        )
    if notional > max_notional:
        # Should be unreachable after clamp; fail closed if arithmetic drifts.
        return SizingResult(
            reason_code=SizerReasonCode.MAX_NOTIONAL_BLOCKS,
            candidate=None,
            as_of=clock,
        )

    instrument = quote.instrument
    expires_at = clock + timedelta(seconds=config.candidate_ttl_seconds)
    candidate = CandidateOrder.model_validate(
        {
            "candidate_id": candidate_id,
            "signal_id": signal.signal_id,
            "account_scope": portfolio.account_scope,
            "instrument_id": instrument.instrument_id,
            "symbol": instrument.symbol,
            "exchange": instrument.exchange,
            "currency": instrument.currency,
            "asset_type": instrument.asset_type,
            "side": side,
            "quantity": quantity,
            "quantity_increment": qty_inc,
            "order_type": OrderType.LIMIT,
            "limit_price": limit_price,
            "price_increment": price_inc,
            "time_in_force": TimeInForce.DAY,
            "maximum_notional": notional,
            "strategy": signal.strategy,
            "strategy_version": signal.strategy_version,
            "created_at": clock,
            "expires_at": expires_at,
            "reason_codes": [SizerReasonCode.SIZED_TO_TARGET_WEIGHT.value],
        }
    )
    return SizingResult(
        reason_code=SizerReasonCode.SIZED_TO_TARGET_WEIGHT,
        candidate=candidate,
        as_of=clock,
    )


def _early_reject(
    *,
    signal: TradeSignal,
    quote: MarketQuote,
    portfolio: PortfolioSnapshot,
    config: SizingConfig,
    as_of: datetime,
) -> SizerReasonCode | None:
    if signal.intent is SignalIntent.HOLD:
        return SizerReasonCode.HOLD_SIGNAL
    if as_of < signal.generated_at:
        return SizerReasonCode.SIGNAL_NOT_ACTIVE
    if signal.is_expired(as_of):
        return SizerReasonCode.SIGNAL_EXPIRED
    if signal.target_weight is None:
        return SizerReasonCode.MISSING_TARGET_WEIGHT

    qty_inc = parse_decimal(config.quantity_increment)
    price_inc = parse_decimal(config.price_increment)
    if qty_inc <= ZERO:
        return SizerReasonCode.INVALID_QUANTITY_INCREMENT
    if price_inc <= ZERO:
        return SizerReasonCode.INVALID_PRICE_INCREMENT

    try:
        last_price = parse_decimal(quote.last_price)
    except ValueError:
        return SizerReasonCode.MISSING_PRICE
    if last_price <= ZERO:
        return SizerReasonCode.MISSING_PRICE

    if quote.instrument.symbol != signal.symbol:
        return SizerReasonCode.SYMBOL_MISMATCH
    if quote.currency != portfolio.currency or quote.instrument.currency != portfolio.currency:
        return SizerReasonCode.CURRENCY_MISMATCH

    # Instrument binding: any open position for the symbol must match quote identity.
    for position in portfolio.positions:
        if position.instrument.symbol == signal.symbol and (
            position.instrument.instrument_id != quote.instrument.instrument_id
            or position.instrument.exchange != quote.instrument.exchange
            or position.instrument.currency != quote.instrument.currency
            or position.instrument.asset_type != quote.instrument.asset_type
        ):
            return SizerReasonCode.INSTRUMENT_MISMATCH
    return None


def _find_position(portfolio: PortfolioSnapshot, quote: MarketQuote) -> PositionSnapshot | None:
    for position in portfolio.positions:
        if position.instrument.instrument_id == quote.instrument.instrument_id:
            return position
    return None


def _open_order_quantities(
    portfolio: PortfolioSnapshot, instrument_id: str
) -> tuple[Decimal, Decimal]:
    """Return ``(open_buy_qty, open_sell_qty)`` for one instrument."""
    buy_qty = ZERO
    sell_qty = ZERO
    for order in portfolio.open_orders:
        if order.instrument.instrument_id != instrument_id:
            continue
        qty = parse_decimal(order.quantity)
        if order.side is OrderSide.BUY:
            buy_qty = canonicalize_decimal(buy_qty + qty)
        elif order.side is OrderSide.SELL:
            sell_qty = canonicalize_decimal(sell_qty + qty)
    return buy_qty, sell_qty


def _open_buy_reserved_notional(portfolio: PortfolioSnapshot) -> Decimal | None:
    """Sum open BUY ``qty * limit_price``.

    Returns ``None`` when any open BUY lacks a positive limit price so sizing
    cannot safely reserve capital (fail closed).
    """
    reserved = ZERO
    for order in portfolio.open_orders:
        if order.side is not OrderSide.BUY:
            continue
        if order.limit_price is None:
            return None
        try:
            limit = parse_decimal(order.limit_price)
        except ValueError:
            return None
        if limit <= ZERO:
            return None
        reserved = canonicalize_decimal(reserved + parse_decimal(order.quantity) * limit)
    return reserved


def _reference_price(*, quote: MarketQuote, side: OrderSide) -> Decimal | None:
    """Prefer touch price when present; otherwise last. Fail closed if unusable."""
    try:
        last = parse_decimal(quote.last_price)
    except ValueError:
        return None
    if last <= ZERO:
        return None
    if side is OrderSide.BUY and quote.ask is not None:
        try:
            ask = parse_decimal(quote.ask)
        except ValueError:
            return None
        return ask if ask > ZERO else None
    if side is OrderSide.SELL and quote.bid is not None:
        try:
            bid = parse_decimal(quote.bid)
        except ValueError:
            return None
        return bid if bid > ZERO else None
    return last


def _normalize_limit_price(price: Decimal, increment: Decimal, *, side: OrderSide) -> Decimal:
    """Snap to tick in the capital-safe direction.

    BUY floors (never pay more than the reference). SELL ceils (never receive
    less than the reference). Exact multiples are unchanged.
    """
    if increment <= ZERO:
        raise ValueError("price increment must be > 0")
    price = parse_decimal(price)
    increment = parse_decimal(increment)
    if side is OrderSide.BUY:
        return _floor_to_increment(price, increment)
    return _ceil_to_increment(price, increment)


def _floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    """Largest non-negative multiple of ``increment`` that is ``<= value``."""
    value = parse_decimal(value)
    increment = parse_decimal(increment)
    if increment <= ZERO:
        raise ValueError("increment must be > 0")
    if value <= ZERO:
        return ZERO
    # Exact integer arithmetic avoids Decimal context rounding.
    value_coeff, value_exp = _coeff_exp(value)
    inc_coeff, inc_exp = _coeff_exp(increment)
    scale = min(value_exp, inc_exp)
    value_int = value_coeff * (10 ** (value_exp - scale))
    inc_int = inc_coeff * (10 ** (inc_exp - scale))
    multiples = value_int // inc_int
    result_exp = scale
    return canonicalize_decimal(Decimal((0, _digits(multiples * inc_int), result_exp)))


def _ceil_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    """Smallest positive multiple of ``increment`` that is ``>= value``."""
    value = parse_decimal(value)
    increment = parse_decimal(increment)
    if increment <= ZERO:
        raise ValueError("increment must be > 0")
    if value <= ZERO:
        return increment
    floored = _floor_to_increment(value, increment)
    if floored == value:
        return floored
    return canonicalize_decimal(floored + increment)


def _spendable_buying_power(
    *, portfolio: PortfolioSnapshot, config: SizingConfig
) -> Decimal | None:
    """Buying power remaining after cash reserve and open BUY notionals.

    When ``buying_power`` already nets open-buy reserves (paper-style snapshots
    where ``buying_power + open_buy_notional ≈ cash``), those notionals are not
    subtracted again. Gross ``buying_power`` snapshots still reserve open buys.

    Returns ``None`` when an open BUY is missing a usable limit price.
    """
    buying_power = parse_decimal(portfolio.buying_power)
    cash = parse_decimal(portfolio.cash)
    reserve = parse_decimal(config.cash_reserve)
    after_reserve = cash - reserve
    if after_reserve < ZERO:
        after_reserve = ZERO
    open_buy_notional = _open_buy_reserved_notional(portfolio)
    if open_buy_notional is None:
        return None
    # Paper exports buying_power = cash - open_buy_reserves. Require approximate
    # equality so margin/agentic BP < cash does not skip reservation.
    _tol = Decimal("0.000001")
    already_net = open_buy_notional > ZERO and abs(buying_power + open_buy_notional - cash) <= _tol
    available = min(buying_power, after_reserve)
    if not already_net:
        available = available - open_buy_notional
    if available < ZERO:
        available = ZERO
    return canonicalize_decimal(available)


def _coeff_exp(value: Decimal) -> tuple[int, int]:
    canonical = parse_decimal(value)
    sign, digits, exp = canonical.as_tuple()
    if not isinstance(exp, int):
        raise ValueError("NaN and Infinity are not allowed")
    coefficient = int("".join(str(digit) for digit in digits) or "0")
    if sign:
        coefficient = -coefficient
    return coefficient, exp


def _digits(value: int) -> tuple[int, ...]:
    if value < 0:
        raise ValueError("digit coefficient must be non-negative")
    if value == 0:
        return (0,)
    return tuple(int(ch) for ch in str(value))


# Public helpers for tests and downstream pure math consumers.
normalize_limit_price = _normalize_limit_price
floor_to_increment = _floor_to_increment
ceil_to_increment = _ceil_to_increment


__all__ = [
    "CandidateId",
    "SizerReasonCode",
    "SizingConfig",
    "SizingResult",
    "ceil_to_increment",
    "floor_to_increment",
    "normalize_limit_price",
    "size_position",
]
