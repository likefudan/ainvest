"""Versioned research inputs and deterministic offline reconstruction."""

from typing import Annotated, Literal

from pydantic import Field

from ainvest.data.indicators import (
    DEFAULT_PARAMETERS,
    Digest,
    IndicatorParameters,
    IndicatorRun,
    compute_indicators,
    digest_bytes,
)
from ainvest.data.models import OhlcvPage
from ainvest.data.ports import DataConflictError, DataIncompleteError, DataOperation
from ainvest.data.providers.news import NewsPage
from ainvest.data.providers.sec import SecFundamentalPage
from ainvest.data.quality import BarExpectation
from ainvest.schemas.common import DomainModel, QualityFlag, SchemaVersion, SourceId, StableId
from ainvest.schemas.market import MarketQuote, ResearchMarketSection
from ainvest.schemas.research import EvidenceCitation, EvidenceKind, ResearchPacket, ThesisSection


class ResponseDigest(DomainModel):
    schema_version: SchemaVersion = "1.0"
    source: SourceId
    role: Literal["quote", "bars", "news", "fundamentals"]
    raw_response_digest: Digest


class SnapshotInputs(DomainModel):
    schema_version: SchemaVersion = "1.0"
    normalization_version: Literal["ainvest-normalized-v1"] = "ainvest-normalized-v1"
    expected: BarExpectation
    quote: MarketQuote
    bars: OhlcvPage
    raw_digests: Annotated[tuple[ResponseDigest, ...], Field(min_length=2, max_length=202)]
    news: Annotated[tuple[NewsPage, ...], Field(max_length=100)] = ()
    fundamentals: Annotated[tuple[SecFundamentalPage, ...], Field(max_length=100)] = ()

    @property
    def cache_key(self) -> str:
        """Includes source, full instrument, timeframe, adjustment and as_of."""
        return digest_bytes(self.expected.model_dump_json().encode())


class ResearchSnapshot(DomainModel):
    schema_version: SchemaVersion = "1.0"
    inputs: SnapshotInputs
    calculation: IndicatorRun
    packet: ResearchPacket


def build_snapshot(
    inputs: SnapshotInputs,
    *,
    research_id: StableId,
    thesis: ThesisSection | None = None,
    parameters: IndicatorParameters = DEFAULT_PARAMETERS,
) -> ResearchSnapshot:
    """Reject invalid market inputs; preserve partial evidence without promotion."""
    expected, quote = inputs.expected, inputs.quote
    needed = {("quote", quote.provenance.source), ("bars", inputs.bars.provenance.source)}
    needed.update(("news", page.provenance.source) for page in inputs.news)
    needed.update(("fundamentals", page.provenance.source) for page in inputs.fundamentals)
    available = {(item.role, item.source) for item in inputs.raw_digests}
    if not needed.issubset(available) or quote.instrument != expected.instrument:
        raise DataIncompleteError(
            "Snapshot source binding invalid",
            operation=DataOperation.DATASET,
            reason_code="SNAPSHOT_BINDING_INVALID",
        )
    if (
        quote.provenance.received_at > expected.as_of
        or quote.provenance.observed_at > expected.as_of
        or (expected.as_of - quote.provenance.observed_at).total_seconds()
        > expected.max_age_seconds
        or quote.provenance.is_delayed
        or quote.provenance.quality_flags
    ):
        raise DataIncompleteError(
            "Snapshot quote quality invalid",
            operation=DataOperation.DATASET,
            reason_code="SNAPSHOT_QUOTE_INVALID",
        )
    run = compute_indicators(inputs.bars, expected, parameters)
    quote_digest = digest_bytes(quote.model_dump_json().encode()).split(":")[1]
    citations = [
        EvidenceCitation(
            evidence_id="quote_" + quote_digest,
            kind=EvidenceKind.QUOTE,
            summary="Captured structured market quote",
            provenance=quote.provenance,
            locator="quote:research/" + quote_digest,
        ),
        EvidenceCitation(
            evidence_id="technical_" + run.input_digest.split(":")[1],
            kind=EvidenceKind.TECHNICAL,
            summary="Reproducible TA-Lib technical indicators",
            provenance=run.technical.provenance,
            locator="technical:talib/" + run.input_digest.split(":")[1],
        ),
    ]
    flags = set(run.technical.provenance.quality_flags)
    evidence_pages: tuple[NewsPage | SecFundamentalPage, ...] = (*inputs.news, *inputs.fundamentals)
    for page in evidence_pages:
        if (
            page.provenance.received_at > expected.as_of
            or page.provenance.observed_at > expected.as_of
        ):
            raise DataIncompleteError(
                "Future source capture",
                operation=DataOperation.DATASET,
                reason_code="SNAPSHOT_FUTURE_EVIDENCE",
            )
        flags.update(page.provenance.quality_flags)
        if page.next_cursor is not None:
            flags.add(QualityFlag.PARTIAL)
        for item in page.items:
            citations.extend(item.citations)
    for page in inputs.news:
        if any(
            item.symbols and expected.instrument.symbol not in item.symbols for item in page.items
        ):
            raise DataIncompleteError(
                "Unrelated news",
                operation=DataOperation.DATASET,
                reason_code="SNAPSHOT_BINDING_INVALID",
            )
    for page in inputs.fundamentals:
        if any(item.instrument != expected.instrument for item in page.items):
            raise DataIncompleteError(
                "Unrelated fundamentals",
                operation=DataOperation.DATASET,
                reason_code="SNAPSHOT_BINDING_INVALID",
            )
    unique: dict[str, EvidenceCitation] = {}
    for citation in citations:
        if (
            citation.provenance.observed_at > expected.as_of
            or citation.provenance.received_at > expected.as_of
        ):
            raise DataIncompleteError(
                "Future evidence citation",
                operation=DataOperation.DATASET,
                reason_code="SNAPSHOT_FUTURE_EVIDENCE",
            )
        if citation.evidence_id in unique and unique[citation.evidence_id] != citation:
            raise DataConflictError(
                "Conflicting evidence",
                operation=DataOperation.DATASET,
                reason_code="SNAPSHOT_EVIDENCE_CONFLICT",
            )
        unique[citation.evidence_id] = citation
        flags.update(citation.provenance.quality_flags)
    packet = ResearchPacket(
        research_id=research_id,
        symbol=expected.instrument.symbol,
        as_of=expected.as_of,
        instrument=expected.instrument,
        market=ResearchMarketSection(
            last_price=quote.last_price,
            bid=quote.bid,
            ask=quote.ask,
            currency=quote.currency,
            observed_at=quote.provenance.observed_at,
            provenance=quote.provenance,
        ),
        technical=run.technical,
        thesis=thesis or ThesisSection(),
        evidence=tuple(unique[key] for key in sorted(unique)),
        quality_flags=tuple(sorted(flags)),
    )
    return ResearchSnapshot(inputs=inputs, calculation=run, packet=packet)


def replay_snapshot(snapshot: ResearchSnapshot) -> ResearchPacket:
    """Recompute locally and reject changed inputs, outputs or library versions."""
    rebuilt = build_snapshot(
        snapshot.inputs,
        research_id=snapshot.packet.research_id,
        thesis=snapshot.packet.thesis,
        parameters=snapshot.calculation.parameters,
    )
    if rebuilt != snapshot:
        raise DataConflictError(
            "Snapshot replay mismatch",
            operation=DataOperation.DATASET,
            reason_code="SNAPSHOT_REPLAY_MISMATCH",
        )
    return rebuilt.packet
