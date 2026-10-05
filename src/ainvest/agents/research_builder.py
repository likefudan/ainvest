"""Deterministic evidence-bound packet assembly, with no provider/ORM/write access."""

import json
import unicodedata
from decimal import ROUND_HALF_EVEN, localcontext

from ainvest.agents.prompts import SYSTEM_PROMPT
from ainvest.agents.research_agent import _PROHIBITED
from ainvest.agents.research_archive import (
    MAX_ARCHIVE_BYTES,
    ResearchArchive,
    ResearchArchivePayload,
    ResearchBuildRequest,
)
from ainvest.agents.research_capture import MAX_RUN_CAPTURE_BYTES
from ainvest.agents.research_models import ResearchAgentResult, ResearchNarrative
from ainvest.agents.tools import ResearchTools
from ainvest.agents.tools.models import (
    TOOL_NAMES,
    HistoryData,
    PortfolioMetrics,
    ToolName,
    ToolResult,
)
from ainvest.agents.tools.runner import _scan
from ainvest.data.indicators import IndicatorRun, compute_indicators, digest_bytes
from ainvest.schemas.common import DomainModel, QualityFlag
from ainvest.schemas.market import MarketQuote, ResearchMarketSection, ResearchPortfolioSection
from ainvest.schemas.research import EvidenceCitation, ResearchPacket, ThesisSection


class ResearchAssemblyError(ValueError):
    """Stable rejection; never include raw model/provider content in the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _input_digest(request: ResearchBuildRequest) -> str:
    body = json.dumps(
        {
            "scope": request.scope.model_dump(mode="json"),
            "history": request.history.model_dump(mode="json") if request.history else None,
            "limits": request.limits.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return digest_bytes(body.encode())


def _schema_digest() -> str:
    body = json.dumps(
        {
            "names": TOOL_NAMES,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "narrative": ResearchNarrative.model_json_schema(),
        },
        sort_keys=True,
    )
    return digest_bytes(body.encode())


def _reconcile(
    request: ResearchBuildRequest,
    result: ResearchAgentResult,
    tools: ResearchTools | None,
) -> tuple[dict[ToolName, ToolResult[DomainModel]], dict[str, EvidenceCitation], set[QualityFlag]]:
    scope = request.scope
    if (
        result.record.run_id != scope.run_id
        or result.record.input_digest != _input_digest(request)
        or result.record.prompt_digest != digest_bytes(SYSTEM_PROMPT.encode())
        or result.record.tool_schema_digest != _schema_digest()
        or (tools is not None and tools.scope != scope)
    ):
        raise ResearchAssemblyError("RESEARCH_BINDING_INVALID")
    if (
        not result.captures
        or tuple(item.digest for item in result.captures) != result.record.tool_output_digests
        or sum(len(item.json_result.encode()) for item in result.captures) > MAX_RUN_CAPTURE_BYTES
    ):
        raise ResearchAssemblyError("CAPTURE_BINDING_INVALID")
    selected: dict[ToolName, ToolResult[DomainModel]] = {}
    evidence: dict[str, EvidenceCitation] = {}
    flags = set(result.quality_flags)
    for capture in result.captures:
        value = capture.decode()
        if value.run_id != scope.run_id or value.as_of != scope.as_of:
            raise ResearchAssemblyError("TOOL_SCOPE_INVALID")
        if value.status == "error" or value.data is None:
            raise ResearchAssemblyError("TOOL_FAILED")
        previous = selected.get(value.tool)
        if previous is not None and previous != value:
            raise ResearchAssemblyError("TOOL_RESULT_CONFLICT")
        selected[value.tool] = value
        inferred, _ = _scan(value.data, scope)
        if not inferred.issubset(value.quality_flags):
            raise ResearchAssemblyError("TOOL_QUALITY_INVALID")
        flags.update(value.quality_flags)
        digest = digest_bytes(
            (scope.run_id + value.tool).encode() + value.data.model_dump_json().encode()
        ).split(":")[1]
        master_id = "tool_" + digest
        masters = [item for item in value.evidence if item.evidence_id == master_id]
        if len(masters) != 1 or masters[0].locator != "tool:ainvest." + value.tool + "/" + digest:
            raise ResearchAssemblyError("TOOL_DATA_BINDING_INVALID")
        if tools is not None:
            registered = tools.resolve_evidence(tuple(item.evidence_id for item in value.evidence))
            if registered != value.evidence:
                raise ResearchAssemblyError("EVIDENCE_REGISTRY_CONFLICT")
        for citation in value.evidence:
            if citation.evidence_id in evidence and evidence[citation.evidence_id] != citation:
                raise ResearchAssemblyError("EVIDENCE_CONFLICT")
            evidence[citation.evidence_id] = citation
            flags.update(citation.provenance.quality_flags)
    if not set(request.limits.required_tools).issubset(selected):
        raise ResearchAssemblyError("REQUIRED_CAPTURE_MISSING")
    if result.narrative is None or not result.narrative.claims():
        raise ResearchAssemblyError("NARRATIVE_MISSING")
    if result.record.output_digest != digest_bytes(result.narrative.model_dump_json().encode()):
        raise ResearchAssemblyError("NARRATIVE_DIGEST_INVALID")
    for citation in result.evidence:
        if evidence.get(citation.evidence_id) != citation:
            raise ResearchAssemblyError("NARRATIVE_EVIDENCE_INVALID")
    returned_ids = {item.evidence_id for item in result.evidence}
    for claim in result.narrative.claims():
        text = unicodedata.normalize("NFKC", claim.text)
        if (
            any(char.isnumeric() for char in text)
            or _PROHIBITED.search(text)
            or any(unicodedata.category(char) == "Cf" for char in text)
        ):
            raise ResearchAssemblyError("PROHIBITED_NARRATIVE")
        if any(key not in returned_ids for key in claim.evidence_ids):
            raise ResearchAssemblyError("CLAIM_EVIDENCE_INVALID")
        if claim.kind == "observation" and claim.text not in {
            evidence[key].summary for key in claim.evidence_ids
        }:
            raise ResearchAssemblyError("UNSUPPORTED_CLAIM")
        if claim.kind == "hypothesis":
            flags.add(QualityFlag.PARTIAL)
    if result.status != "complete" or not result.record.usage_complete:
        flags.add(QualityFlag.PARTIAL)
    return selected, evidence, flags


def _packet(
    request: ResearchBuildRequest,
    result: ResearchAgentResult,
    tools: ResearchTools | None,
) -> ResearchPacket:
    selected, citations, flags = _reconcile(request, result, tools)
    quote_result = selected.get("quote")
    if (
        quote_result is None
        or quote_result.status != "complete"
        or not isinstance(quote_result.data, MarketQuote)
    ):
        raise ResearchAssemblyError("REQUIRED_QUOTE_INVALID")
    quote, scope = quote_result.data, request.scope
    if (
        quote.instrument != scope.instrument
        or quote.provenance.is_delayed
        or quote.provenance.quality_flags
        or quote.provenance.observed_at > scope.as_of
        or quote.provenance.received_at > scope.as_of
        or quote.bid is None
        or quote.ask is None
        or (scope.as_of - quote.provenance.observed_at).total_seconds() > scope.max_age_seconds
    ):
        raise ResearchAssemblyError("REQUIRED_QUOTE_INVALID")
    technical = None
    indicator_result = selected.get("indicators")
    if indicator_result is not None:
        history_result = selected.get("history")
        if (
            history_result is None
            or not isinstance(history_result.data, HistoryData)
            or not isinstance(indicator_result.data, IndicatorRun)
            or request.history is None
            or history_result.data.expected != request.history.expected
        ):
            raise ResearchAssemblyError("TECHNICAL_INPUT_INVALID")
        expected = history_result.data.expected
        if (
            expected.instrument != scope.instrument
            or expected.as_of != scope.as_of
            or expected.max_age_seconds != scope.max_age_seconds
        ):
            raise ResearchAssemblyError("TECHNICAL_SCOPE_INVALID")
        with localcontext() as context:
            context.rounding = ROUND_HALF_EVEN
            recomputed = compute_indicators(
                history_result.data.bars, expected, indicator_result.data.parameters
            )
        if recomputed != indicator_result.data:
            raise ResearchAssemblyError("TECHNICAL_REPLAY_INVALID")
        technical = indicator_result.data.technical
    else:
        flags.update((QualityFlag.PARTIAL, QualityFlag.MISSING_FIELDS))
    portfolio = None
    concentration = selected.get("concentration")
    if concentration is not None:
        if not isinstance(concentration.data, PortfolioMetrics):
            raise ResearchAssemblyError("PORTFOLIO_INPUT_INVALID")
        metrics = concentration.data
        if (
            metrics.instrument != scope.instrument
            or metrics.currency != scope.instrument.currency
            or (scope.as_of - metrics.provenance.observed_at).total_seconds()
            > scope.max_age_seconds
        ):
            raise ResearchAssemblyError("PORTFOLIO_SCOPE_INVALID")
        portfolio = ResearchPortfolioSection(
            quantity=metrics.quantity,
            market_value=metrics.market_value,
            portfolio_weight=metrics.portfolio_weight,
            buying_power=metrics.buying_power,
        )
    else:
        flags.update((QualityFlag.PARTIAL, QualityFlag.MISSING_FIELDS))
    narrative = result.narrative
    if narrative is None:
        raise ResearchAssemblyError("NARRATIVE_MISSING")
    # Open questions remain in the archive; the shared v1 packet has only these sections.
    return ResearchPacket(
        research_id=scope.run_id,
        symbol=scope.instrument.symbol,
        instrument=scope.instrument,
        as_of=scope.as_of,
        market=ResearchMarketSection(
            last_price=quote.last_price,
            bid=quote.bid,
            ask=quote.ask,
            currency=quote.currency,
            observed_at=quote.provenance.observed_at,
            provenance=quote.provenance,
        ),
        technical=technical,
        portfolio=portfolio,
        thesis=ThesisSection(
            bull_case=tuple(item.text for item in narrative.bull_case),
            bear_case=tuple(item.text for item in narrative.bear_case),
            risks=tuple(item.text for item in narrative.risks),
        ),
        evidence=tuple(citations[key] for key in sorted(citations)),
        quality_flags=tuple(sorted(flags)),
    )


def build_research_archive(
    request: ResearchBuildRequest,
    result: ResearchAgentResult,
    *,
    tools: ResearchTools,
) -> ResearchArchive:
    """Reconcile the live run registry and build or withhold a versioned packet."""
    try:
        request = ResearchBuildRequest.model_validate_json(request.model_dump_json())
        if len(result.model_dump_json().encode()) > MAX_ARCHIVE_BYTES:
            raise ValueError("oversized run")
        result = ResearchAgentResult.model_validate_json(result.model_dump_json())
    except ValueError:
        raise ResearchAssemblyError("BUILD_INPUT_INVALID") from None
    packet = None
    code = result.error_code if result.status == "error" else None
    if code is None:
        try:
            packet = _packet(request, result, tools)
        except ResearchAssemblyError as exc:
            code = exc.code
        except Exception:
            code = "ASSEMBLY_FAILED"
    payload = ResearchArchivePayload(
        request=request,
        agent=result,
        packet=packet,
        error_code=code,
        status="error" if code else "partial" if packet and packet.quality_flags else "complete",
    )
    return ResearchArchive.create(payload)


def replay_research_archive(archive: ResearchArchive) -> ResearchPacket | None:
    """Rebuild from retained tool captures without provider/model/database access.

    Hashes detect accidental/local tampering, not replacement of an entire trusted
    archive and its digest by a privileged attacker. Operational storage needs
    its separate access controls and append-only audit checkpoint.
    """
    try:
        if len(archive.model_dump_json().encode()) > MAX_ARCHIVE_BYTES:
            raise ValueError("oversized archive")
        archive = ResearchArchive.model_validate_json(archive.model_dump_json())
        if archive.payload.status == "error":
            return None
        rebuilt = _packet(archive.payload.request, archive.payload.agent, None)
        if rebuilt != archive.payload.packet:
            raise ValueError("packet differs from retained tool data")
        return rebuilt
    except Exception:
        raise ResearchAssemblyError("RESEARCH_REPLAY_INVALID") from None
