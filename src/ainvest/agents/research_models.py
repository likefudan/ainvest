"""Bounded intermediate narrative contracts, never an order or final packet."""

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from ainvest.agents.tools.models import ToolName
from ainvest.data.indicators import Digest
from ainvest.schemas.common import DomainModel, MachineCode, QualityFlag, StableId
from ainvest.schemas.research import EvidenceCitation

Text = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
Count = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
ProviderId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,160}$")]


class NarrativeClaim(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    text: Text
    evidence_ids: Annotated[tuple[StableId, ...], Field(min_length=1, max_length=8)]
    kind: Literal["observation", "hypothesis"]

    @model_validator(mode="after")
    def _references(self) -> Self:
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("claim references must be unique")
        return self


class ResearchNarrative(DomainModel):
    schema_version: Literal["1.0"]
    bull_case: Annotated[tuple[NarrativeClaim, ...], Field(max_length=8)]
    bear_case: Annotated[tuple[NarrativeClaim, ...], Field(max_length=8)]
    risks: Annotated[tuple[NarrativeClaim, ...], Field(max_length=8)]
    open_questions: Annotated[tuple[NarrativeClaim, ...], Field(max_length=8)]

    def claims(self) -> tuple[NarrativeClaim, ...]:
        return self.bull_case + self.bear_case + self.risks + self.open_questions


class ResearchLimits(DomainModel):
    """Engineering ceilings only, not an owner-approved monetary budget."""

    schema_version: Literal["1.0"] = "1.0"
    duration_seconds: Annotated[int, Field(strict=True, ge=1, le=120)] = 60
    requests: Annotated[int, Field(strict=True, ge=1, le=8)] = 4
    tool_calls: Annotated[int, Field(strict=True, ge=1, le=16)] = 8
    input_tokens: Annotated[int, Field(strict=True, ge=1, le=64_000)] = 32_000
    output_tokens: Annotated[int, Field(strict=True, ge=1, le=8192)] = 4096
    required_tools: Annotated[tuple[ToolName, ...], Field(min_length=1, max_length=8)] = ("quote",)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len(set(self.required_tools)) != len(self.required_tools):
            raise ValueError("required tools must be unique")
        return self


class ModelReply(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    model_id: Literal["gpt-5.6-sol"] = "gpt-5.6-sol"
    output_json: Annotated[str, StringConstraints(max_length=65_536)]
    request_ids: Annotated[tuple[ProviderId, ...], Field(min_length=1, max_length=8)]
    response_ids: Annotated[tuple[ProviderId, ...], Field(min_length=1, max_length=8)]
    input_tokens: Count
    output_tokens: Count
    requests: Annotated[int, Field(strict=True, ge=1, le=8)]
    usage_complete: bool = True

    @model_validator(mode="after")
    def _requests(self) -> Self:
        if (
            len(self.request_ids) != self.requests
            or len(self.response_ids) != self.requests
            or len(set(self.request_ids)) != self.requests
            or len(set(self.response_ids)) != self.requests
        ):
            raise ValueError("each successful request requires distinct provider identifiers")
        return self


class ModelTelemetry(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    requests: Annotated[int, Field(strict=True, ge=0, le=8)] = 1
    input_tokens: Count = 0
    output_tokens: Count = 0
    request_ids: Annotated[tuple[ProviderId, ...], Field(max_length=8)] = ()
    response_ids: Annotated[tuple[ProviderId, ...], Field(max_length=8)] = ()
    usage_complete: bool = False


class ResearchRunRecord(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: StableId
    model_id: Literal["gpt-5.6-sol"] = "gpt-5.6-sol"
    prompt_version: Literal["research-narrative-v1"] = "research-narrative-v1"
    tool_schema_version: Literal["research-tools-v1"] = "research-tools-v1"
    prompt_digest: Digest
    tool_schema_digest: Digest
    input_digest: Digest
    output_digest: Digest | None
    tool_output_digests: Annotated[tuple[Digest, ...], Field(max_length=16)]
    attempts: Annotated[int, Field(strict=True, ge=0, le=2)]
    request_ids: Annotated[tuple[ProviderId, ...], Field(max_length=16)]
    response_ids: Annotated[tuple[ProviderId, ...], Field(max_length=16)]
    input_tokens: Count
    output_tokens: Count
    requests: Count
    usage_complete: bool


class ResearchAgentResult(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["complete", "partial", "error"]
    narrative: ResearchNarrative | None
    evidence: Annotated[tuple[EvidenceCitation, ...], Field(max_length=128)]
    quality_flags: tuple[QualityFlag, ...]
    error_code: MachineCode | None
    record: ResearchRunRecord

    @model_validator(mode="after")
    def _result(self) -> Self:
        if self.status == "error":
            if self.narrative is not None or self.evidence or self.error_code is None:
                raise ValueError("error must withhold narrative and evidence")
        elif self.narrative is None or not self.evidence or self.error_code is not None:
            raise ValueError("success needs narrative and evidence")
        if self.status == "complete" and self.quality_flags:
            raise ValueError("flagged output cannot be complete")
        if self.status != "complete" and not self.quality_flags:
            raise ValueError("incomplete output requires flags")
        if self.narrative is not None:
            ids = {item.evidence_id for item in self.evidence}
            if len(ids) != len(self.evidence) or any(
                key not in ids for claim in self.narrative.claims() for key in claim.evidence_ids
            ):
                raise ValueError("narrative requires distinct returned evidence")
        if self.status == "complete" and (
            self.narrative is None
            or not self.record.usage_complete
            or self.record.attempts == 0
            or not self.record.tool_output_digests
            or any(claim.kind == "hypothesis" for claim in self.narrative.claims())
        ):
            raise ValueError("complete output requires verified observations and usage")
        return self
