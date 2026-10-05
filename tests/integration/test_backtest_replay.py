"""Point-in-time isolated replay uses the same Strategy and sizing as Paper."""

from decimal import ROUND_HALF_EVEN, localcontext

from backtest_fixtures import replay_inputs

from ainvest.backtest.runner import run_replay
from ainvest.data.calendar_port import FakeMarketCalendar
from ainvest.portfolio.sizer import size_position
from ainvest.strategies.reference.moving_average.plugin import plugin
from ainvest.strategies.worker.runner import evaluate_in_worker


def test_same_context_has_paper_strategy_and_sizer_parity() -> None:
    data, points, config = replay_inputs()
    definition = plugin.strategy_definitions()[0]
    replay = run_replay(
        data, points[:1], config, definition=definition, calendar=FakeMarketCalendar()
    )
    assert replay.status == "complete", replay.error_code
    step = replay.steps[0]
    paper_worker = evaluate_in_worker(definition, params=config.params, context=step.context)
    assert paper_worker.result == step.strategy_result
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        assert definition.create(config.params).evaluate(step.context) == step.strategy_result
        assert step.sizing is not None and step.sizing.candidate is not None
        paper_sizing = size_position(
            signal=step.strategy_result.signals[0],
            quote=data.quotes[0],
            portfolio=step.context.portfolio,
            config=config.sizing,
            as_of=step.context.as_of,
            candidate_id=step.sizing.candidate.candidate_id,
        )
    assert paper_sizing == step.sizing
    assert not hasattr(step.context, "bars") and not hasattr(step.context, "dataset")
    assert not replay.execution_enabled
