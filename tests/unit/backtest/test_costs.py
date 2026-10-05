"""Explicit synthetic economics, never broker executions."""

from datetime import timedelta
from decimal import Decimal, localcontext

import pytest
from backtest_fixtures import replay_inputs

from ainvest.backtest.costs import (
    AdjustmentEvent,
    AdjustmentRequest,
    CostConfig,
    CostError,
    CostRequest,
    adjust_holdings,
    simulate_costs,
    verify_cost_replay,
)
from ainvest.data.models import PriceAdjustment
from ainvest.schemas.common import OrderSide
from ainvest.schemas.orders import CandidateOrder
from ainvest.schemas.portfolio import AccountScope


def cost_request() -> CostRequest:
    data, _, _ = replay_inputs()
    bar = data.bars[1]
    order = CandidateOrder(
        candidate_id="cand_cost_0001",
        signal_id="sig_cost_0001",
        account_scope=AccountScope.PAPER,
        instrument_id=data.instrument.instrument_id,
        symbol=data.instrument.symbol,
        exchange=data.instrument.exchange,
        currency=data.instrument.currency,
        asset_type=data.instrument.asset_type,
        side=OrderSide.BUY,
        quantity=Decimal(5),
        quantity_increment=Decimal(1),
        limit_price=Decimal(200),
        price_increment=Decimal("0.01"),
        maximum_notional=Decimal(1000),
        strategy="moving_average",
        strategy_version="1.0.0",
        created_at=bar.bar.bar_start - timedelta(seconds=2),
        expires_at=data.bars[4].closed_at,
    )
    bars = tuple(
        item.model_copy(update={"bar": item.bar.model_copy(update={"volume": Decimal(20)})})
        for item in data.bars[1:4]
    )
    return CostRequest(
        config=CostConfig(
            version="synthetic-v1",
            commission_flat=Decimal(1),
            commission_bps=Decimal(10),
            half_spread_bps=Decimal(5),
            slippage_bps=Decimal(5),
            participation=Decimal("0.10"),
            minimum_delay_seconds=1,
            unit_test_zero_cost=False,
        ),
        orders=(order,),
        bars=bars,
        adjustment=PriceAdjustment.RAW,
    )


def test_partial_volume_costs_and_deterministic_replay() -> None:
    request = cost_request()
    result = simulate_costs(request)
    assert tuple(fill.quantity for fill in result.fills) == (Decimal(2), Decimal(2), Decimal(1))
    assert not result.execution_enabled
    assert result.unfilled[0].quantity == 0
    assert result.fills[0].commission > 1
    assert result.fills[1].commission < 1
    assert all(fill.price > fill.reference_price for fill in result.fills)
    assert result.total_cost > 1
    assert result == simulate_costs(CostRequest.model_validate_json(request.model_dump_json()))
    verify_cost_replay(request, result)
    with pytest.raises(CostError, match="COST_REPLAY_MISMATCH"):
        verify_cost_replay(request, result.model_copy(update={"total_cost": Decimal(0)}))
    with localcontext() as context:
        context.prec = 12
        assert result == simulate_costs(request)


def test_shared_volume_and_stable_order_priority() -> None:
    request = cost_request()
    second = request.orders[0].model_copy(update={"candidate_id": "cand_cost_0002"})
    result = simulate_costs(request.model_copy(update={"orders": (second, request.orders[0])}))
    assert sum(fill.quantity for fill in result.fills) == 6
    assert result.fills[0].candidate_id == request.orders[0].candidate_id
    assert result.unfilled[1].quantity == 4


def test_limit_is_not_optimistically_clipped() -> None:
    request = cost_request()
    price = request.bars[0].bar.open
    order = request.orders[0].model_copy(update={"limit_price": price})
    result = simulate_costs(request.model_copy(update={"orders": (order,)}))
    assert not result.fills and result.total_cost == 0


def test_sell_is_adverse_and_no_future_expiry_volume() -> None:
    request = cost_request()
    order = request.orders[0].model_copy(update={"side": OrderSide.SELL, "limit_price": Decimal(1)})
    result = simulate_costs(request.model_copy(update={"orders": (order,)}))
    assert all(fill.price < fill.reference_price for fill in result.fills)
    assert all(fill.cash_delta > 0 for fill in result.fills)
    expired = order.model_copy(
        update={"expires_at": request.bars[0].closed_at - timedelta(seconds=1)}
    )
    assert not simulate_costs(request.model_copy(update={"orders": (expired,)})).fills


def test_same_bar_and_zero_cost_guards() -> None:
    request = cost_request()
    order = request.orders[0].model_copy(update={"created_at": request.bars[0].bar.bar_start})
    result = simulate_costs(request.model_copy(update={"orders": (order,)}))
    assert all(fill.bar_start > order.created_at for fill in result.fills)
    zero = request.config.model_copy(
        update={
            "commission_flat": Decimal(0),
            "commission_bps": Decimal(0),
            "half_spread_bps": Decimal(0),
            "slippage_bps": Decimal(0),
        }
    )
    with pytest.raises(ValueError):
        CostConfig.model_validate_json(zero.model_dump_json())
    zero = zero.model_copy(update={"unit_test_zero_cost": True})
    assert simulate_costs(request.model_copy(update={"config": zero})).unit_test_mode


def test_duplicate_identity_adjustment_quality_and_bounds_fail_closed() -> None:
    request = cost_request()
    for modified in (
        request.model_copy(update={"orders": request.orders * 2}),
        request.model_copy(update={"bars": request.bars * 2}),
        request.model_copy(update={"adjustment": PriceAdjustment.SPLIT}),
        request.model_copy(
            update={
                "orders": (
                    request.orders[0].model_copy(update={"account_scope": AccountScope.AGENTIC}),
                )
            }
        ),
    ):
        with pytest.raises(CostError):
            simulate_costs(modified)


def test_fractional_volume_tick_fee_and_economic_bounds() -> None:
    request = cost_request()
    order = request.orders[0].model_copy(
        update={
            "quantity": Decimal("0.3"),
            "quantity_increment": Decimal("0.1"),
            "price_increment": Decimal("0.1"),
        }
    )
    result = simulate_costs(request.model_copy(update={"orders": (order,)}))
    assert sum(fill.quantity for fill in result.fills) == Decimal("0.3")
    assert result.fills[0].price % Decimal("0.1") == 0
    assert result.fills[0].commission >= request.config.commission_flat
    oversized = order.model_copy(
        update={"quantity": Decimal("1e13"), "maximum_notional": Decimal("1e16")}
    )
    with pytest.raises(CostError, match="COST_INPUT_INVALID"):
        simulate_costs(request.model_copy(update={"orders": (oversized,)}))


def test_raw_split_and_explicit_entitled_dividend_no_double_adjustment() -> None:
    request = cost_request()
    clock = request.bars[0].closed_at
    split = AdjustmentEvent(
        action_id="action_split_0001",
        kind="split",
        effective_at=clock,
        available_at=clock,
        split_ratio=Decimal(2),
    )
    dividend = AdjustmentEvent(
        action_id="action_dividend_0001",
        kind="dividend_credit",
        effective_at=clock + timedelta(seconds=1),
        available_at=clock,
        cash_per_share=Decimal("0.5"),
        entitled_quantity=Decimal(20),
    )
    inputs = AdjustmentRequest(
        adjustment=PriceAdjustment.RAW,
        as_of=clock + timedelta(seconds=2),
        quantity=Decimal(10),
        basis_per_share=Decimal(100),
        cash=Decimal(5),
        events=(split, dividend),
        already_applied=(),
    )
    result = adjust_holdings(inputs)
    assert (result.quantity, result.basis_per_share, result.cash) == (
        Decimal(20),
        Decimal(50),
        Decimal(15),
    )
    assert result.total_basis == Decimal(1000)
    assert result == adjust_holdings(
        AdjustmentRequest.model_validate_json(inputs.model_dump_json())
    )
    for invalid in (
        inputs.model_copy(update={"adjustment": PriceAdjustment.SPLIT}),
        inputs.model_copy(update={"already_applied": (split.action_id,)}),
        inputs.model_copy(update={"as_of": clock - timedelta(seconds=1)}),
    ):
        with pytest.raises(CostError):
            adjust_holdings(invalid)
