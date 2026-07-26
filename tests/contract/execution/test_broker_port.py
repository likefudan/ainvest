"""Contract tests for BrokerReadPort / BrokerWritePort separation (P03-T13).

Uses a Paper adapter **stub** (not the P03-T14 fill simulator) to prove the
port contract: read-only types cannot submit/cancel, unknown outcomes are
distinct from confirmed rejection, and no replace operation exists.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from ainvest.approval.order_hash import attach_order_hash
from ainvest.execution.broker import (
    FORBIDDEN_REPLACE_METHOD_NAMES,
    READ_METHOD_NAMES,
    WRITE_METHOD_NAMES,
    BrokerReadPort,
    BrokerRejectedError,
    BrokerSubmitOutcome,
    BrokerSubmitRequest,
    BrokerSubmitResult,
    BrokerUnknownOutcomeError,
    BrokerWritePort,
    assert_no_replace_operation,
    assert_read_port_has_no_write_methods,
    cancel_is_confirmed_rejection,
    cancel_is_unknown_outcome,
)
from ainvest.schemas.approval import ApprovalEvent
from ainvest.schemas.broker import (
    BrokerFill,
    BrokerOrder,
    CancelCommand,
    CancelResult,
    CancelStatus,
)
from ainvest.schemas.examples import (
    approval_event_example,
    broker_fill_example,
    broker_order_example,
    cancel_command_example,
    cancel_result_example,
    market_quote_example,
    order_proposal_valid,
    portfolio_snapshot_example,
)
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.orders import OrderProposal
from ainvest.schemas.portfolio import AccountScope, PortfolioSnapshot, PositionSnapshot


class _PaperBrokerReadStub:
    """Minimal Paper read adapter stub for protocol compliance tests."""

    def get_account(self, account_scope: AccountScope) -> PortfolioSnapshot:
        snapshot = PortfolioSnapshot.model_validate(portfolio_snapshot_example())
        assert snapshot.account_scope is account_scope or account_scope is AccountScope.PAPER
        return snapshot

    def get_positions(self, account_scope: AccountScope) -> tuple[PositionSnapshot, ...]:
        return self.get_account(account_scope).positions

    def get_quotes(self, instrument_ids: tuple[str, ...]) -> tuple[MarketQuote, ...]:
        quote = MarketQuote.model_validate(market_quote_example())
        if instrument_ids and quote.instrument.instrument_id not in instrument_ids:
            return ()
        return (quote,)

    def get_orders(
        self,
        account_scope: AccountScope,
        *,
        broker_order_ids: tuple[str, ...] | None = None,
        client_order_ids: tuple[str, ...] | None = None,
    ) -> tuple[BrokerOrder, ...]:
        del account_scope
        order = BrokerOrder.model_validate(broker_order_example())
        if broker_order_ids is not None and order.broker_order_id not in broker_order_ids:
            return ()
        if client_order_ids is not None and order.client_order_id not in client_order_ids:
            return ()
        return (order,)

    def get_fills(
        self,
        account_scope: AccountScope,
        *,
        broker_order_ids: tuple[str, ...] | None = None,
    ) -> tuple[BrokerFill, ...]:
        del account_scope
        fill = BrokerFill.model_validate(broker_fill_example())
        if broker_order_ids is not None and fill.broker_order_id not in broker_order_ids:
            return ()
        return (fill,)


class _PaperBrokerWriteStub:
    """Minimal Paper write capability stub: submit + cancel only."""

    def __init__(self) -> None:
        self._mode: str = "accept"

    def configure(self, mode: str) -> None:
        self._mode = mode

    def submit(self, request: BrokerSubmitRequest) -> BrokerSubmitResult:
        if self._mode == "reject":
            return BrokerSubmitResult.model_validate(
                {
                    "outcome": "REJECTED",
                    "client_order_id": request.client_order_id,
                    "observed_at": "2026-07-24T18:30:20Z",
                    "reason_code": "PAPER_REJECTED",
                }
            )
        if self._mode == "unknown":
            return BrokerSubmitResult.model_validate(
                {
                    "outcome": "UNKNOWN",
                    "client_order_id": request.client_order_id,
                    "observed_at": "2026-07-24T18:30:20Z",
                    "reason_code": "PAPER_SUBMIT_UNKNOWN",
                }
            )
        if self._mode == "unknown_exc":
            raise BrokerUnknownOutcomeError(
                "paper submit outcome unknown",
                reason_code="PAPER_SUBMIT_UNKNOWN",
                operation="submit",
                idempotency_key=request.client_order_id,
            )
        order_payload = broker_order_example()
        order_payload["client_order_id"] = request.client_order_id
        return BrokerSubmitResult(
            outcome=BrokerSubmitOutcome.ACCEPTED,
            client_order_id=request.client_order_id,
            observed_at=datetime(2026, 7, 24, 18, 30, 20, tzinfo=UTC),
            broker_order=BrokerOrder.model_validate(order_payload),
        )

    def cancel(self, command: CancelCommand) -> CancelResult:
        if self._mode == "reject":
            return CancelResult.model_validate(
                {
                    **cancel_result_example(),
                    "cancel_id": command.cancel_id,
                    "broker_order_id": command.broker_order_id,
                    "status": CancelStatus.REJECTED.value,
                    "reason_code": "PAPER_CANCEL_REJECTED",
                }
            )
        if self._mode == "unknown":
            return CancelResult.model_validate(
                {
                    **cancel_result_example(),
                    "cancel_id": command.cancel_id,
                    "broker_order_id": command.broker_order_id,
                    "status": CancelStatus.UNKNOWN.value,
                    "reason_code": "PAPER_CANCEL_UNKNOWN",
                }
            )
        return CancelResult.model_validate(
            {
                **cancel_result_example(),
                "cancel_id": command.cancel_id,
                "broker_order_id": command.broker_order_id,
                "status": CancelStatus.CONFIRMED.value,
                "reason_code": command.reason_code,
            }
        )


class _PaperBrokerFullStub(_PaperBrokerReadStub, _PaperBrokerWriteStub):
    """Combined Paper stub implementing both protocols (still no replace)."""


def _submit_request() -> BrokerSubmitRequest:
    payload = attach_order_hash({**order_proposal_valid(), "account_scope": "paper"})
    proposal = OrderProposal.model_validate(payload)
    approval = ApprovalEvent.model_validate(
        {
            **approval_event_example(),
            "proposal_id": proposal.proposal_id,
            "order_hash": proposal.order_hash,
            "scope": "paper",
            "method": "telegram",
        }
    )
    return BrokerSubmitRequest(
        proposal=proposal,
        approval=approval,
        client_order_id="client_ord_1",
    )


@pytest.mark.contract
def test_paper_read_stub_satisfies_read_port_only() -> None:
    reader: BrokerReadPort = _PaperBrokerReadStub()
    assert isinstance(reader, BrokerReadPort)
    assert not isinstance(reader, BrokerWritePort)
    assert_read_port_has_no_write_methods(reader)
    assert_no_replace_operation(reader)

    for name in WRITE_METHOD_NAMES:
        attr = getattr(reader, name, None)
        assert attr is None or not callable(attr)

    snapshot = reader.get_account(AccountScope.PAPER)
    assert snapshot.schema_version == "1.0"
    assert reader.get_positions(AccountScope.PAPER) == snapshot.positions
    assert len(reader.get_quotes(("rh_inst_aapl_xnas",))) == 1
    assert len(reader.get_orders(AccountScope.PAPER)) == 1
    assert len(reader.get_fills(AccountScope.PAPER)) == 1


@pytest.mark.contract
def test_read_only_type_cannot_call_submit_or_cancel() -> None:
    """Structural: BrokerReadPort declares no write methods; stub has none."""
    read_members = {
        name
        for name, member in inspect.getmembers(BrokerReadPort)
        if callable(member) and not name.startswith("_")
    }
    assert read_members >= READ_METHOD_NAMES
    assert WRITE_METHOD_NAMES.isdisjoint(read_members)
    assert FORBIDDEN_REPLACE_METHOD_NAMES.isdisjoint(read_members)

    reader = _PaperBrokerReadStub()
    with pytest.raises(AttributeError):
        _ = reader.submit  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        _ = reader.cancel  # type: ignore[attr-defined]

    class _LeakingRead(_PaperBrokerReadStub):
        def submit(self, request: BrokerSubmitRequest) -> BrokerSubmitResult:
            raise AssertionError("must not be called")

    with pytest.raises(AssertionError, match="must not expose write method"):
        assert_read_port_has_no_write_methods(_LeakingRead())


@pytest.mark.contract
def test_write_port_has_submit_cancel_but_no_replace() -> None:
    write_members = {
        name
        for name, member in inspect.getmembers(BrokerWritePort)
        if callable(member) and not name.startswith("_")
    }
    assert write_members >= WRITE_METHOD_NAMES
    assert FORBIDDEN_REPLACE_METHOD_NAMES.isdisjoint(write_members)
    assert "replace" not in dir(BrokerWritePort)
    assert "replace_order" not in dir(BrokerWritePort)

    writer: BrokerWritePort = _PaperBrokerWriteStub()
    assert isinstance(writer, BrokerWritePort)
    assert_no_replace_operation(writer)

    # Distinct idempotency: submit client_order_id vs cancel idempotency_key.
    submit_req = _submit_request()
    cancel_cmd = CancelCommand.model_validate(cancel_command_example())
    assert submit_req.client_order_id != cancel_cmd.idempotency_key

    accepted = writer.submit(submit_req)
    assert accepted.outcome is BrokerSubmitOutcome.ACCEPTED
    assert accepted.broker_order is not None

    confirmed = writer.cancel(cancel_cmd)
    assert confirmed.status is CancelStatus.CONFIRMED


@pytest.mark.contract
def test_paper_write_stub_distinguishes_rejection_from_unknown() -> None:
    writer = _PaperBrokerWriteStub()
    request = _submit_request()
    command = CancelCommand.model_validate(cancel_command_example())

    writer.configure("reject")
    rejected = writer.submit(request)
    assert rejected.is_confirmed_rejection is True
    assert rejected.is_unknown_outcome is False
    cancel_rejected = writer.cancel(command)
    assert cancel_is_confirmed_rejection(cancel_rejected) is True
    assert cancel_is_unknown_outcome(cancel_rejected) is False

    writer.configure("unknown")
    unknown = writer.submit(request)
    assert unknown.is_confirmed_rejection is False
    assert unknown.is_unknown_outcome is True
    assert unknown.outcome is not BrokerSubmitOutcome.REJECTED
    cancel_unknown = writer.cancel(command)
    assert cancel_is_confirmed_rejection(cancel_unknown) is False
    assert cancel_is_unknown_outcome(cancel_unknown) is True

    writer.configure("unknown_exc")
    with pytest.raises(BrokerUnknownOutcomeError) as exc_info:
        writer.submit(request)
    assert exc_info.value.is_unknown_outcome is True
    assert not isinstance(exc_info.value, BrokerRejectedError)


@pytest.mark.contract
def test_full_paper_stub_is_both_ports_without_replace() -> None:
    broker = _PaperBrokerFullStub()
    assert isinstance(broker, BrokerReadPort)
    assert isinstance(broker, BrokerWritePort)
    assert_no_replace_operation(broker)
    for name in FORBIDDEN_REPLACE_METHOD_NAMES:
        assert not callable(getattr(broker, name, None))


@pytest.mark.contract
def test_replacement_policy_documented_on_write_port() -> None:
    doc = BrokerWritePort.__doc__ or ""
    assert "replace" in doc.lower() or "Replacement" in doc
    assert "cancel" in doc.lower()
    submit_doc = BrokerSubmitRequest.__doc__ or ""
    assert "DEC-007" in submit_doc or "new approved proposal" in submit_doc
