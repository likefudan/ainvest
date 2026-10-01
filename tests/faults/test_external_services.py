"""External-service failures stay bounded, sanitized, and funds-neutral."""

from __future__ import annotations

import asyncio

import pytest
from execution.robinhood.gateway_fakes import FakeGateway, FakeGatewayError, RecordingSink

from ainvest.execution.robinhood.errors import (
    RH_MCP_ERROR_CODE_MAP,
    GatewayReadError,
    GatewayReadErrorCode,
)
from ainvest.execution.robinhood.read_client import RobinhoodReadClient

from .conftest import FaultEvidence, assert_fault_evidence


class _FaultingMarketService:
    """Synthetic market boundary with an explicit call/effect counter."""

    def __init__(self) -> None:
        self.calls = 0

    def fetch_quote(self) -> object:
        self.calls += 1
        raise TimeoutError("synthetic market timeout")


class _FaultingNewsService:
    """Synthetic news boundary; it carries no article text or network client."""

    def __init__(self) -> None:
        self.calls = 0

    def fetch_news(self) -> object:
        self.calls += 1
        raise ConnectionResetError("synthetic news connection reset")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("service", "operation", "result_code"),
    [
        (_FaultingMarketService(), "fetch_quote", "MARKET_TIMEOUT"),
        (_FaultingNewsService(), "fetch_news", "NEWS_CONNECTION_RESET"),
    ],
)
def test_market_and_news_failures_do_not_fallback_to_a_funds_path(
    service: _FaultingMarketService | _FaultingNewsService,
    operation: str,
    result_code: str,
) -> None:
    with pytest.raises((TimeoutError, ConnectionResetError)):
        getattr(service, operation)()
    evidence = FaultEvidence(
        final_state="NO_DECISION",
        audit_or_result=(result_code,),
        external_calls=service.calls,
        funds_effect="unchanged",
    )

    assert_fault_evidence(evidence, state="NO_DECISION", calls=1, funds="unchanged")


@pytest.mark.integration
@pytest.mark.parametrize("gateway_code", ["timeout", "provider_error"])
def test_named_rh_mcp_read_failure_is_single_call_and_funds_neutral(
    gateway_code: str,
) -> None:
    gateway = FakeGateway(raises=FakeGatewayError(gateway_code, retryable=True))
    sink = RecordingSink()
    client = RobinhoodReadClient(gateway, log_sink=sink)
    asyncio.run(client.verify_startup())

    with pytest.raises(GatewayReadError) as exc_info:
        asyncio.run(client.read_equity_quotes({"symbols": ["ZZZZ"]}))

    evidence = FaultEvidence(
        final_state="READ_FAILED",
        audit_or_result=(str(sink.only[1]["error_code"]),),
        external_calls=len(gateway.invocations),
        funds_effect="unchanged",
    )
    expected = RH_MCP_ERROR_CODE_MAP[gateway_code]
    assert exc_info.value.code is expected
    assert "IGNORE PREVIOUS" not in f"{exc_info.value!r}{sink.records!r}"
    assert_fault_evidence(evidence, state="READ_FAILED", calls=1, funds="unchanged")


@pytest.mark.integration
def test_manifest_drift_stops_before_any_external_read_or_funds_effect() -> None:
    gateway = FakeGateway()
    gateway.readiness_result["manifest_digest"] = "sha256:" + ("f" * 64)
    client = RobinhoodReadClient(gateway)

    with pytest.raises(GatewayReadError) as exc_info:
        asyncio.run(client.verify_startup())

    evidence = FaultEvidence(
        final_state="NOT_READY",
        audit_or_result=(str(exc_info.value.code),),
        external_calls=len(gateway.invocations),
        funds_effect="unchanged",
    )
    assert exc_info.value.code is GatewayReadErrorCode.NOT_READY
    assert_fault_evidence(evidence, state="NOT_READY", calls=0, funds="unchanged")
