"""ORM-free research build request and immutable archive contracts."""

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from ainvest.agents.research_models import ResearchAgentResult, ResearchLimits
from ainvest.agents.tools import HistoryInput, ToolScope
from ainvest.data.indicators import Digest, digest_bytes
from ainvest.schemas.common import DomainModel, MachineCode, UtcDateTime
from ainvest.schemas.research import ResearchPacket

MAX_ARCHIVE_BYTES = 2_097_152
BuildVersion = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
]


class ResearchBuildRequest(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    scope: ToolScope
    limits: ResearchLimits
    history: HistoryInput | None
    started_at: UtcDateTime
    completed_at: UtcDateTime
    code_version: BuildVersion
    config_version: BuildVersion

    @model_validator(mode="after")
    def _times(self) -> Self:
        if self.scope.as_of > self.started_at or self.completed_at < self.started_at:
            raise ValueError("build cutoff and timestamps are inconsistent")
        return self


class ResearchArchivePayload(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    builder_version: Literal["research-builder-v1"] = "research-builder-v1"
    request: ResearchBuildRequest
    agent: ResearchAgentResult
    status: Literal["complete", "partial", "error"]
    packet: ResearchPacket | None
    error_code: MachineCode | None

    @model_validator(mode="after")
    def _status(self) -> Self:
        if self.status == "error":
            if self.packet is not None or self.error_code is None:
                raise ValueError("failed assembly must withhold packet")
        elif self.packet is None or self.error_code is not None:
            raise ValueError("successful assembly needs packet")
        elif self.status == "complete" and self.packet.quality_flags:
            raise ValueError("flagged packet cannot be complete")
        elif self.status == "partial" and not self.packet.quality_flags:
            raise ValueError("partial packet requires quality flags")
        return self


class ResearchArchive(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    payload: ResearchArchivePayload
    digest: Digest

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        body = self.payload.model_dump_json().encode()
        if len(body) > MAX_ARCHIVE_BYTES or digest_bytes(body) != self.digest:
            raise ValueError("research archive size or digest invalid")
        return self

    @classmethod
    def create(cls, payload: ResearchArchivePayload) -> Self:
        return cls(payload=payload, digest=digest_bytes(payload.model_dump_json().encode()))


class ResearchStorageQuota(DomainModel):
    """Caller-selected logical limits; no retention, eviction or default budget."""

    schema_version: Literal["1.0"] = "1.0"
    max_records: Annotated[int, Field(strict=True, ge=1, le=10_000)]
    max_record_bytes: Annotated[int, Field(strict=True, ge=1, le=MAX_ARCHIVE_BYTES)]
