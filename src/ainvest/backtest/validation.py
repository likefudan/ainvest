"""Supplied point-in-time evidence and bounded non-overlapping test windows."""

from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from ainvest.backtest.models import Version
from ainvest.data.indicators import Digest
from ainvest.schemas.common import DomainModel, MachineCode, StableId, UtcDateTime
from ainvest.strategies.worker.digests import digest_json

InstrumentId = Annotated[str, StringConstraints(min_length=3, max_length=128)]


class DataUse(DomainModel):
    """Trusted capture metadata for one strategy/parameter use, not source authentication."""

    data_id: StableId
    kind: Literal["bar", "filing", "news", "parameter"]
    instrument_id: InstrumentId | None = None
    event_at: UtcDateTime
    available_at: UtcDateTime
    used_at: UtcDateTime
    published_at: UtcDateTime | None = None
    closed_at: UtcDateTime | None = None
    parameter_selection_deadline: UtcDateTime | None = None
    snapshot_digest: Digest


class UniverseRecord(DomainModel):
    """Historical membership, including removed instruments, known at that time."""

    instrument_id: InstrumentId
    known_at: UtcDateTime
    member_from: UtcDateTime
    member_through: UtcDateTime
    snapshot_digest: Digest

    @model_validator(mode="after")
    def _interval(self) -> Self:
        if self.member_through <= self.member_from:
            raise ValueError("membership interval is empty")
        return self


class PrefixProbe(DomainModel):
    """Retained outputs of two runs differing only in their unseen suffix."""

    cutoff: UtcDateTime
    baseline_dataset_digest: Digest
    perturbed_dataset_digest: Digest
    baseline_visible_digest: Digest
    perturbed_visible_digest: Digest
    baseline_decision_digest: Digest
    perturbed_decision_digest: Digest


class TemporalRequest(DomainModel):
    code_version: Version
    config_digest: Digest
    data_digest: Digest
    universe_coverage: Literal["complete_point_in_time", "unknown"]
    uses: Annotated[tuple[DataUse, ...], Field(min_length=1, max_length=512)]
    universe: Annotated[tuple[UniverseRecord, ...], Field(max_length=512)]
    probes: Annotated[tuple[PrefixProbe, ...], Field(max_length=64)]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len({item.data_id for item in self.uses}) != len(self.uses):
            raise ValueError("duplicate data uses")
        return self


class TemporalIssue(DomainModel):
    code: MachineCode
    item_id: Annotated[str, StringConstraints(min_length=1, max_length=160)]


class TemporalResult(DomainModel):
    validator_version: Literal["temporal-evidence-v1"] = "temporal-evidence-v1"
    input_digest: Digest
    issues: tuple[TemporalIssue, ...]
    passed: bool
    live_eligible: Literal[False] = False
    universal_leakage_proof: Literal[False] = False

    @model_validator(mode="after")
    def _outcome(self) -> Self:
        if self.passed != (not self.issues):
            raise ValueError("temporal outcome differs from issues")
        return self


def validate_temporal(request: TemporalRequest) -> TemporalResult:
    """Fail incomplete universe/filing evidence; never infer availability from event dates."""
    request = TemporalRequest.model_validate_json(request.model_dump_json())
    issues: list[TemporalIssue] = []

    def add(code: str, item_id: str) -> None:
        issues.append(TemporalIssue(code=code, item_id=item_id))

    if request.universe_coverage != "complete_point_in_time":
        add("SURVIVORSHIP_UNVERIFIED", "universe")
    for use in request.uses:
        if use.available_at < use.event_at:
            add("DATA_TIME_INCONSISTENT", use.data_id)
        if max(use.event_at, use.available_at) > use.used_at:
            add("LOOKAHEAD_DATA", use.data_id)
        if use.kind == "bar" and (use.closed_at is None or use.closed_at > use.used_at):
            add("BAR_NOT_CLOSED", use.data_id)
        if (
            use.kind == "bar"
            and use.closed_at is not None
            and (use.closed_at <= use.event_at or use.available_at < use.closed_at)
        ):
            add("BAR_TIME_INCONSISTENT", use.data_id)
        if use.kind == "filing":
            if use.published_at is None:
                add("FILING_PUBLICATION_UNVERIFIED", use.data_id)
            elif use.published_at > use.used_at or use.published_at > use.available_at:
                add("FILING_PUBLICATION_LEAK", use.data_id)
        if use.kind == "news" and (
            use.published_at is None or use.published_at > min(use.used_at, use.available_at)
        ):
            add("NEWS_PUBLICATION_UNVERIFIED", use.data_id)
        if use.kind == "parameter" and (
            use.parameter_selection_deadline is None
            or use.available_at > use.parameter_selection_deadline
            or use.parameter_selection_deadline > use.used_at
        ):
            add("PARAMETER_SELECTION_LEAK", use.data_id)
        if use.kind != "parameter":
            if use.instrument_id is None:
                add("SURVIVORSHIP_UNVERIFIED", use.data_id)
            else:
                records = tuple(
                    record
                    for record in request.universe
                    if record.instrument_id == use.instrument_id and record.known_at <= use.used_at
                )
                if not records:
                    add("SURVIVORSHIP_UNVERIFIED", use.data_id)
                elif not any(
                    record.member_from <= use.used_at < record.member_through for record in records
                ):
                    add("UNIVERSE_MEMBERSHIP_INVALID", use.data_id)
    for index, probe in enumerate(request.probes):
        label = "probe_" + str(index)
        if probe.baseline_dataset_digest == probe.perturbed_dataset_digest:
            add("FUTURE_SUFFIX_PROBE_INEFFECTIVE", label)
        if probe.baseline_visible_digest != probe.perturbed_visible_digest:
            add("FUTURE_SUFFIX_VISIBLE_CHANGE", label)
        if probe.baseline_decision_digest != probe.perturbed_decision_digest:
            add("FUTURE_SUFFIX_DEPENDENCE", label)
    return TemporalResult(
        input_digest=digest_json(request.model_dump(mode="json")),
        issues=tuple(issues),
        passed=not issues,
    )


class WalkForwardConfig(DomainModel):
    start_at: UtcDateTime
    end_at: UtcDateTime
    train_seconds: Annotated[int, Field(ge=1, le=315_576_000)]
    test_seconds: Annotated[int, Field(ge=1, le=315_576_000)]
    step_seconds: Annotated[int, Field(ge=1, le=315_576_000)]
    embargo_seconds: Annotated[int, Field(ge=0, le=315_576_000)]
    parameter_digest: Digest
    data_digest: Digest
    code_version: Version

    @model_validator(mode="after")
    def _windows(self) -> Self:
        if self.end_at <= self.start_at or self.step_seconds < self.test_seconds:
            raise ValueError("empty horizon or overlapping out-of-sample windows")
        return self


class WalkForwardFold(DomainModel):
    index: Annotated[int, Field(ge=0, le=63)]
    train_start: UtcDateTime
    train_end: UtcDateTime
    test_start: UtcDateTime
    test_end: UtcDateTime
    parameter_selection_deadline: UtcDateTime
    parameter_digest: Digest

    @model_validator(mode="after")
    def _separation(self) -> Self:
        if not (self.train_start < self.train_end <= self.test_start < self.test_end) or (
            self.parameter_selection_deadline != self.train_end
        ):
            raise ValueError("invalid train/test separation or parameter deadline")
        return self


class WalkForwardResult(DomainModel):
    input_digest: Digest
    folds: Annotated[tuple[WalkForwardFold, ...], Field(min_length=1, max_length=64)]
    intervals: Literal["half_open"] = "half_open"
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def _separation(self) -> Self:
        if tuple(fold.index for fold in self.folds) != tuple(range(len(self.folds))):
            raise ValueError("fold indices differ")
        if any(
            left.test_end > right.test_start
            for left, right in zip(self.folds, self.folds[1:], strict=False)
        ):
            raise ValueError("overlapping out-of-sample periods")
        return self


def walk_forward(config: WalkForwardConfig) -> WalkForwardResult:
    """Generate rolling train/embargo/test schedules; no fitting or test-driven optimization."""
    config = WalkForwardConfig.model_validate_json(config.model_dump_json())
    folds: list[WalkForwardFold] = []
    start = config.start_at
    while True:
        train_end = start + timedelta(seconds=config.train_seconds)
        test_start = train_end + timedelta(seconds=config.embargo_seconds)
        test_end = test_start + timedelta(seconds=config.test_seconds)
        if test_end > config.end_at:
            break
        if len(folds) >= 64:
            raise ValueError("WALK_FORWARD_LIMIT")
        folds.append(
            WalkForwardFold(
                index=len(folds),
                train_start=start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                parameter_selection_deadline=train_end,
                parameter_digest=config.parameter_digest,
            )
        )
        start += timedelta(seconds=config.step_seconds)
    if not folds:
        raise ValueError("WALK_FORWARD_EMPTY")
    return WalkForwardResult(
        input_digest=digest_json(config.model_dump(mode="json")), folds=tuple(folds)
    )
