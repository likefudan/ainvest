"""Versioned report models for the strategy conformance suite."""

from __future__ import annotations

from pydantic import Field

from ainvest.schemas.common import SCHEMA_VERSION_V1, DomainModel, SchemaVersion
from ainvest.strategy_conformance.codes import ConformanceCode, ConformanceStatus


class CheckResult(DomainModel):
    """One named check outcome."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    check_id: str = Field(min_length=2, max_length=64)
    status: ConformanceStatus
    code: ConformanceCode
    message: str = Field(min_length=1, max_length=1024)
    duration_ms: float = Field(default=0.0, ge=0)
    details: dict[str, str] = Field(default_factory=dict)


class ConformanceReport(DomainModel):
    """Machine-readable suite report (also rendered as human text)."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    suite_version: str = Field(min_length=1, max_length=32)
    package_version: str = Field(min_length=1, max_length=64)
    plugin_id: str = Field(min_length=1, max_length=64)
    strategy_name: str = Field(min_length=1, max_length=64)
    strategy_version: str = Field(min_length=1, max_length=32)
    passed: bool
    checks: tuple[CheckResult, ...] = ()
    duration_ms: float = Field(ge=0)

    @property
    def failed_checks(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.status is ConformanceStatus.FAILED)


__all__ = ["CheckResult", "ConformanceReport"]
