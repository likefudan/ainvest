"""Real locked SDK, mock Responses transport, no real network/key/account."""

import asyncio
import socket

import pytest
from research_agent_fixtures import responses_transport
from research_tool_fixtures import fixtures

from ainvest.agents.research_agent import ResearchAgent, offline_responses_model
from ainvest.agents.tools import ResearchTools


def test_sdk_wire_policy_and_fresh_context(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket, "create_connection", denied)
    transport, requests = responses_transport()
    model = offline_responses_model(transport)
    scope, sources = fixtures()
    with ResearchTools(scope, sources) as tools:
        result = asyncio.run(ResearchAgent(model).run(tools))
    assert result.status == "complete", result.error_code
    assert result.record.request_ids == ("req_offline_1", "req_offline_2")
    assert result.record.requests == 2
    assert result.record.input_tokens == 200
    assert len(requests) == 2
    for request in requests:
        assert request["model"] == "gpt-5.6-sol"
        assert request["reasoning"]["effort"] == "medium"
        assert request["store"] is False
        assert request["parallel_tool_calls"] is False
        assert request["text"]["format"]["type"] == "json_schema"
        assert request["text"]["format"]["strict"] is True
        assert "previous_response_id" not in request and "conversation" not in request
        assert len(request["tools"]) == 8
        assert {tool["type"] for tool in request["tools"]} == {"function"}
        assert {tool["name"] for tool in request["tools"]} == {
            "quote",
            "price_book",
            "history",
            "indicators",
            "filings",
            "news",
            "concentration",
            "buying_power",
        }
        assert all(tool["strict"] is True for tool in request["tools"])
        assert all(tool["parameters"]["additionalProperties"] is False for tool in request["tools"])
        assert "UNTRUSTED DATA" in str(request)
    other_scope = scope.model_copy(update={"run_id": "research_other_002"})
    with ResearchTools(other_scope, sources) as tools:
        assert asyncio.run(ResearchAgent(model).run(tools)).status == "complete"
    assert requests[2]["input"] != requests[0]["input"]
    assert not any(item.get("type") == "function_call_output" for item in requests[2]["input"])


@pytest.mark.parametrize(
    "mode",
    [
        "invalid",
        "refusal",
        "incomplete",
        "wrong_model",
        "unknown_tool",
        "http_error",
        "rate_limit_always",
        "invalid_json",
    ],
)
def test_sdk_bad_responses_withhold_narrative(mode: str) -> None:
    transport, requests = responses_transport(mode)
    scope, sources = fixtures()
    with ResearchTools(scope, sources) as tools:
        result = asyncio.run(ResearchAgent(offline_responses_model(transport)).run(tools))
    assert result.status == "error"
    assert result.narrative is None and not result.evidence
    assert len(requests) <= 2
    if mode == "invalid_json":
        assert len(requests) == 1
    if mode in {"refusal", "invalid", "incomplete"}:
        assert result.record.requests == 2
        assert result.record.input_tokens == 200


@pytest.mark.parametrize("mode", ["rate_limit", "network"])
def test_sdk_only_single_transient_retry(mode: str) -> None:
    transport, requests = responses_transport(mode)
    scope, sources = fixtures()
    with ResearchTools(scope, sources) as tools:
        result = asyncio.run(ResearchAgent(offline_responses_model(transport)).run(tools))
    assert result.status != "error", result.error_code
    assert result.record.attempts == 2
    assert len(requests) == 3
    assert not any(item.get("type") == "function_call_output" for item in requests[1]["input"])


def test_missing_sdk_usage_cannot_be_invented_as_zero_complete() -> None:
    transport, _ = responses_transport("missing_usage")
    scope, sources = fixtures()
    with ResearchTools(scope, sources) as tools:
        result = asyncio.run(ResearchAgent(offline_responses_model(transport)).run(tools))
    assert result.status != "complete"
    assert not result.record.usage_complete or result.status == "error"
