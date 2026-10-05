"""Temporal evidence and rolling splits do not assert universal strategy safety."""

from datetime import UTC, datetime, timedelta

import pytest

from ainvest.backtest.validation import (
    DataUse,
    PrefixProbe,
    TemporalRequest,
    UniverseRecord,
    WalkForwardConfig,
    validate_temporal,
    walk_forward,
)
from ainvest.strategies.worker.digests import digest_json

CLOCK = datetime(2026, 7, 1, tzinfo=UTC)
DIGEST = digest_json({"synthetic": 1})


def inputs() -> TemporalRequest:
    return TemporalRequest(
        code_version="synthetic-v1",
        config_digest=DIGEST,
        data_digest=DIGEST,
        universe_coverage="complete_point_in_time",
        uses=(
            DataUse(
                data_id="data_bar_0001",
                kind="bar",
                instrument_id="fixture_inst",
                event_at=CLOCK,
                closed_at=CLOCK + timedelta(minutes=1),
                available_at=CLOCK + timedelta(minutes=1),
                used_at=CLOCK + timedelta(minutes=2),
                snapshot_digest=DIGEST,
            ),
        ),
        universe=(
            UniverseRecord(
                instrument_id="fixture_inst",
                known_at=CLOCK,
                member_from=CLOCK,
                member_through=CLOCK + timedelta(days=30),
                snapshot_digest=DIGEST,
            ),
        ),
        probes=(),
    )


def test_temporal_evidence_replays_and_missing_universe_is_not_pass() -> None:
    request = inputs()
    result = validate_temporal(request)
    assert result.passed and not result.live_eligible
    assert result == validate_temporal(
        TemporalRequest.model_validate_json(request.model_dump_json())
    )
    missing = validate_temporal(request.model_copy(update={"universe": ()}))
    assert not missing.passed
    assert "SURVIVORSHIP_UNVERIFIED" in {issue.code for issue in missing.issues}


def test_lookahead_and_late_filing_publication() -> None:
    request = inputs()
    future = request.uses[0].model_copy(update={"used_at": CLOCK})
    result = validate_temporal(request.model_copy(update={"uses": (future,)}))
    assert {issue.code for issue in result.issues} >= {"LOOKAHEAD_DATA", "BAR_NOT_CLOSED"}
    filing = future.model_copy(
        update={"kind": "filing", "closed_at": None, "published_at": CLOCK + timedelta(days=1)}
    )
    result = validate_temporal(request.model_copy(update={"uses": (filing,)}))
    assert "FILING_PUBLICATION_LEAK" in {issue.code for issue in result.issues}
    filing = filing.model_copy(update={"published_at": None})
    assert not validate_temporal(request.model_copy(update={"uses": (filing,)})).passed


def test_current_only_universe_and_outside_membership_rejected() -> None:
    request = inputs()
    for record in (
        request.universe[0].model_copy(update={"known_at": CLOCK + timedelta(days=1)}),
        request.universe[0].model_copy(update={"member_through": CLOCK + timedelta(seconds=1)}),
    ):
        assert not validate_temporal(request.model_copy(update={"universe": (record,)})).passed
    assert not validate_temporal(request.model_copy(update={"universe_coverage": "unknown"})).passed


def test_parameter_selection_must_be_frozen_before_test() -> None:
    request = inputs()
    use = request.uses[0].model_copy(
        update={"kind": "parameter", "instrument_id": None, "parameter_selection_deadline": CLOCK}
    )
    result = validate_temporal(request.model_copy(update={"uses": (use,)}))
    assert "PARAMETER_SELECTION_LEAK" in {issue.code for issue in result.issues}


def test_impossible_bar_capture_and_serialized_outcome_are_rejected() -> None:
    request = inputs()
    use = request.uses[0].model_copy(update={"closed_at": CLOCK - timedelta(seconds=1)})
    result = validate_temporal(request.model_copy(update={"uses": (use,)}))
    assert "BAR_TIME_INCONSISTENT" in {issue.code for issue in result.issues}
    with pytest.raises(ValueError):
        type(result).model_validate_json(
            result.model_copy(update={"passed": True}).model_dump_json()
        )


def test_deliberately_leaking_strategy_is_caught_by_future_suffix_probe() -> None:
    # Deliberately wrong strategy consults the final unseen value.
    def leaking_strategy(full_data: tuple[int, ...]) -> str:
        return digest_json({"buy": full_data[-1] > 10})

    probe = PrefixProbe(
        cutoff=CLOCK,
        baseline_dataset_digest=digest_json([1, 20]),
        perturbed_dataset_digest=digest_json([1, 2]),
        baseline_visible_digest=DIGEST,
        perturbed_visible_digest=DIGEST,
        baseline_decision_digest=leaking_strategy((1, 20)),
        perturbed_decision_digest=leaking_strategy((1, 2)),
    )
    result = validate_temporal(inputs().model_copy(update={"probes": (probe,)}))
    assert "FUTURE_SUFFIX_DEPENDENCE" in {issue.code for issue in result.issues}


def test_half_open_walk_forward_and_parameter_freeze() -> None:
    config = WalkForwardConfig(
        start_at=CLOCK,
        end_at=CLOCK + timedelta(days=12),
        train_seconds=3 * 86400,
        test_seconds=2 * 86400,
        step_seconds=2 * 86400,
        embargo_seconds=86400,
        parameter_digest=DIGEST,
        data_digest=DIGEST,
        code_version="v1",
    )
    result = walk_forward(config)
    assert len(result.folds) == 4
    assert all(fold.train_end < fold.test_start for fold in result.folds)
    assert result.folds[0].test_end == result.folds[1].test_start
    assert result == walk_forward(WalkForwardConfig.model_validate_json(config.model_dump_json()))
    overlapping = config.model_copy(update={"step_seconds": 86400})
    with pytest.raises(ValueError):
        walk_forward(overlapping)
    empty = config.model_copy(update={"end_at": CLOCK + timedelta(days=1)})
    with pytest.raises(ValueError):
        walk_forward(empty)
