"""Actual isolated replay followed by diagnostic costs and temporal evidence checks."""

from datetime import timedelta
from decimal import Decimal

from backtest_fixtures import replay_inputs

from ainvest.backtest.costs import CostConfig, CostRequest, simulate_costs, verify_cost_replay
from ainvest.backtest.runner import run_replay
from ainvest.backtest.validation import (
    DataUse,
    PrefixProbe,
    TemporalRequest,
    UniverseRecord,
    validate_temporal,
)
from ainvest.data.calendar_port import FakeMarketCalendar
from ainvest.data.models import PriceAdjustment
from ainvest.strategies.reference.moving_average.plugin import plugin
from ainvest.strategies.worker.digests import digest_json


def test_real_replay_candidate_cost_and_future_prefix_evidence() -> None:
    data, points, config = replay_inputs()
    definition = plugin.strategy_definitions()[0]
    first = run_replay(
        data, points[:1], config, definition=definition, calendar=FakeMarketCalendar()
    )
    assert first.status == "complete"
    sizing = first.steps[0].sizing
    assert sizing is not None and sizing.candidate is not None
    request = CostRequest(
        config=CostConfig(
            version="synthetic-integration-v1",
            commission_flat=Decimal(1),
            commission_bps=Decimal(1),
            half_spread_bps=Decimal(5),
            slippage_bps=Decimal(5),
            participation=Decimal("0.01"),
            minimum_delay_seconds=1,
            unit_test_zero_cost=False,
        ),
        adjustment=PriceAdjustment.RAW,
        orders=(sizing.candidate,),
        bars=data.bars,
    )
    costs = simulate_costs(request)
    verify_cost_replay(request, costs)
    # Next observed opening price exceeds this conservative buy limit. Never clip it.
    assert not costs.fills and not costs.execution_enabled
    future = data.bars[-1]
    changed = data.model_copy(
        update={
            "bars": (
                *data.bars[:-1],
                future.model_copy(
                    update={
                        "bar": future.bar.model_copy(
                            update={
                                "open": Decimal(999),
                                "high": Decimal(1000),
                                "low": Decimal(998),
                                "close": Decimal(999),
                            }
                        )
                    }
                ),
            )
        }
    )
    second = run_replay(
        changed, points[:1], config, definition=definition, calendar=FakeMarketCalendar()
    )
    point = points[0]
    used_bar = data.bars[59]
    check = TemporalRequest(
        code_version=config.code_version,
        config_digest=first.config_digest,
        data_digest=first.dataset_digest,
        universe_coverage="complete_point_in_time",
        uses=(
            DataUse(
                data_id="data_integration_0001",
                kind="bar",
                instrument_id=data.instrument.instrument_id,
                event_at=used_bar.bar.bar_start,
                closed_at=used_bar.closed_at,
                available_at=used_bar.bar.provenance.received_at,
                used_at=point.as_of,
                snapshot_digest=digest_json(used_bar.model_dump(mode="json")),
            ),
        ),
        universe=(
            UniverseRecord(
                instrument_id=data.instrument.instrument_id,
                known_at=data.instrument.identity_as_of,
                member_from=data.bars[0].bar.bar_start,
                member_through=point.as_of + timedelta(days=1),
                snapshot_digest=first.dataset_digest,
            ),
        ),
        probes=(
            PrefixProbe(
                cutoff=point.as_of,
                baseline_dataset_digest=first.dataset_digest,
                perturbed_dataset_digest=second.dataset_digest,
                baseline_visible_digest=first.steps[0].visible_input_digest,
                perturbed_visible_digest=second.steps[0].visible_input_digest,
                baseline_decision_digest=first.semantic_digest,
                perturbed_decision_digest=second.semantic_digest,
            ),
        ),
    )
    assert validate_temporal(check).passed
