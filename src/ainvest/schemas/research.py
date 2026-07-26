"""Research packet, thesis, and evidence schemas (P02-T1)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, StringConstraints, field_validator, model_validator

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    DomainModel,
    InstrumentIdentity,
    PnL,
    Provenance,
    QualityFlag,
    SchemaVersion,
    SourceId,
    StableId,
    Symbol,
    UtcDateTime,
)
from ainvest.schemas.market import (
    ResearchMarketSection,
    ResearchPortfolioSection,
    TechnicalIndicators,
)


class EvidenceKind(StrEnum):
    """Allowed evidence categories. Free-form natural language is not evidence."""

    FILING = "FILING"
    QUOTE = "QUOTE"
    OHLCV = "OHLCV"
    TECHNICAL = "TECHNICAL"
    FUNDAMENTAL = "FUNDAMENTAL"
    EVENT = "EVENT"
    CALCULATED = "CALCULATED"


class EvidenceCitation(DomainModel):
    """Provenanced citation for a research claim.

    Natural-language assertions without a deterministic source/locator cannot
    validate. Numeric values must cite a calculation or tool source.
    """

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    evidence_id: StableId
    kind: EvidenceKind
    summary: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    provenance: Provenance
    locator: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    numeric_value: PnL | None = None
    calculation_source: SourceId | None = None

    @model_validator(mode="after")
    def _require_deterministic_binding(self) -> EvidenceCitation:
        if self.kind is EvidenceKind.CALCULATED and self.calculation_source is None:
            raise ValueError("CALCULATED evidence requires calculation_source")
        if self.numeric_value is not None and self.calculation_source is None:
            raise ValueError("numeric evidence requires calculation_source")
        if self.kind is EvidenceKind.CALCULATED and self.numeric_value is None:
            raise ValueError("CALCULATED evidence requires numeric_value")
        return self


class ThesisSection(DomainModel):
    """Structured thesis bullets. These are narrative, not evidence."""

    bull_case: tuple[str, ...] = ()
    bear_case: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()

    @field_validator("bull_case", "bear_case", "risks", mode="before")
    @classmethod
    def _coerce_lists(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            raise ValueError("thesis entries must be a list of strings")
        return value

    @field_validator("bull_case", "bear_case", "risks")
    @classmethod
    def _non_empty_entries(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(item.strip() for item in value)
        if any(not item for item in cleaned):
            raise ValueError("thesis entries must be non-empty strings")
        return cleaned


def _reject_after_as_of(label: str, moment: datetime, as_of: datetime) -> None:
    if moment > as_of:
        raise ValueError(f"{label} must be <= research as_of")


class ResearchPacket(DomainModel):
    """AI research output consumed by strategies (design.md §6.1)."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    research_id: StableId
    symbol: Symbol
    as_of: UtcDateTime
    instrument: InstrumentIdentity | None = None
    market: ResearchMarketSection
    technical: TechnicalIndicators | None = None
    portfolio: ResearchPortfolioSection | None = None
    thesis: ThesisSection = Field(default_factory=ThesisSection)
    evidence: tuple[EvidenceCitation, ...] = ()
    quality_flags: tuple[QualityFlag, ...] = ()

    @field_validator("evidence", "quality_flags", mode="before")
    @classmethod
    def _coerce_sequences(cls, value: object) -> object:
        if value is None:
            return ()
        return value

    @model_validator(mode="after")
    def _consistency(self) -> ResearchPacket:
        if self.instrument is not None:
            if self.instrument.symbol != self.symbol:
                raise ValueError("instrument.symbol must match research packet symbol")
            if self.market.currency != self.instrument.currency:
                raise ValueError("market.currency must match instrument.currency")
            _reject_after_as_of(
                "instrument.identity_as_of",
                self.instrument.identity_as_of,
                self.as_of,
            )

        if self.technical is not None and self.technical.symbol != self.symbol:
            raise ValueError("technical.symbol must match research packet symbol")

        _reject_after_as_of(
            "market.provenance.observed_at",
            self.market.provenance.observed_at,
            self.as_of,
        )
        _reject_after_as_of(
            "market.provenance.received_at",
            self.market.provenance.received_at,
            self.as_of,
        )

        if self.technical is not None:
            _reject_after_as_of(
                "technical.provenance.observed_at",
                self.technical.provenance.observed_at,
                self.as_of,
            )
            _reject_after_as_of(
                "technical.provenance.received_at",
                self.technical.provenance.received_at,
                self.as_of,
            )

        for index, citation in enumerate(self.evidence):
            _reject_after_as_of(
                f"evidence[{index}].provenance.observed_at",
                citation.provenance.observed_at,
                self.as_of,
            )
            _reject_after_as_of(
                f"evidence[{index}].provenance.received_at",
                citation.provenance.received_at,
                self.as_of,
            )

        stale_flags = set(self.quality_flags) | set(self.market.provenance.quality_flags)
        if self.market.provenance.is_delayed and QualityFlag.DELAYED not in stale_flags:
            raise ValueError("delayed market data must set DELAYED quality flag")
        return self

    def flagged_stale(self) -> bool:
        """True when the packet or market provenance marks stale/delayed data."""
        flags = set(self.quality_flags) | set(self.market.provenance.quality_flags)
        return (
            bool(flags & {QualityFlag.STALE, QualityFlag.DELAYED})
            or self.market.provenance.is_delayed
        )


def research_packet_example() -> dict[str, Any]:
    """Return the design.md §6.1 example enriched with required provenance."""
    as_of = "2026-07-24T18:30:00Z"
    observed = "2026-07-24T18:29:58Z"
    return {
        "schema_version": "1.0",
        "research_id": "res_01HZYEXAMPLE0001",
        "symbol": "AAPL",
        "as_of": as_of,
        "instrument": {
            "instrument_id": "rh_inst_aapl_xnas",
            "symbol": "AAPL",
            "exchange": "XNAS",
            "currency": "USD",
            "asset_type": "EQUITY",
            "identity_as_of": as_of,
            "provider": "robinhood.mcp",
        },
        "market": {
            "last_price": "215.42",
            "bid": "215.40",
            "ask": "215.44",
            "currency": "USD",
            "observed_at": observed,
            "provenance": {
                "source": "robinhood.mcp.quotes",
                "observed_at": observed,
                "received_at": as_of,
                "timezone": "UTC",
                "is_delayed": False,
                "quality_flags": [],
            },
        },
        "technical": {
            "schema_version": "1.0",
            "symbol": "AAPL",
            "sma_20": "211.30",
            "sma_50": "204.80",
            "rsi_14": "61.20",
            "atr_14": "4.70",
            "provenance": {
                "source": "ainvest.indicators.v1",
                "observed_at": observed,
                "received_at": as_of,
                "timezone": "UTC",
                "is_delayed": False,
                "quality_flags": [],
            },
        },
        "portfolio": {
            "quantity": "10",
            "market_value": "2154.20",
            "portfolio_weight": "0.0800",
            "buying_power": "3000.00",
        },
        "thesis": {"bull_case": [], "bear_case": [], "risks": []},
        "evidence": [],
        "quality_flags": [],
    }


def parse_research_packet(data: dict[str, Any]) -> ResearchPacket:
    """Validate and construct a ResearchPacket from a mapping."""
    return ResearchPacket.model_validate(data)


def assert_time_ordering(
    *,
    observed_at: datetime,
    received_at: datetime,
    as_of: datetime | None = None,
) -> None:
    """Shared fail-closed time-order checks for research ingest paths."""
    if received_at < observed_at:
        raise ValueError("received_at must be >= observed_at")
    if as_of is not None and (observed_at > as_of or received_at > as_of):
        raise ValueError("observation times must be <= as_of")


__all__ = [
    "EvidenceCitation",
    "EvidenceKind",
    "ResearchPacket",
    "ThesisSection",
    "assert_time_ordering",
    "parse_research_packet",
    "research_packet_example",
]
