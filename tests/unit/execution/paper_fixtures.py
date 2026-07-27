"""Shared builders for paper broker and broker-port unit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from random import Random

from ainvest.approval.order_hash import attach_order_hash
from ainvest.execution.broker import BrokerSubmitRequest, BrokerSubmitResult
from ainvest.execution.paper import PaperBroker, PaperCostModel, PaperMarketEvent
from ainvest.schemas.approval import ApprovalEvent
from ainvest.schemas.broker import CancelCommand
from ainvest.schemas.examples import (
    approval_event_example,
    cancel_command_example,
    order_proposal_valid,
)
from ainvest.schemas.orders import OrderProposal
from ainvest.schemas.portfolio import AccountScope

T0 = datetime(2026, 7, 24, 18, 30, 20, tzinfo=UTC)


class FixedClock:
    """Deterministic clock for paper-broker tests."""

    def __init__(self, moment: datetime = T0) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: int) -> None:
        self.moment = self.moment + timedelta(seconds=seconds)


def make_cost_model(
    *,
    fee_bps: str = "10",
    half_spread_bps: str = "5",
    slippage_bps: str = "5",
) -> PaperCostModel:
    return PaperCostModel(
        fee_bps=Decimal(fee_bps),
        half_spread_bps=Decimal(half_spread_bps),
        slippage_bps=Decimal(slippage_bps),
    )


def make_paper_proposal(
    *,
    side: str = "BUY",
    quantity: str = "2",
    limit_price: str = "214.50",
    maximum_notional: str | None = None,
    expires_at: str = "2026-07-24T18:32:12Z",
) -> OrderProposal:
    qty = Decimal(quantity)
    limit = Decimal(limit_price)
    notional = maximum_notional or str(qty * limit)
    payload = attach_order_hash(
        {
            **order_proposal_valid(),
            "account_scope": "paper",
            "side": side,
            "quantity": quantity,
            "limit_price": limit_price,
            "maximum_notional": notional,
            "expires_at": expires_at,
        }
    )
    return OrderProposal.model_validate(payload)


def make_approval_for(proposal: OrderProposal) -> ApprovalEvent:
    return ApprovalEvent.model_validate(
        {
            **approval_event_example(),
            "proposal_id": proposal.proposal_id,
            "order_hash": proposal.order_hash,
            "scope": "paper",
            "method": "telegram",
        }
    )


def make_paper_proposal_and_approval() -> tuple[OrderProposal, ApprovalEvent]:
    """Paper proposal with a matching paper/telegram approval."""
    proposal = make_paper_proposal()
    return proposal, make_approval_for(proposal)


def make_submit_request(
    *,
    proposal: OrderProposal | None = None,
    approval: ApprovalEvent | None = None,
    client_order_id: str = "client_ord_1",
) -> BrokerSubmitRequest:
    if proposal is None:
        proposal, bound_approval = make_paper_proposal_and_approval()
        approval = approval or bound_approval
    elif approval is None:
        approval = make_approval_for(proposal)
    return BrokerSubmitRequest(
        proposal=proposal,
        approval=approval,
        client_order_id=client_order_id,
    )


def submit_paper(
    broker: PaperBroker,
    proposal: OrderProposal,
    *,
    client_order_id: str = "client_ord_1",
) -> BrokerSubmitResult:
    return broker.submit(make_submit_request(proposal=proposal, client_order_id=client_order_id))


def make_market_event(
    *,
    event_id: str = "evt_1",
    bid: str = "214.40",
    ask: str = "214.45",
    last: str = "214.42",
    liquidity: str = "10",
    observed_at: datetime | None = None,
    instrument_id: str = "rh_inst_aapl_xnas",
) -> PaperMarketEvent:
    return PaperMarketEvent(
        event_id=event_id,
        instrument_id=instrument_id,
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=Decimal(last),
        liquidity=Decimal(liquidity),
        observed_at=observed_at or (T0 + timedelta(seconds=30)),
    )


def make_paper_broker(
    *,
    cash: str = "10000.00",
    clock: FixedClock | None = None,
    rng: Random | None = None,
) -> PaperBroker:
    return PaperBroker(
        cost_model=make_cost_model(),
        clock=clock or FixedClock(),
        initial_cash=Decimal(cash),
        rng=rng,
    )


def make_cancel_command(
    *,
    proposal: OrderProposal | None = None,
    broker_order_id: str = "paper_client_ord_1",
    cancel_id: str = "cncl_01HZYEXAMPLE0001",
    idempotency_key: str = "cancel-key-0001",
    account_scope: AccountScope | str = AccountScope.PAPER,
    order_hash: str | None = None,
    proposal_id: str | None = None,
    reason_code: str = "USER_REQUESTED",
    requested_at: str = "2026-07-24T18:31:00Z",
) -> CancelCommand:
    """Build a cancel command, optionally bound to a paper proposal."""
    if proposal is not None:
        proposal_id = proposal_id or proposal.proposal_id
        order_hash = order_hash or proposal.order_hash
    else:
        example = cancel_command_example()
        proposal_id = proposal_id or example["proposal_id"]
        order_hash = order_hash or example["order_hash"]
    scope = account_scope.value if isinstance(account_scope, AccountScope) else account_scope
    return CancelCommand.model_validate(
        {
            **cancel_command_example(),
            "cancel_id": cancel_id,
            "proposal_id": proposal_id,
            "broker_order_id": broker_order_id,
            "order_hash": order_hash,
            "account_scope": scope,
            "reason_code": reason_code,
            "idempotency_key": idempotency_key,
            "requested_at": requested_at,
        }
    )
