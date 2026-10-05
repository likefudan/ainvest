"""Synthetic, bounded research assembly; no provider network or real accounts."""

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from indicator_fixtures import snapshot
from research_tool_fixtures import fixtures

from ainvest.agents.research_agent import AgentContext, ResearchAgent
from ainvest.agents.research_archive import ResearchBuildRequest
from ainvest.agents.research_models import ModelReply, ResearchAgentResult, ResearchLimits
from ainvest.agents.tools import HistoryInput, ResearchTools


@contextmanager
def assembly_inputs(
    run_id: str = "research_tools_001",
    *,
    include_context: bool = True,
    age_seconds: int = 0,
    hypothesis: bool = False,
) -> Iterator[tuple[ResearchBuildRequest, ResearchAgentResult, ResearchTools]]:
    scope, sources = fixtures()
    scope = scope.model_copy(
        update={"run_id": run_id, "as_of": scope.as_of + timedelta(seconds=age_seconds)}
    )
    history = HistoryInput(expected=snapshot().inputs.expected) if include_context else None
    limits = ResearchLimits(
        required_tools=("quote", "history", "indicators", "concentration")
        if include_context
        else ("quote",)
    )

    async def model(context: AgentContext) -> ModelReply:
        quote = await context.tools.quote()
        if include_context:
            await context.tools.history()
            await context.tools.indicators()
            await context.tools.concentration()
        citation = quote.evidence[0]
        return ModelReply(
            output_json=json.dumps(
                {
                    "schema_version": "1.0",
                    "bull_case": [
                        {
                            "text": "Liquidity may warrant closer review."
                            if hypothesis
                            else citation.summary,
                            "evidence_ids": [citation.evidence_id],
                            "kind": "hypothesis" if hypothesis else "observation",
                        }
                    ],
                    "bear_case": [],
                    "risks": [],
                    "open_questions": [],
                }
            ),
            request_ids=("req_builder_offline",),
            response_ids=("resp_builder_offline",),
            input_tokens=100,
            output_tokens=30,
            requests=1,
        )

    request = ResearchBuildRequest(
        scope=scope,
        limits=limits,
        history=history,
        started_at=scope.as_of + timedelta(seconds=1),
        completed_at=scope.as_of + timedelta(seconds=2),
        code_version="offline-test-v1",
        config_version="synthetic-v1",
    )
    with ResearchTools(scope, sources) as tools:
        result = asyncio.run(ResearchAgent(model, limits=limits).run(tools, history=history))
        yield request, result, tools
