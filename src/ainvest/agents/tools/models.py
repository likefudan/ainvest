"""Research-local contracts; no broker types or credentials."""

from typing import Annotated, Literal, Self

from pydantic import Field, SerializeAsAny, model_validator

from ainvest.data.indicators import Digest
from ainvest.data.models import OhlcvPage
from ainvest.data.providers.sec import SecFilingMetadata
from ainvest.data.quality import BarExpectation
from ainvest.schemas.common import (
    CurrencyCode,
    DomainModel,
    InstrumentIdentity,
    MachineCode,
    Money,
    Provenance,
    QualityFlag,
    Quantity,
    SchemaVersion,
    StableId,
    UtcDateTime,
    Weight,
)
from ainvest.schemas.research import EvidenceCitation

ToolName = Literal[
    "quote",
    "price_book",
    "history",
    "indicators",
    "filings",
    "news",
    "concentration",
    "buying_power",
]
TOOL_NAMES: tuple[ToolName, ...] = (
    "quote",
    "price_book",
    "history",
    "indicators",
    "filings",
    "news",
    "concentration",
    "buying_power",
)


class ToolScope(DomainModel):
    schema_version: SchemaVersion = "1.0"
    run_id: StableId
    instrument: InstrumentIdentity
    as_of: UtcDateTime
    max_age_seconds: Annotated[int, Field(strict=True, ge=0, le=86400)]
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=30)] = 10

    @model_validator(mode="after")
    def _identity_cutoff(self) -> Self:
        if self.instrument.identity_as_of > self.as_of:
            raise ValueError("instrument identity is after research cutoff")
        return self


class HistoryInput(DomainModel):
    schema_version: SchemaVersion = "1.0"
    expected: BarExpectation

    @model_validator(mode="after")
    def _size(self) -> Self:
        if len(self.expected.expected_starts) > 500:
            raise ValueError("research history is bounded to 500 bars")
        return self


class HistoryData(DomainModel):
    schema_version: SchemaVersion = "1.0"
    expected: BarExpectation
    bars: OhlcvPage


class FilingPage(DomainModel):
    schema_version: SchemaVersion = "1.0"
    instrument: InstrumentIdentity
    items: Annotated[tuple[SecFilingMetadata, ...], Field(max_length=100)]
    provenance: Provenance
    has_more: bool = False

    @model_validator(mode="after")
    def _capture(self) -> Self:
        accessions = [item.accession_number for item in self.items]
        if len(set(accessions)) != len(accessions):
            raise ValueError("duplicate filing accession")
        if any(
            item.provenance.source != self.provenance.source
            or item.provenance.observed_at > self.provenance.observed_at
            or item.provenance.received_at > self.provenance.received_at
            for item in self.items
        ):
            raise ValueError("filing capture does not contain its item provenance")
        return self


class PortfolioMetrics(DomainModel):
    """Only symbol context and aggregate amounts; no account/order identifiers."""

    schema_version: SchemaVersion = "1.0"
    calculation_version: Literal["portfolio-ratios-decimal-8dp-v1"] = (
        "portfolio-ratios-decimal-8dp-v1"
    )
    input_digest: Digest
    instrument: InstrumentIdentity
    currency: CurrencyCode
    quantity: Quantity
    market_value: Money
    portfolio_weight: Weight
    largest_position_weight: Weight
    position_count: Annotated[int, Field(strict=True, ge=0, le=500)]
    cash: Money
    equity: Money
    buying_power: Money
    provenance: Provenance


class BuyingPowerData(DomainModel):
    schema_version: SchemaVersion = "1.0"
    currency: CurrencyCode
    buying_power: Money
    cash: Money
    equity: Money
    provenance: Provenance


class ToolResult[T: DomainModel](DomainModel):
    schema_version: SchemaVersion = "1.0"
    tool: ToolName
    run_id: StableId
    as_of: UtcDateTime
    status: Literal["complete", "partial", "error"]
    data: SerializeAsAny[T] | None = None
    evidence: Annotated[tuple[EvidenceCitation, ...], Field(max_length=128)] = ()
    quality_flags: tuple[QualityFlag, ...] = ()
    error_code: MachineCode | None = None

    @model_validator(mode="after")
    def _status(self) -> Self:
        if self.status == "error":
            if (
                self.error_code is None
                or self.data is not None
                or self.evidence
                or not self.quality_flags
            ):
                raise ValueError("error result must withhold data and evidence")
        elif self.data is None or self.error_code is not None or not self.evidence:
            raise ValueError("successful result requires data and evidence")
        if self.status == "complete" and self.quality_flags:
            raise ValueError("quality flags prohibit complete status")
        if self.status == "partial" and not self.quality_flags:
            raise ValueError("partial result requires quality flags")
        for item in self.evidence:
            if item.provenance.observed_at > self.as_of or item.provenance.received_at > self.as_of:
                raise ValueError("future evidence")
            if not set(item.provenance.quality_flags).issubset(self.quality_flags):
                raise ValueError("result must retain evidence quality flags")
        return self
