"""Offline narrative safety; no credentials or provider network."""

import asyncio
import json
from dataclasses import replace
from threading import Event
from time import monotonic

import pytest
from research_tool_fixtures import fixtures

from ainvest.agents.research_agent import (
    AgentContext,
    ModelPort,
    ResearchAgent,
    TransientModelError,
)
from ainvest.agents.research_models import ModelReply, ResearchAgentResult, ResearchLimits
from ainvest.agents.tools import ResearchTools
from ainvest.agents.tools.models import ToolScope
from ainvest.schemas.market import MarketQuote


def reply(evidence: str, text: str = "Observed normalized quote.") -> ModelReply:
    claim = {"text": text, "evidence_ids": [evidence], "kind": "observation"}
    return ModelReply(
        output_json=json.dumps(
            {
                "schema_version": "1.0",
                "bull_case": [claim],
                "bear_case": [],
                "risks": [],
                "open_questions": [],
            }
        ),
        request_ids=("req_offline_001",),
        response_ids=("resp_offline_001",),
        input_tokens=100,
        output_tokens=30,
        requests=1,
    )


def run(model: ModelPort | None, limits: ResearchLimits | None = None) -> ResearchAgentResult:
    scope, sources = fixtures()
    with ResearchTools(scope, sources) as tools:
        return asyncio.run(ResearchAgent(model, limits=limits).run(tools))


def test_valid_extract_requires_same_run_returned_evidence() -> None:
    async def model(context: AgentContext) -> ModelReply:
        quote = await context.tools.quote()
        return reply(quote.evidence[0].evidence_id, quote.evidence[0].summary)

    result = run(model)
    assert result.status == "complete"
    assert result.narrative is not None
    assert result.record.model_id == "gpt-5.6-sol"
    assert result.record.request_ids == ("req_offline_001",)
    assert result.record.tool_output_digests
    assert not hasattr(result, "packet")


@pytest.mark.parametrize(
    "text",
    [
        "BUY shares now",
        "sell the position",
        "买入股票",
        "卖出股票",
        "Guaranteed returns",
        "收益保证",
        "Price will be 200",
        "two shares",
    ],
)
def test_trade_promises_and_numeric_prose_rejected(text: str) -> None:
    async def model(context: AgentContext) -> ModelReply:
        quote = await context.tools.quote()
        return reply(quote.evidence[0].evidence_id, text)

    result = run(model)
    assert result.status == "error" and result.narrative is None


def test_invented_and_unsupported_claim_rejected() -> None:
    async def invented(context: AgentContext) -> ModelReply:
        await context.tools.quote()
        return reply("evidence_invented")

    assert run(invented).error_code == "UNSUPPORTED_EVIDENCE"

    async def unsupported(context: AgentContext) -> ModelReply:
        quote = await context.tools.quote()
        return reply(quote.evidence[0].evidence_id, "Issuer earnings improved.")

    assert run(unsupported).error_code == "UNSUPPORTED_CLAIM"


def test_only_explicit_transient_error_retried_once() -> None:
    calls = 0

    async def model(context: AgentContext) -> ModelReply:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientModelError("RATE_LIMIT")
        quote = await context.tools.quote()
        return reply(quote.evidence[0].evidence_id, quote.evidence[0].summary)

    assert run(model).record.attempts == 2
    assert calls == 2

    async def exhausted(context: AgentContext) -> ModelReply:
        raise TransientModelError("NETWORK")

    assert run(exhausted).error_code == "TRANSIENT_RETRY_EXHAUSTED"


def test_invalid_model_output_and_nontransient_error_no_retry() -> None:
    calls = 0

    async def invalid(context: AgentContext) -> ModelReply:
        nonlocal calls
        calls += 1
        return reply("evidence_unknown").model_copy(update={"output_json": "{}"})

    assert run(invalid).status == "error"
    assert calls == 1

    async def failed(context: AgentContext) -> ModelReply:
        raise RuntimeError("private upstream error must not escape")

    result = run(failed)
    assert result.error_code == "MODEL_FAILED"
    assert "private upstream" not in result.model_dump_json()


def test_timeout_and_usage_limit_fail_closed() -> None:
    async def slow(context: AgentContext) -> ModelReply:
        await asyncio.sleep(2)
        raise AssertionError("not reached")

    assert run(slow, ResearchLimits(duration_seconds=1)).error_code == "TIMEOUT"

    async def large(context: AgentContext) -> ModelReply:
        quote = await context.tools.quote()
        return reply(quote.evidence[0].evidence_id).model_copy(update={"input_tokens": 100_000})

    assert run(large).error_code == "USAGE_LIMIT"


def test_failed_required_read_prevents_narrative() -> None:
    scope, sources = fixtures()

    async def model(context: AgentContext) -> ModelReply:
        quote = await context.tools.quote()
        assert quote.status == "error"
        return reply("evidence_unknown")

    with ResearchTools(scope, replace(sources, quote=None)) as tools:
        result = asyncio.run(ResearchAgent(model).run(tools))
    assert result.status == "error"


def test_default_real_adapter_disabled() -> None:
    assert run(None).error_code == "AI_DISABLED"


def test_hypotheses_are_never_verified_complete() -> None:
    async def model(context: AgentContext) -> ModelReply:
        quote = await context.tools.quote()
        output = reply(quote.evidence[0].evidence_id, "Liquidity may warrant closer review.")
        body = json.loads(output.output_json)
        body["bull_case"][0]["kind"] = "hypothesis"
        return output.model_copy(update={"output_json": json.dumps(body)})

    result = run(model)
    assert result.status == "partial" and result.narrative is not None


def test_tool_limit_cannot_be_swallowed_to_complete() -> None:
    async def model(context: AgentContext) -> ModelReply:
        quote = await context.tools.quote()
        with pytest.raises(ValueError):
            await context.tools.quote()
        return reply(quote.evidence[0].evidence_id, quote.evidence[0].summary)

    assert run(model, ResearchLimits(tool_calls=1)).error_code == "REQUIRED_TOOL_FAILED"


def test_history_is_bound_by_caller_not_model() -> None:
    async def model(context: AgentContext) -> ModelReply:
        await context.tools.history()
        raise AssertionError("history without bound input cannot succeed")

    assert run(model).error_code == "HISTORY_INPUT_MISSING"


def test_run_ids_are_single_use_and_global_concurrency_is_bounded() -> None:
    async def model(context: AgentContext) -> ModelReply:
        quote = await context.tools.quote()
        return reply(quote.evidence[0].evidence_id, quote.evidence[0].summary)

    scope, sources = fixtures()
    agent = ResearchAgent(model)
    with ResearchTools(scope, sources) as tools:
        assert asyncio.run(agent.run(tools)).status == "complete"
        assert asyncio.run(agent.run(tools)).error_code == "RUN_REUSED"

    async def concurrent() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def held(context: AgentContext) -> ModelReply:
            entered.set()
            await release.wait()
            return await model(context)

        with ResearchTools(scope, sources) as left, ResearchTools(scope, sources) as right:
            task = asyncio.create_task(ResearchAgent(held).run(left))
            await entered.wait()
            assert (await ResearchAgent(model).run(right)).error_code == "RUN_BUSY"
            release.set()
            assert (await task).status == "complete"

    asyncio.run(concurrent())


def test_unknown_retry_usage_stays_partial_and_total_requests_are_bounded() -> None:
    calls = 0

    async def model(context: AgentContext) -> ModelReply:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientModelError("NETWORK")
        quote = await context.tools.quote()
        return reply(quote.evidence[0].evidence_id, quote.evidence[0].summary)

    result = run(model)
    assert result.status == "partial" and not result.record.usage_complete
    assert result.record.requests == 2
    calls = 0
    assert run(model, ResearchLimits(requests=1)).error_code == "USAGE_LIMIT"
    assert calls == 1


def test_wrong_model_and_invented_extra_fields_rejected() -> None:
    async def model(context: AgentContext) -> ModelReply:
        return reply("evidence_unknown").model_copy(update={"model_id": "other-model"})

    assert run(model).status == "error"


def test_slow_tool_timeout_does_not_join_default_executor() -> None:
    scope, sources = fixtures()
    release = Event()
    reader = sources.quote
    assert reader is not None

    def blocked(request: ToolScope) -> MarketQuote:
        release.wait(5)
        return reader(request)

    async def model(context: AgentContext) -> ModelReply:
        await context.tools.quote()
        raise AssertionError("timed-out tool must not return to model")

    with ResearchTools(scope, replace(sources, quote=blocked)) as tools:
        try:
            started = monotonic()
            result = asyncio.run(
                ResearchAgent(model, limits=ResearchLimits(duration_seconds=1)).run(tools)
            )
            assert result.error_code == "TIMEOUT"
            assert monotonic() - started < 3
            assert tools.quote().error_code in {"RUN_CLOSED", "RUN_BUSY"}
        finally:
            release.set()
