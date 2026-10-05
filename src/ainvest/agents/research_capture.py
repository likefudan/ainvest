"""Bounded immutable records of actual named tool returns."""

from typing import Annotated, Literal, Self, cast

from pydantic import Field, model_validator

from ainvest.agents.tools.models import (
    BuyingPowerData,
    FilingPage,
    HistoryData,
    PortfolioMetrics,
    ToolName,
    ToolResult,
)
from ainvest.data.indicators import Digest, IndicatorRun, digest_bytes
from ainvest.data.models import PriceBook
from ainvest.data.providers.news import NewsPage
from ainvest.schemas.common import DomainModel
from ainvest.schemas.market import MarketQuote

MAX_CAPTURE_BYTES = 262_144
MAX_RUN_CAPTURE_BYTES = 1_048_576
TOOL_DATA: dict[ToolName, type[DomainModel]] = {
    "quote": MarketQuote,
    "price_book": PriceBook,
    "history": HistoryData,
    "indicators": IndicatorRun,
    "filings": FilingPage,
    "news": NewsPage,
    "concentration": PortfolioMetrics,
    "buying_power": BuyingPowerData,
}


def _model(tool: ToolName) -> type[ToolResult[DomainModel]]:
    return cast(type[ToolResult[DomainModel]], ToolResult.__class_getitem__(TOOL_DATA[tool]))


class CapturedToolResult(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    tool: ToolName
    json_result: Annotated[str, Field(min_length=1, max_length=MAX_CAPTURE_BYTES)]
    digest: Digest

    def decode(self) -> ToolResult[DomainModel]:
        """Revalidate the exact fixed data model for the declared tool."""
        return _model(self.tool).model_validate_json(self.json_result)

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.schema_version != "1.0" or len(self.json_result.encode()) > MAX_CAPTURE_BYTES:
            raise ValueError("invalid capture version or size")
        result = self.decode()
        if result.tool != self.tool or result.model_dump_json() != self.json_result:
            raise ValueError("capture must be a canonical fixed-model tool result")
        if digest_bytes(self.json_result.encode()) != self.digest:
            raise ValueError("tool capture digest mismatch")
        return self

    @classmethod
    def capture_json(cls, tool: ToolName, value: str) -> Self:
        """Canonicalize a bounded typed result; no provider/capability invocation."""
        if len(value.encode()) > MAX_CAPTURE_BYTES:
            raise ValueError("tool capture too large")
        result = _model(tool).model_validate_json(value)
        body = result.model_dump_json()
        return cls(tool=tool, json_result=body, digest=digest_bytes(body.encode()))
