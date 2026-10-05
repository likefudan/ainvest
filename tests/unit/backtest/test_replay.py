"""Real isolated reference strategy, no order execution or future data."""

import time
from datetime import timedelta
from decimal import Decimal

import pytest
from backtest_fixtures import replay_inputs

from ainvest.backtest.models import ReplayConfig, ReplayPoint, ReplayResult
from ainvest.backtest.runner import ReplayError, run_replay
from ainvest.data.calendar import ExchangeCalendar
from ainvest.data.calendar_port import FakeMarketCalendar
from ainvest.schemas.risk import RiskOutcome
from ainvest.schemas.strategy import SignalIntent
from ainvest.strategies.reference.moving_average.plugin import plugin
from ainvest.strategies.worker.codes import WorkerStatus
from ainvest.strategies.worker.runner import evaluate_in_worker


def test_deterministic_decisions_and_state_progression() -> None:
    data, points, config = replay_inputs()
    definition = plugin.strategy_definitions()[0]
    first = run_replay(data, points, config, definition=definition, calendar=FakeMarketCalendar())
    second = run_replay(data, points, config, definition=definition, calendar=FakeMarketCalendar())
    assert first.status == "complete", first.error_code
    assert first == second
    assert first.steps[0].strategy_result.signals[0].intent == SignalIntent.BUY
    assert first.steps[1].strategy_result.signals[0].intent == SignalIntent.HOLD
    assert first.steps[1].context.strategy_state == first.steps[0].strategy_result.next_state
    assert first.steps[0].sizing is not None and first.steps[0].sizing.candidate is not None
    assert first.steps[0].risk is not None
    assert first.steps[0].risk.decision.outcome == RiskOutcome.APPROVED
    assert not first.execution_enabled and not first.performance_report_available
    assert first == ReplayResult.model_validate_json(first.model_dump_json())


def test_unseen_future_suffix_cannot_change_visible_prefix_decision() -> None:
    data, points, config = replay_inputs()
    definition = plugin.strategy_definitions()[0]
    first = run_replay(
        data, points[:1], config, definition=definition, calendar=FakeMarketCalendar()
    )
    bars = list(data.bars)
    future = bars[-1]
    bars[-1] = future.model_copy(
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
    )
    modified = data.model_copy(update={"bars": tuple(bars)})
    second = run_replay(
        modified, points[:1], config, definition=definition, calendar=FakeMarketCalendar()
    )
    assert first.status == second.status == "complete"
    assert first.steps == second.steps and first.semantic_digest == second.semantic_digest
    assert first.dataset_digest != second.dataset_digest  # full artifact identity remains traceable


def test_late_received_bar_is_not_available_and_grid_gap_fails_closed() -> None:
    data, points, config = replay_inputs()
    bars = list(data.bars)
    old = bars[10]
    bars[10] = old.model_copy(
        update={
            "bar": old.bar.model_copy(
                update={
                    "provenance": old.bar.provenance.model_copy(
                        update={"received_at": points[0].as_of + timedelta(minutes=1)}
                    )
                }
            )
        }
    )
    result = run_replay(
        data.model_copy(update={"bars": tuple(bars)}),
        points[:1],
        config,
        definition=plugin.strategy_definitions()[0],
        calendar=FakeMarketCalendar(),
    )
    assert result.status == "error" and not result.steps


def test_future_portfolio_state_and_weakened_workers_are_rejected() -> None:
    data, points, config = replay_inputs()
    point = points[0]
    with pytest.raises(ValueError):
        ReplayPoint.model_validate_json(
            point.model_copy(
                update={"available_at": point.as_of + timedelta(seconds=1)}
            ).model_dump_json()
        )
    future_config = (
        config.model_copy(
            update={
                "initial_state": config.initial_state.model_copy(
                    update={"updated_at": point.as_of + timedelta(seconds=1)}
                )
            }
        )
        if config.initial_state
        else config
    )
    with pytest.raises(ReplayError, match="REPLAY_INPUT_INVALID"):
        run_replay(
            data,
            points,
            future_config,
            definition=plugin.strategy_definitions()[0],
            calendar=FakeMarketCalendar(),
        )
    with pytest.raises(ValueError):
        ReplayConfig.model_validate_json(
            config.model_copy(
                update={
                    "worker_limits": config.worker_limits.model_copy(
                        update={"block_network": False}
                    )
                }
            ).model_dump_json()
        )


def test_standard_risk_kill_switch_veto_is_retained() -> None:
    data, points, config = replay_inputs()
    point = points[0].model_copy(
        update={
            "kill_switch": points[0].kill_switch.model_copy(update={"operational_active": True})
        }
    )
    result = run_replay(
        data,
        (point,),
        config,
        definition=plugin.strategy_definitions()[0],
        calendar=FakeMarketCalendar(),
    )
    assert result.status == "complete"
    risk = result.steps[0].risk
    assert risk is not None and risk.decision.outcome == RiskOutcome.REJECTED
    assert "ORDERS_KILL_SWITCH" in risk.rule_codes


def test_stale_quote_fails_without_calling_worker() -> None:
    data, points, config = replay_inputs()
    quote = data.quotes[0]
    stale = quote.model_copy(
        update={
            "provenance": quote.provenance.model_copy(
                update={
                    "observed_at": points[0].as_of - timedelta(seconds=121),
                    "received_at": points[0].as_of - timedelta(seconds=121),
                }
            )
        }
    )
    result = run_replay(
        data.model_copy(update={"quotes": (stale, *data.quotes[1:])}),
        points[:1],
        config,
        definition=plugin.strategy_definitions()[0],
        calendar=FakeMarketCalendar(),
    )
    assert result.status == "error" and result.error_code == "REPLAY_QUOTE_QUALITY"
    assert not result.steps


@pytest.mark.parametrize("kind", ["failed", "binding"])
def test_worker_failure_or_context_spoof_cannot_produce_decision(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    import ainvest.backtest.runner as runner

    data, points, config = replay_inputs()
    definition = plugin.strategy_definitions()[0]
    baseline = run_replay(
        data, points[:1], config, definition=definition, calendar=FakeMarketCalendar()
    )
    worker = evaluate_in_worker(definition, params=config.params, context=baseline.steps[0].context)
    fake = (
        worker.model_copy(
            update={
                "status": WorkerStatus.FAILED,
                "result": None,
                "failure_message": "synthetic-private-canary",
            }
        )
        if kind == "failed"
        else worker.model_copy(update={"input_digest": "sha256:" + "0" * 64})
    )
    monkeypatch.setattr(runner, "evaluate_in_worker", lambda *args, **kwargs: fake)
    result = run_replay(
        data, points[:1], config, definition=definition, calendar=FakeMarketCalendar()
    )
    assert result.status == "error" and not result.steps
    assert result.error_code == (
        "REPLAY_WORKER_FAILED" if kind == "failed" else "REPLAY_WORKER_BINDING"
    )
    assert "synthetic-private-canary" not in result.model_dump_json()


def test_replay_semantic_digest_tamper_rejected() -> None:
    data, points, config = replay_inputs()
    result = run_replay(
        data,
        points[:1],
        config,
        definition=plugin.strategy_definitions()[0],
        calendar=FakeMarketCalendar(),
    )
    with pytest.raises(ValueError):
        ReplayResult.model_validate_json(
            result.model_copy(update={"semantic_digest": "sha256:" + "0" * 64}).model_dump_json()
        )


def test_busy_replay_is_rejected_without_queueing() -> None:
    import ainvest.backtest.runner as runner

    data, points, config = replay_inputs()
    assert runner._REPLAY_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(ReplayError, match="REPLAY_RUN_BUSY"):
            run_replay(
                data,
                points,
                config,
                definition=plugin.strategy_definitions()[0],
                calendar=FakeMarketCalendar(),
            )
    finally:
        runner._REPLAY_LOCK.release()


def test_total_deadline_discards_late_worker_result(monkeypatch: pytest.MonkeyPatch) -> None:
    import ainvest.backtest.runner as runner

    data, points, config = replay_inputs()
    definition = plugin.strategy_definitions()[0]
    baseline = run_replay(
        data, points[:1], config, definition=definition, calendar=FakeMarketCalendar()
    )
    worker = evaluate_in_worker(definition, params=config.params, context=baseline.steps[0].context)
    monkeypatch.setattr(runner, "evaluate_in_worker", lambda *args, **kwargs: worker)
    ticks = iter((0, 1, 2, 31))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 31))
    result = run_replay(
        data, points[:1], config, definition=definition, calendar=FakeMarketCalendar()
    )
    assert result.error_code == "REPLAY_TIMEOUT" and not result.steps


def test_local_exchange_calendar_version_and_horizon_are_fingerprinted() -> None:
    data, points, config = replay_inputs()
    day = points[0].as_of.date()
    result = run_replay(
        data,
        points[:1],
        config,
        definition=plugin.strategy_definitions()[0],
        calendar=ExchangeCalendar(valid_from=day, valid_through=day),
    )
    assert result.status == "complete" and result.steps[0].risk is not None
    assert result.steps[0].risk.decision.outcome == RiskOutcome.APPROVED
    fake = run_replay(
        data,
        points[:1],
        config,
        definition=plugin.strategy_definitions()[0],
        calendar=FakeMarketCalendar(),
    )
    assert fake.calendar_digest != result.calendar_digest
