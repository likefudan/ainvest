"""OpenAI Responses wire fixtures; in-memory transport and synthetic data only."""

import json
from typing import Any

import httpx


def responses_transport(
    mode: str = "success",
) -> tuple[httpx.MockTransport, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        count = len(requests)
        if mode == "invalid_json":
            return httpx.Response(200, text="not a JSON response")
        if (mode == "rate_limit" and count == 1) or mode == "rate_limit_always":
            return httpx.Response(
                429,
                json={"error": {"message": "synthetic limit"}},
                headers={"x-request-id": f"req_offline_{count}"},
            )
        if mode == "network" and count == 1:
            raise httpx.ConnectError("synthetic network", request=request)
        if mode == "http_error":
            return httpx.Response(400, json={"error": {"message": "synthetic error"}})
        returns = [item for item in body["input"] if item.get("type") == "function_call_output"]
        if not returns:
            output: list[dict[str, Any]] = [
                {
                    "type": "function_call",
                    "id": "fc_offline_quote",
                    "call_id": "call_offline_quote",
                    "name": "quote",
                    "arguments": "{}",
                    "status": "completed",
                }
            ]
            if mode == "unknown_tool":
                output[0]["name"] = "submit_order"
        else:
            data = json.loads(returns[-1]["output"])
            evidence = data["evidence"][0]
            claim = {
                "schema_version": "1.0",
                "text": evidence["summary"],
                "evidence_ids": [evidence["evidence_id"]],
                "kind": "observation",
            }
            narrative = {
                "schema_version": "1.0",
                "bull_case": [claim],
                "bear_case": [],
                "risks": [],
                "open_questions": [],
            }
            content: list[dict[str, Any]] = [
                {"type": "output_text", "text": json.dumps(narrative), "annotations": []}
            ]
            if mode == "invalid":
                content[0]["text"] = "{}"
            if mode == "refusal":
                content = [{"type": "refusal", "refusal": "synthetic refusal"}]
            output = [
                {
                    "type": "message",
                    "id": "msg_offline_result",
                    "role": "assistant",
                    "status": "completed",
                    "content": content,
                }
            ]
        response: dict[str, Any] = {
            "id": f"resp_offline_{count}",
            "object": "response",
            "created_at": 1780000000,
            "model": "gpt-5.6-sol",
            "output": output,
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "instructions": None,
            "parallel_tool_calls": False,
            "tools": [],
            "tool_choice": "auto",
            "temperature": 1,
            "top_p": 1,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 30,
                "total_tokens": 130,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        }
        if mode == "wrong_model":
            response["model"] = "unapproved-model"
        if mode == "missing_usage":
            response["usage"] = None
        if mode == "incomplete" and returns:
            response["status"] = "incomplete"
            response["incomplete_details"] = {"reason": "max_output_tokens"}
        return httpx.Response(200, json=response, headers={"x-request-id": f"req_offline_{count}"})

    return httpx.MockTransport(handle), requests
