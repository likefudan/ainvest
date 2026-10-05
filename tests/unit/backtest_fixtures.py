"""Bounded synthetic raw bars, explicit closures, Paper state and test policy."""

from datetime import timedelta
from decimal import Decimal

from indicator_fixtures import sample

from ainvest.backtest.models import ClosedHistoricalBar, ReplayConfig, ReplayMarketData, ReplayPoint
from ainvest.orchestrator.fixtures import (
    make_exposure_inputs,
    make_instrument,
    make_risk_config,
    make_sizing_config,
)
from ainvest.risk.models import AllowlistEntry, KillSwitchSnapshot
from ainvest.schemas.common import Provenance
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.portfolio import AccountScope, ExposureSnapshot, PortfolioSnapshot
from ainvest.schemas.strategy import StrategyState, StrategyStateItem, StrategyStateValueKind
from ainvest.strategies.worker.protocol import WorkerLimits


def replay_inputs() -> tuple[ReplayMarketData, tuple[ReplayPoint, ...], ReplayConfig]:
    page, expected = sample(62)
    instrument = expected.instrument
    cutoffs = tuple(page.items[index].provenance.received_at for index in (59, 60))
    quotes = tuple(
        MarketQuote(
            instrument=instrument,
            last_price=Decimal(101 + index),
            bid=Decimal(101 + index) - Decimal("0.01"),
            ask=Decimal(101 + index) + Decimal("0.01"),
            provenance=page.items[index].provenance,
        )
        for index in (59, 60, 61)
    )
    data = ReplayMarketData(
        instrument=instrument,
        timeframe="1m",
        bars=tuple(
            ClosedHistoricalBar(bar=bar, closed_at=bar.provenance.observed_at) for bar in page.items
        ),
        quotes=quotes,
    )
    points = []
    for index, cutoff in enumerate(cutoffs):
        portfolio = PortfolioSnapshot(
            snapshot_id="replay_port_" + str(index),
            account_scope=AccountScope.PAPER,
            as_of=cutoff,
            cash=Decimal(10_000),
            buying_power=Decimal(10_000),
            equity=Decimal(10_000),
            exposure=ExposureSnapshot(
                cash=Decimal(10_000),
                equity=Decimal(10_000),
                gross_market_value=Decimal(0),
                net_market_value=Decimal(0),
                largest_position_weight=Decimal(0),
            ),
            provenance=Provenance(
                source="fixture.replay.portfolio", observed_at=cutoff, received_at=cutoff
            ),
        )
        metadata = make_instrument(
            instrument_id=instrument.instrument_id,
            symbol=instrument.symbol,
            exchange=instrument.exchange,
            currency=instrument.currency,
            asset_type=instrument.asset_type,
        )
        points.append(
            ReplayPoint(
                as_of=cutoff,
                available_at=cutoff,
                expected_starts=tuple(bar.bar_start for bar in page.items[: 60 + index]),
                portfolio=portfolio,
                instrument_metadata=metadata,
                exposure_inputs=make_exposure_inputs(instrument_id=instrument.instrument_id),
                short_term_volatility_bps=Decimal(100),
                kill_switch=KillSwitchSnapshot(
                    configured_active=False, operational_active=False, updated_at=cutoff
                ),
                recent_submissions=(),
            )
        )
    risk = make_risk_config(
        allowlist=(
            AllowlistEntry(
                instrument_id=instrument.instrument_id,
                symbol=instrument.symbol,
                exchange=instrument.exchange,
                currency=instrument.currency,
                asset_type=instrument.asset_type,
            ),
        )
    )
    config = ReplayConfig(
        run_id="backtest_synthetic_001",
        code_version="offline-test-v1",
        config_version="synthetic-policy-v1",
        params={"fast_window": 20, "slow_window": 50, "target_weight": "0.10"},
        initial_state=StrategyState(
            strategy="moving_average",
            strategy_version="1.0.0",
            updated_at=cutoffs[0] - timedelta(minutes=1),
            entries=(
                StrategyStateItem(
                    key="fast_above_slow", kind=StrategyStateValueKind.BOOLEAN, boolean_value=False
                ),
            ),
        ),
        sizing=make_sizing_config(),
        risk=risk,
        worker_limits=WorkerLimits(),
        max_age_seconds=120,
        duration_seconds=30,
    )
    return data, tuple(points), config
