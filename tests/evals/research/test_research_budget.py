"""Synthetic admission is sticky, bounded and never selects another model."""

from concurrent.futures import ThreadPoolExecutor
from decimal import ROUND_FLOOR, Decimal, localcontext

import pytest
from research_eval_fixtures import definitions, observation, rates

from ainvest.agents.research_budget import ResearchBudgetGuard, ResearchBudgetLimit


def guard(ceiling: str = "0.1") -> ResearchBudgetGuard:
    return ResearchBudgetGuard(ResearchBudgetLimit(ceiling_usd=Decimal(ceiling), rates=rates()))


def test_explicit_budget_reservation_settlement_and_idempotency() -> None:
    record = observation(definitions().cases[0]).archive.payload.agent.record
    budget = guard()
    decision = budget.reserve(record.run_id, input_tokens=1000, output_tokens=100)
    assert decision.allowed and decision.reservation_usd == Decimal("0.0028")
    settled = budget.settle(record)
    assert settled.spent_usd == Decimal("0.00044") and settled.reserved_usd == 0
    assert budget.settle(record) == settled
    with pytest.raises(ValueError, match="BUDGET_RUN_REUSED"):
        budget.reserve(record.run_id, input_tokens=1000, output_tokens=100)
    with pytest.raises(ValueError, match="BUDGET_SETTLEMENT_CONFLICT"):
        budget.settle(record.model_copy(update={"input_tokens": 101}))


def test_exhaustion_prevents_start_and_remains_paused_with_alert() -> None:
    budget = guard("0.0001")
    starts = 0
    admission = budget.reserve("eval_budget_rejected", input_tokens=100, output_tokens=30)
    if admission.allowed:
        starts += 1
    assert starts == 0 and budget.snapshot.paused
    assert budget.snapshot.alerts[0].code == "BUDGET_EXHAUSTED"
    assert not budget.reserve("eval_budget_later", input_tokens=0, output_tokens=0).allowed
    assert len(budget.snapshot.alerts) == 1


def test_unknown_usage_preserves_hold_and_pauses_new_research() -> None:
    record = observation(definitions().cases[0]).archive.payload.agent.record
    budget = guard()
    admission = budget.reserve(record.run_id, input_tokens=1000, output_tokens=100)
    settled = budget.settle(record.model_copy(update={"usage_complete": False}))
    assert settled.paused and settled.reserved_usd == admission.reservation_usd
    assert settled.alerts[0].code == "BUDGET_USAGE_UNKNOWN"
    assert not budget.reserve("eval_budget_after_unknown", input_tokens=1, output_tokens=1).allowed


def test_observed_excess_and_run_capacity_pause() -> None:
    record = observation(definitions().cases[0]).archive.payload.agent.record
    budget = guard()
    budget.reserve(record.run_id, input_tokens=1, output_tokens=1)
    assert budget.settle(record).alerts[0].code == "BUDGET_RESERVATION_EXCEEDED"
    bounded = ResearchBudgetGuard(
        ResearchBudgetLimit(ceiling_usd=Decimal(1), rates=rates(), max_runs=1)
    )
    assert bounded.reserve("eval_budget_first", input_tokens=1, output_tokens=1).allowed
    assert not bounded.reserve("eval_budget_second", input_tokens=1, output_tokens=1).allowed
    assert bounded.snapshot.alerts[0].code == "BUDGET_RUN_LIMIT"


def test_concurrent_admission_cannot_over_reserve() -> None:
    budget = guard("0.0005")

    def reserve(run_id: str) -> bool:
        return budget.reserve(run_id, input_tokens=100, output_tokens=30).allowed

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(reserve, ("eval_budget_race_a", "eval_budget_race_b")))
    assert sum(results) == 1 and budget.snapshot.reserved_usd == Decimal("0.00044")


def test_money_rounding_is_conservative_and_ambient_context_independent() -> None:
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_FLOOR
        assert rates().estimate(100, 30) == Decimal("0.00044")
    with pytest.raises(ValueError):
        rates().estimate(True, 1)
    with pytest.raises(ValueError):
        ResearchBudgetLimit.model_validate({"rates": rates()})


def test_unknown_usage_hold_covers_observed_lower_bound_even_after_excess() -> None:
    record = observation(definitions().cases[0]).archive.payload.agent.record
    budget = guard()
    budget.reserve(record.run_id, input_tokens=1, output_tokens=1)
    settled = budget.settle(record.model_copy(update={"usage_complete": False}))
    assert settled.paused and settled.reserved_usd == Decimal("0.00044")


def test_exact_exhaustion_and_missing_reservation() -> None:
    record = observation(definitions().cases[0]).archive.payload.agent.record
    budget = guard("0.00044")
    with pytest.raises(ValueError, match="BUDGET_RESERVATION_MISSING"):
        budget.settle(record)
    assert budget.reserve(record.run_id, input_tokens=100, output_tokens=30).allowed
    settled = budget.settle(record)
    assert settled.paused and settled.remaining_usd == 0
    assert settled.alerts[0].code == "BUDGET_EXHAUSTED"
