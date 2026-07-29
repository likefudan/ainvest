"""Integration tests for the deterministic paper orchestration loop (P03-T16)."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import StringIO

import pytest

from ainvest.execution import BrokerSubmitOutcome, BrokerSubmitRequest, BrokerSubmitResult
from ainvest.execution.state_machine import OrderLifecycleState
from ainvest.observability import (
    bind_log_context,
    clear_log_context,
    configure_logging,
    get_logger,
)
from ainvest.orchestrator import PaperFlowTerminal, run_paper_flow
from ainvest.orchestrator.fixtures import make_paper_flow_config, make_risk_config
from ainvest.risk import AllowlistEntry
from ainvest.schemas.common import AssetType


class _UnknownWritePort:
    def __init__(self) -> None:
        self.submit_calls = 0

    def submit(self, request: BrokerSubmitRequest) -> BrokerSubmitResult:
        self.submit_calls += 1
        return BrokerSubmitResult(
            outcome=BrokerSubmitOutcome.UNKNOWN,
            client_order_id=request.client_order_id,
            observed_at=request.proposal.created_at,
            reason_code="SIMULATED_UNKNOWN",
        )

    def cancel(self, command: object) -> object:
        raise NotImplementedError


@pytest.mark.integration
def test_paper_flow_success_replayable() -> None:
    first = run_paper_flow(make_paper_flow_config(inject_approval=True))
    second = run_paper_flow(make_paper_flow_config(inject_approval=True))

    assert first.terminal is PaperFlowTerminal.FILLED
    assert first.lifecycle is OrderLifecycleState.FILLED
    assert first.conservation_ok is True
    assert first.filled_quantity > 0
    assert first.order_hash is not None
    assert first.digests["order_hash"] == first.order_hash
    assert "risk_input" in first.digests
    assert [step.name for step in first.steps] == [step.name for step in second.steps]
    assert first.digests == second.digests
    assert first.order_hash == second.order_hash
    assert first.filled_quantity == second.filled_quantity
    assert {step.name for step in first.steps} >= {
        "evaluate_strategy",
        "aggregate_signals",
        "size_position",
        "evaluate_risk",
        "create_proposal",
        "create_challenge",
        "consume_challenge",
        "evaluate_pretrade",
        "execute_order",
        "inject_market_event",
        "reconcile",
    }


@pytest.mark.integration
def test_paper_flow_logs_connect_every_step_without_money_payloads() -> None:
    stream = StringIO()
    clear_log_context()
    configure_logging(
        service="paper-orchestrator",
        environment="test",
        version="test-version",
        stream=stream,
    )

    result = run_paper_flow(make_paper_flow_config(inject_approval=True))
    events = [json.loads(line) for line in stream.getvalue().splitlines()]

    assert [event["event"] for event in events] == [step.name for step in result.steps]
    assert {event["correlation_id"] for event in events} == {result.correlation_id}
    assert {event["strategy_run_id"] for event in events} == {"srun_01HZYD4APAPER0001"}
    assert all(event["service"] == "paper-orchestrator" for event in events)
    assert all(event["environment"] == "test" for event in events)
    assert all("causation_id" in event for event in events)
    assert all("proposal_id" in event for event in events)
    assert all("quantity" not in event for event in events)
    assert all("limit_price" not in event for event in events)
    assert any(event["proposal_id"] == result.proposal_id for event in events)


@pytest.mark.integration
def test_filled_flow_preserves_money_lifecycle_when_sampling_is_zero() -> None:
    stream = StringIO()
    clear_log_context()
    configure_logging(
        service="paper-orchestrator",
        environment="test",
        version="test-version",
        sample_rate=0,
        stream=stream,
    )
    get_logger().debug("ordinary_debug_event")

    result = run_paper_flow(make_paper_flow_config(inject_approval=True))
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    event_names = {event["event"] for event in events}

    assert result.terminal is PaperFlowTerminal.FILLED
    assert "ordinary_debug_event" not in event_names
    assert event_names >= {
        "size_position",
        "evaluate_risk",
        "create_proposal",
        "create_challenge",
        "consume_challenge",
        "evaluate_pretrade",
        "execute_order",
        "inject_market_event",
        "reconcile",
    }
    assert all(event["funds_safety"] is True for event in events)
    assert all(event["level"] == "critical" for event in events)


@pytest.mark.integration
def test_paper_flow_resets_stale_ids_and_restores_caller_context() -> None:
    stream = StringIO()
    clear_log_context()
    configure_logging(stream=stream, environment="test")
    bind_log_context(
        correlation_id="corr_outer_12345678",
        causation_id="cmd_outer_12345678",
        proposal_id="ordp_outer_12345678",
        strategy_run_id="srun_outer_12345678",
        client_order_id="client_outer_12345678",
        broker_order_id="broker_outer_12345678",
    )

    config = make_paper_flow_config(inject_approval=True)
    config.correlation_id = "corr_inner_12345678"
    result = run_paper_flow(config)
    get_logger().info("caller_context_restored")
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    flow_events, caller_event = events[:-1], events[-1]

    assert result.terminal is PaperFlowTerminal.FILLED
    assert {event["correlation_id"] for event in flow_events} == {"corr_inner_12345678"}
    assert flow_events[0]["proposal_id"] is None
    assert flow_events[0]["client_order_id"] is None
    assert flow_events[0]["broker_order_id"] is None
    by_name = {event["event"]: event for event in flow_events}
    assert by_name["create_proposal"]["proposal_id"] == result.proposal_id
    assert by_name["consume_challenge"]["causation_id"] == result.approval_event_id
    assert by_name["evaluate_pretrade"]["client_order_id"] == result.client_order_id
    assert by_name["execute_order"]["broker_order_id"] == result.broker_order_id
    assert by_name["reconcile"]["proposal_id"] == result.proposal_id
    assert all(event["causation_id"] != "cmd_outer_12345678" for event in flow_events)
    assert caller_event["correlation_id"] == "corr_outer_12345678"
    assert caller_event["causation_id"] == "cmd_outer_12345678"
    assert caller_event["proposal_id"] == "ordp_outer_12345678"
    assert caller_event["strategy_run_id"] == "srun_outer_12345678"
    assert caller_event["client_order_id"] == "client_outer_12345678"
    assert caller_event["broker_order_id"] == "broker_outer_12345678"


@pytest.mark.integration
def test_concurrent_paper_flows_do_not_leak_context() -> None:
    stream = StringIO()
    clear_log_context()
    configure_logging(stream=stream, environment="test")
    correlations = ("corr_concurrent_a_12345678", "corr_concurrent_b_12345678")

    def run(correlation_id: str) -> PaperFlowTerminal:
        config = make_paper_flow_config(inject_approval=True)
        config.correlation_id = correlation_id
        return run_paper_flow(config).terminal

    with ThreadPoolExecutor(max_workers=2) as executor:
        terminals = tuple(executor.map(run, correlations))

    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert terminals == (PaperFlowTerminal.FILLED, PaperFlowTerminal.FILLED)
    assert {event["correlation_id"] for event in events} == set(correlations)
    for correlation_id in correlations:
        flow_events = [event for event in events if event["correlation_id"] == correlation_id]
        assert flow_events
        assert all(event["causation_id"] != "cmd_outer_12345678" for event in flow_events)
        assert {event["strategy_run_id"] for event in flow_events} == {"srun_01HZYD4APAPER0001"}


@pytest.mark.integration
def test_paper_flow_dry_run_stops_at_approval_pending() -> None:
    result = run_paper_flow(make_paper_flow_config(inject_approval=False))
    assert result.terminal is PaperFlowTerminal.APPROVAL_PENDING
    assert result.lifecycle is OrderLifecycleState.APPROVAL_PENDING
    assert result.challenge_id is not None
    assert "execute_order" not in {step.name for step in result.steps}


@pytest.mark.integration
def test_paper_flow_risk_rejection() -> None:
    result = run_paper_flow(
        make_paper_flow_config(
            inject_approval=True,
            risk_config=make_risk_config(
                allowlist=(
                    AllowlistEntry(
                        instrument_id="rh_inst_msft_xnas",
                        symbol="MSFT",
                        exchange="XNAS",
                        currency="USD",
                        asset_type=AssetType.EQUITY,
                    ),
                )
            ),
        )
    )
    assert result.terminal is PaperFlowTerminal.RISK_REJECTED
    assert result.lifecycle is OrderLifecycleState.RISK_REJECTED
    assert "execute_order" not in {step.name for step in result.steps}
    assert "create_proposal" not in {step.name for step in result.steps}


@pytest.mark.integration
def test_paper_flow_expired_approval() -> None:
    result = run_paper_flow(make_paper_flow_config(inject_approval=True, expire_approval=True))
    assert result.terminal is PaperFlowTerminal.APPROVAL_EXPIRED
    assert result.lifecycle is OrderLifecycleState.APPROVAL_EXPIRED
    assert "execute_order" not in {step.name for step in result.steps}


@pytest.mark.integration
def test_paper_flow_unknown_broker_then_reconcile() -> None:
    port = _UnknownWritePort()
    result = run_paper_flow(make_paper_flow_config(inject_approval=True, write_port=port))
    assert result.terminal is PaperFlowTerminal.SUBMIT_UNKNOWN
    assert result.lifecycle is OrderLifecycleState.MANUAL_REVIEW
    assert port.submit_calls == 1
    assert "reconcile_after_unknown" in {step.name for step in result.steps}
    assert "blind_retry_blocked" in {step.name for step in result.steps}
    assert result.error is not None
    assert "blind" in result.error.lower() or "SUBMIT_UNKNOWN" in result.error
    assert len(result.audit_events) >= 3
    assert all(event.correlation_id == result.correlation_id for event in result.audit_events)


@pytest.mark.integration
def test_paper_flow_partial_fill() -> None:
    result = run_paper_flow(make_paper_flow_config(inject_approval=True, market_liquidity="1"))
    assert result.terminal is PaperFlowTerminal.PARTIALLY_FILLED
    assert result.lifecycle is OrderLifecycleState.PARTIALLY_FILLED
    assert result.filled_quantity == Decimal("1")
    assert result.conservation_ok is True
