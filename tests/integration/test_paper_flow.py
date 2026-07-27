"""Integration tests for the deterministic paper orchestration loop (P03-T16)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ainvest.execution import BrokerSubmitOutcome, BrokerSubmitRequest, BrokerSubmitResult
from ainvest.execution.state_machine import OrderLifecycleState
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
    assert result.lifecycle is OrderLifecycleState.RECONCILING
    assert port.submit_calls == 1
    assert "reconcile_after_unknown" in {step.name for step in result.steps}
    assert "blind_retry_blocked" in {step.name for step in result.steps}
    assert result.error is not None
    assert "blind" in result.error.lower() or "SUBMIT_UNKNOWN" in result.error


@pytest.mark.integration
def test_paper_flow_partial_fill() -> None:
    result = run_paper_flow(make_paper_flow_config(inject_approval=True, market_liquidity="1"))
    assert result.terminal is PaperFlowTerminal.PARTIALLY_FILLED
    assert result.lifecycle is OrderLifecycleState.PARTIALLY_FILLED
    assert result.filled_quantity == Decimal("1")
    assert result.conservation_ok is True
