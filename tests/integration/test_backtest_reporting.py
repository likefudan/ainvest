"""Synthetic cost ledger to explicit toy NAV, not a funded strategy backtest."""

from datetime import timedelta
from decimal import Decimal, localcontext

from backtest_fixtures import replay_inputs

from ainvest.backtest.costs import CostConfig, CostRequest, simulate_costs, verify_cost_replay
from ainvest.backtest.reporting import (
    NavPoint,
    ReportIdentity,
    ReportRequest,
    SampleInterval,
    generate_report,
    verify_report,
)
from ainvest.data.models import PriceAdjustment
from ainvest.schemas.common import OrderSide
from ainvest.schemas.orders import CandidateOrder
from ainvest.schemas.portfolio import AccountScope
from ainvest.strategies.worker.digests import digest_json


def test_cost_trace_and_caller_supplied_nav_report_replay() -> None:
    data, _, _ = replay_inputs()
    identity = data.instrument
    order = CandidateOrder(
        candidate_id="cand_report_0001",
        signal_id="sig_report_0001",
        account_scope=AccountScope.PAPER,
        instrument_id=identity.instrument_id,
        symbol=identity.symbol,
        exchange=identity.exchange,
        currency=identity.currency,
        asset_type=identity.asset_type,
        side=OrderSide.BUY,
        quantity=Decimal(5),
        quantity_increment=Decimal(1),
        limit_price=Decimal(200),
        price_increment=Decimal("0.01"),
        maximum_notional=Decimal(1000),
        strategy="moving_average",
        strategy_version="1.0.0",
        created_at=data.bars[1].bar.bar_start - timedelta(seconds=2),
        expires_at=data.bars[4].closed_at,
    )
    cost_inputs = CostRequest(
        config=CostConfig(
            version="synthetic-v1",
            commission_flat=Decimal(1),
            commission_bps=Decimal(10),
            half_spread_bps=Decimal(5),
            slippage_bps=Decimal(5),
            participation=Decimal("0.1"),
            minimum_delay_seconds=1,
            unit_test_zero_cost=False,
        ),
        adjustment=PriceAdjustment.RAW,
        orders=(order,),
        bars=tuple(
            item.model_copy(update={"bar": item.bar.model_copy(update={"volume": Decimal(20)})})
            for item in data.bars[1:4]
        ),
    )
    costs = simulate_costs(cost_inputs)
    verify_cost_replay(cost_inputs, costs)
    source = digest_json(costs.model_dump(mode="json"))
    clock = cost_inputs.orders[0].created_at
    points = [
        NavPoint(
            at=clock,
            gross_nav=Decimal(1000),
            net_nav=Decimal(1000),
            external_flow=Decimal(0),
            cost=Decimal(0),
            traded_notional=Decimal(0),
            source_digest=source,
        )
    ]
    with localcontext() as context:
        context.prec = 50
        cumulative = Decimal(0)
        for fill in costs.fills:
            fee = fill.commission + fill.spread_cost + fill.slippage_and_rounding_cost
            cumulative += fee
            # Explicit flat gross-NAV test assumption, not inferred marked positions.
            points.append(
                NavPoint(
                    at=fill.liquidity_window_end,
                    gross_nav=Decimal(1000),
                    net_nav=Decimal(1000) - cumulative,
                    external_flow=Decimal(0),
                    cost=fee,
                    traded_notional=fill.quantity * fill.reference_price,
                    source_digest=source,
                )
            )
    inputs = ReportRequest(
        identity=ReportIdentity(
            code_version="toy-v1",
            code_digest=source,
            strategy_version="synthetic-v1",
            strategy_digest=source,
            parameter_digest=costs.config_digest,
            config_digest=costs.config_digest,
            data_digest=costs.input_digest,
            cost_digest=source,
            validation_digest=digest_json({"synthetic": "not-production-qualified"}),
        ),
        currency="USD",
        cash_flow_timing="end_of_period",
        points=tuple(points),
        interval=SampleInterval(start_at=points[0].at, end_at=points[-1].at),
        benchmark=None,
        annualization=None,
        walk_forward=None,
    )
    report = generate_report(inputs)
    verify_report(inputs, report)
    assert report.sections[0].total_cost == costs.total_cost
    assert report.identity.cost_digest == source
    assert report.sections[0].gross is not None
    assert report.sections[0].gross.total_return == 0
    assert report.sections[0].net is not None and report.sections[0].net.total_return < 0
    assert not report.execution_enabled and not report.scheduled_paper_eligible
