"""Bounded synthetic fill economics; no broker, approval or cash-account mutation."""

from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from ainvest.backtest.models import ClosedHistoricalBar, Version
from ainvest.data.indicators import Digest
from ainvest.data.models import PriceAdjustment
from ainvest.schemas.common import (
    DecimalString,
    DomainModel,
    NonNegativeDecimal,
    OrderSide,
    PositiveDecimal,
    StableId,
    UtcDateTime,
)
from ainvest.schemas.orders import CandidateOrder
from ainvest.schemas.portfolio import AccountScope
from ainvest.strategies.worker.digests import digest_json

Bps = Annotated[NonNegativeDecimal, Field(le=1000)]


class CostError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CostConfig(DomainModel):
    """Required hypothetical rates, not current fees or owner-approved policy."""

    version: Version
    commission_flat: Annotated[NonNegativeDecimal, Field(le=1_000_000)]
    commission_bps: Bps
    half_spread_bps: Bps
    slippage_bps: Bps
    participation: Annotated[PositiveDecimal, Field(le=1)]
    minimum_delay_seconds: Annotated[int, Field(ge=1, le=86400)]
    unit_test_zero_cost: StrictBool

    @model_validator(mode="after")
    def _cost_guard(self) -> Self:
        if not self.unit_test_zero_cost and not any(
            (self.commission_flat, self.commission_bps, self.half_spread_bps, self.slippage_bps)
        ):
            raise ValueError("zero cost requires explicit unit-test mode")
        return self


class CostRequest(DomainModel):
    config: CostConfig
    adjustment: PriceAdjustment
    orders: Annotated[tuple[CandidateOrder, ...], Field(min_length=1, max_length=128)]
    bars: Annotated[tuple[ClosedHistoricalBar, ...], Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def _input(self) -> Self:
        if self.adjustment != PriceAdjustment.RAW:
            raise ValueError("candidate economics require raw execution bars")
        if len({order.candidate_id for order in self.orders}) != len(self.orders):
            raise ValueError("duplicate candidates")
        starts = tuple(item.bar.bar_start for item in self.bars)
        if tuple(sorted(set(starts))) != starts:
            raise ValueError("bars must be unique and ordered")
        identity = self.bars[0].bar.instrument
        interval = self.bars[0].bar.interval
        previous_close = None
        for item in self.bars:
            bar = item.bar
            if (
                bar.instrument != identity
                or bar.interval != interval
                or bar.provenance.quality_flags
                or bar.provenance.is_delayed
                or (previous_close is not None and bar.bar_start < previous_close)
            ):
                raise ValueError("mixed/overlapping/low-quality bars")
            if max(bar.high, bar.volume) > Decimal("1000000000000"):
                raise ValueError("economic input bound")
            previous_close = item.closed_at
        for order in self.orders:
            if (
                order.account_scope != AccountScope.PAPER
                or (
                    order.instrument_id,
                    order.symbol,
                    order.exchange,
                    order.currency,
                    order.asset_type,
                )
                != (
                    identity.instrument_id,
                    identity.symbol,
                    identity.exchange,
                    identity.currency,
                    identity.asset_type,
                )
                or identity.identity_as_of > order.created_at
            ):
                raise ValueError("candidate identity/scope not historical Paper")
            if max(order.quantity, order.limit_price, order.maximum_notional) > Decimal(
                "1e12"
            ) or min(order.quantity_increment, order.price_increment) < Decimal("0.00000001"):
                raise ValueError("economic input bound")
        return self


class HypotheticalFill(DomainModel):
    candidate_id: StableId
    bar_start: UtcDateTime
    liquidity_window_end: UtcDateTime
    quantity: PositiveDecimal
    reference_price: PositiveDecimal
    price: PositiveDecimal
    commission: NonNegativeDecimal
    spread_cost: NonNegativeDecimal
    slippage_and_rounding_cost: NonNegativeDecimal
    cash_delta: DecimalString
    input_digest: Digest


class UnfilledQuantity(DomainModel):
    candidate_id: StableId
    quantity: NonNegativeDecimal


class CostResult(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    simulator_version: Literal["next-open-cost-v1"] = "next-open-cost-v1"
    input_digest: Digest
    config_digest: Digest
    fills: Annotated[tuple[HypotheticalFill, ...], Field(max_length=4096)]
    unfilled: Annotated[tuple[UnfilledQuantity, ...], Field(max_length=128)]
    total_cost: NonNegativeDecimal
    unit_test_mode: StrictBool
    execution_enabled: Literal[False] = False
    # This ledger has no cash/position/risk feedback; it is not a performance report.
    performance_report_available: Literal[False] = False


def verify_cost_replay(request: CostRequest, result: CostResult) -> None:
    """Reject altered totals/fills/configuration by recomputing the retained request."""
    if simulate_costs(request) != result:
        raise CostError("COST_REPLAY_MISMATCH")


def simulate_costs(request: CostRequest) -> CostResult:
    """Replay all hypothetical orders in chronological/ID priority, without writes.

    The full bar's volume is a post-hoc liquidity approximation; fills are not
    credited until bar closure and cannot use a window extending beyond expiry.
    Intrabar touches/high/low never grant a favorable limit fill.
    """
    try:
        if len(request.model_dump_json().encode()) > 2_097_152:
            raise ValueError("input size")
        request = CostRequest.model_validate_json(request.model_dump_json())
    except ValueError:
        raise CostError("COST_INPUT_INVALID") from None
    input_digest = digest_json(request.model_dump(mode="json"))
    config = request.config
    orders = tuple(sorted(request.orders, key=lambda order: (order.created_at, order.candidate_id)))
    remaining = {order.candidate_id: order.quantity for order in orders}
    charged: set[str] = set()
    fills: list[HypotheticalFill] = []
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        total = Decimal(0)
        for item in request.bars:
            bar = item.bar
            liquidity = bar.volume * config.participation
            for order in orders:
                if (
                    remaining[order.candidate_id] == 0
                    or bar.bar_start
                    < order.created_at + timedelta(seconds=config.minimum_delay_seconds)
                    or item.closed_at > order.expires_at
                ):
                    continue
                direction = Decimal(1) if order.side == OrderSide.BUY else Decimal(-1)
                impact = bar.open * (config.half_spread_bps + config.slippage_bps) / 10000
                unrounded = bar.open + direction * impact
                rounding = ROUND_CEILING if direction == 1 else ROUND_FLOOR
                price = (unrounded / order.price_increment).to_integral_value(
                    rounding=rounding
                ) * order.price_increment
                if (
                    price <= 0
                    or (order.side == OrderSide.BUY and price > order.limit_price)
                    or (order.side == OrderSide.SELL and price < order.limit_price)
                ):
                    continue
                quantity = (
                    min(remaining[order.candidate_id], liquidity) / order.quantity_increment
                ).to_integral_value(rounding=ROUND_FLOOR) * order.quantity_increment
                if quantity <= 0:
                    continue
                commission = quantity * price * config.commission_bps / 10000
                if order.candidate_id not in charged:
                    commission += config.commission_flat
                # Money costs round upwards; never improve results via fractional fee loss.
                commission = commission.quantize(Decimal("0.00000001"), rounding=ROUND_CEILING)
                spread = quantity * bar.open * config.half_spread_bps / 10000
                all_impact = quantity * abs(price - bar.open)
                spread = spread.quantize(Decimal("0.00000001"), rounding=ROUND_FLOOR)
                slippage = (all_impact - spread).quantize(
                    Decimal("0.00000001"), rounding=ROUND_CEILING
                )
                cash = -direction * quantity * price - commission
                if len(fills) >= 4096:
                    raise CostError("COST_RESULT_LIMIT")
                fills.append(
                    HypotheticalFill(
                        candidate_id=order.candidate_id,
                        bar_start=bar.bar_start,
                        liquidity_window_end=item.closed_at,
                        quantity=quantity,
                        reference_price=bar.open,
                        price=price,
                        commission=commission,
                        spread_cost=spread,
                        slippage_and_rounding_cost=slippage,
                        cash_delta=cash.quantize(Decimal("0.00000001"), rounding=ROUND_FLOOR),
                        input_digest=input_digest,
                    )
                )
                remaining[order.candidate_id] -= quantity
                liquidity -= quantity
                charged.add(order.candidate_id)
                total += commission + spread + slippage
        result = CostResult(
            input_digest=input_digest,
            config_digest=digest_json(config.model_dump(mode="json")),
            fills=tuple(fills),
            unfilled=tuple(
                UnfilledQuantity(
                    candidate_id=order.candidate_id, quantity=remaining[order.candidate_id]
                )
                for order in orders
            ),
            total_cost=total,
            unit_test_mode=config.unit_test_zero_cost,
        )
        if len(result.model_dump_json().encode()) > 2_097_152:
            raise CostError("COST_RESULT_LIMIT")
        return result


class AdjustmentEvent(DomainModel):
    """One supplied effective split or actual cash-credit event (not ex-date inference)."""

    action_id: StableId
    kind: Literal["split", "dividend_credit"]
    effective_at: UtcDateTime
    available_at: UtcDateTime
    split_ratio: PositiveDecimal | None = None
    cash_per_share: NonNegativeDecimal | None = None
    entitled_quantity: NonNegativeDecimal | None = None

    @model_validator(mode="after")
    def _fields(self) -> Self:
        for amount in (self.split_ratio, self.cash_per_share, self.entitled_quantity):
            if amount is not None:
                _adjustment_bound(amount)
        if self.kind == "split":
            if (
                self.split_ratio is None
                or self.cash_per_share is not None
                or self.entitled_quantity is not None
            ):
                raise ValueError("split fields differ")
        elif (
            self.split_ratio is not None
            or self.cash_per_share is None
            or self.entitled_quantity is None
        ):
            raise ValueError("dividend requires explicitly established entitled shares")
        return self


class AdjustmentRequest(DomainModel):
    """One instrument/currency lot, explicitly supplied raw or adjusted convention."""

    adjustment: PriceAdjustment
    as_of: UtcDateTime
    quantity: NonNegativeDecimal
    basis_per_share: NonNegativeDecimal
    cash: NonNegativeDecimal
    events: Annotated[tuple[AdjustmentEvent, ...], Field(max_length=128)]
    already_applied: Annotated[tuple[StableId, ...], Field(max_length=128)]

    @model_validator(mode="after")
    def _events(self) -> Self:
        if max(self.quantity, self.basis_per_share, self.cash) > Decimal("1e12"):
            raise ValueError("adjustment economic bound")
        for amount in (self.quantity, self.basis_per_share, self.cash):
            _adjustment_bound(amount)
        ids = tuple(event.action_id for event in self.events)
        if len(set(ids)) != len(ids) or set(ids) & set(self.already_applied):
            raise ValueError("corporate action already applied")
        if len(set(self.already_applied)) != len(self.already_applied):
            raise ValueError("duplicate applied actions")
        clocks = tuple((event.effective_at, event.action_id) for event in self.events)
        if tuple(sorted(clocks)) != clocks:
            raise ValueError("actions must be ordered")
        if self.events and self.adjustment != PriceAdjustment.RAW:
            raise ValueError("cannot reapply corporate actions to adjusted prices/share basis")
        if any(max(event.effective_at, event.available_at) > self.as_of for event in self.events):
            raise ValueError("future corporate action")
        return self


class AdjustmentResult(DomainModel):
    input_digest: Digest
    adjustment: PriceAdjustment
    quantity: NonNegativeDecimal
    basis_per_share: NonNegativeDecimal
    cash: NonNegativeDecimal
    total_basis: NonNegativeDecimal
    applied: Annotated[tuple[StableId, ...], Field(max_length=256)]
    execution_enabled: Literal[False] = False


def _adjustment_bound(amount: Decimal) -> None:
    with localcontext() as context:
        context.prec = 50
        if amount > Decimal("1e12") or amount != amount.quantize(Decimal("0.00000001")):
            raise CostError("ADJUSTMENT_ECONOMIC_LIMIT")


def adjust_holdings(request: AdjustmentRequest) -> AdjustmentResult:
    """Pure lot transform; caller establishes currency and dividend entitlement.

    Adjusted-series lots remain unchanged with no action reapplication. Raw
    splits conserve authoritative total_basis; per-share basis is a 12-decimal
    display value. Explicit dividend cash credits do not alter basis.
    """
    try:
        request = AdjustmentRequest.model_validate_json(request.model_dump_json())
    except ValueError:
        raise CostError("ADJUSTMENT_INPUT_INVALID") from None
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        quantity, basis, cash = request.quantity, request.basis_per_share, request.cash
        total_basis = quantity * basis
        for event in request.events:
            if event.kind == "split" and event.split_ratio is not None:
                quantity *= event.split_ratio
            elif event.cash_per_share is not None and event.entitled_quantity is not None:
                cash += event.cash_per_share * event.entitled_quantity
            if max(quantity, cash) > Decimal("1e12"):
                raise CostError("ADJUSTMENT_ECONOMIC_LIMIT")
            _adjustment_bound(quantity)
        basis = (total_basis / quantity if quantity else basis).quantize(Decimal("0.000000000001"))
        if basis > Decimal("1e12"):
            raise CostError("ADJUSTMENT_ECONOMIC_LIMIT")
        return AdjustmentResult(
            input_digest=digest_json(request.model_dump(mode="json")),
            adjustment=request.adjustment,
            quantity=quantity,
            basis_per_share=basis,
            cash=cash,
            total_basis=total_basis,
            applied=request.already_applied + tuple(event.action_id for event in request.events),
        )
