"""Explicit quality expectations; no invented trading-session bar grids."""

from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from ainvest.data.models import OhlcvPage, PriceAdjustment
from ainvest.schemas.common import (
    DomainModel,
    InstrumentIdentity,
    MachineCode,
    QualityFlag,
    SchemaVersion,
    SourceId,
    UtcDateTime,
)


class BarExpectation(DomainModel):
    schema_version: SchemaVersion = "1.0"
    instrument: InstrumentIdentity
    provider: SourceId
    timeframe: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]?[mhdw]$")]
    adjustment: PriceAdjustment
    as_of: UtcDateTime
    max_age_seconds: Annotated[int, Field(strict=True, ge=0, le=31_536_000)]
    expected_starts: Annotated[tuple[UtcDateTime, ...], Field(min_length=1, max_length=10000)]

    @model_validator(mode="after")
    def _grid(self) -> Self:
        if tuple(sorted(set(self.expected_starts))) != self.expected_starts:
            raise ValueError("expected bar starts must be unique and ordered")
        if self.expected_starts[-1] > self.as_of:
            raise ValueError("expected grid is in the future")
        return self


class QualityReport(DomainModel):
    schema_version: SchemaVersion = "1.0"
    issues: tuple[MachineCode, ...]
    flags: tuple[QualityFlag, ...]


def assess_bars(page: OhlcvPage, expected: BarExpectation) -> QualityReport:
    """Fail on gaps/mismatches rather than sort, fill, resample or repair them."""
    issues: set[str] = set()
    flags = set(page.provenance.quality_flags)
    items = page.items
    if not items or len(items) > 10000:
        issues.add("BAR_COUNT_INVALID")
    if page.next_cursor is not None:
        issues.add("INCOMPLETE_PAGE")
    if page.provenance.source != expected.provider or any(
        bar.provenance.source != expected.provider for bar in items
    ):
        issues.add("PROVIDER_MISMATCH")
    if page.adjustment != expected.adjustment:
        issues.add("ADJUSTMENT_MISMATCH")
    if page.instrument_id != expected.instrument.instrument_id or any(
        bar.instrument != expected.instrument for bar in items
    ):
        issues.add("INSTRUMENT_MISMATCH")
    if page.interval != expected.timeframe or any(
        bar.interval != expected.timeframe for bar in items
    ):
        issues.add("TIMEFRAME_MISMATCH")
    starts = tuple(bar.bar_start for bar in items)
    if len(set(starts)) != len(starts):
        issues.add("DUPLICATE_BARS")
    if tuple(sorted(starts)) != starts:
        issues.add("UNORDERED_BARS")
    if starts != expected.expected_starts:
        issues.add("BAR_GRID_MISMATCH")
    provenances = (page.provenance, *(bar.provenance for bar in items))
    if expected.instrument.identity_as_of > expected.as_of or any(
        p.received_at > expected.as_of or p.observed_at > expected.as_of for p in provenances
    ):
        issues.add("FUTURE_BARS")
    if (
        items
        and (expected.as_of - items[-1].provenance.observed_at).total_seconds()
        > expected.max_age_seconds
    ):
        issues.add("STALE_BARS")
        flags.add(QualityFlag.STALE)
    if any(p.is_delayed for p in provenances):
        issues.add("DELAYED_BARS")
        flags.add(QualityFlag.DELAYED)
    for provenance in provenances:
        flags.update(provenance.quality_flags)
    if flags:
        issues.add("SOURCE_QUALITY_FLAGS")
    if issues:
        flags.add(QualityFlag.PARTIAL)
    return QualityReport(issues=tuple(sorted(issues)), flags=tuple(sorted(flags)))
