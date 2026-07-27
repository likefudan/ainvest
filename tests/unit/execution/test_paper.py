"""Unit tests for the deterministic Paper Broker (P03-T14)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from random import Random

import pytest
from pydantic import ValidationError

from ainvest.approval.order_hash import attach_order_hash
from ainvest.execution.broker import (
    BrokerInvalidOrderError,
    BrokerSubmitOutcome,
    BrokerSubmitRequest,
    BrokerSubmitResult,
    assert_no_replace_operation,
    assert_read_port_has_no_write_methods,
)
from ainvest.execution.paper import (
    PaperBroker,
    PaperCostModel,
    PaperMarketEvent,
    PaperRejectReason,
    as_read_port,
    as_write_port,
)
from ainvest.schemas.approval import ApprovalEvent
from ainvest.schemas.broker import BrokerOrderStatus, CancelCommand, CancelStatus
from ainvest.schemas.examples import approval_event_example, order_proposal_valid
from ainvest.schemas.orders import OrderProposal
from ainvest.schemas.portfolio import AccountScope

_T0 = datetime(2026, 7, 24, 18, 30, 20, tzinfo=UTC)


class _FixedClock:
    def __init__(self, moment: datetime = _T0) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: int) -> None:
        self.moment = self.moment + timedelta(seconds=seconds)


def _costs(
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


def _paper_proposal(
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


def _approval_for(proposal: OrderProposal) -> ApprovalEvent:
    return ApprovalEvent.model_validate(
        {
            **approval_event_example(),
            "proposal_id": proposal.proposal_id,
            "order_hash": proposal.order_hash,
            "scope": "paper",
            "method": "telegram",
        }
    )


def _submit(
    broker: PaperBroker,
    proposal: OrderProposal,
    *,
    client_order_id: str = "client_ord_1",
) -> BrokerSubmitResult:
    return broker.submit(
        BrokerSubmitRequest(
            proposal=proposal,
            approval=_approval_for(proposal),
            client_order_id=client_order_id,
        )
    )


def _event(
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
        observed_at=observed_at or (_T0 + timedelta(seconds=30)),
    )


def _broker(*, cash: str = "10000.00", clock: _FixedClock | None = None) -> PaperBroker:
    return PaperBroker(
        cost_model=_costs(),
        clock=clock or _FixedClock(),
        initial_cash=Decimal(cash),
    )


@pytest.mark.unit
def test_cost_model_requires_explicit_components() -> None:
    model = _costs(fee_bps="0", half_spread_bps="0", slippage_bps="0")
    assert model.fee_bps == Decimal("0")
    with pytest.raises(ValidationError):
        PaperCostModel()  # type: ignore[call-arg]


@pytest.mark.unit
def test_submit_accept_cancel_and_idempotent_resubmit() -> None:
    clock = _FixedClock()
    broker = _broker(clock=clock)
    proposal = _paper_proposal()
    first = _submit(broker, proposal)
    assert first.outcome is BrokerSubmitOutcome.ACCEPTED
    assert first.broker_order is not None
    assert first.broker_order.status is BrokerOrderStatus.ACCEPTED

    cash_before = broker.get_account(AccountScope.PAPER).cash
    second = _submit(broker, proposal)
    assert second.outcome is BrokerSubmitOutcome.ACCEPTED
    assert second.broker_order is not None
    assert second.broker_order.broker_order_id == first.broker_order.broker_order_id
    assert broker.get_account(AccountScope.PAPER).cash == cash_before

    conflict = _paper_proposal(quantity="1", maximum_notional="214.50")
    with pytest.raises(BrokerInvalidOrderError, match="idempotency"):
        _submit(broker, conflict)

    cancel = broker.cancel(
        CancelCommand.model_validate(
            {
                "cancel_id": "cncl_01HZYEXAMPLE0001",
                "proposal_id": proposal.proposal_id,
                "broker_order_id": first.broker_order.broker_order_id,
                "order_hash": proposal.order_hash,
                "account_scope": "paper",
                "reason_code": "USER_REQUESTED",
                "idempotency_key": "cancel-key-0001",
                "requested_at": "2026-07-24T18:31:00Z",
            }
        )
    )
    assert cancel.status is CancelStatus.CONFIRMED
    orders = broker.get_orders(AccountScope.PAPER)
    assert orders[0].status is BrokerOrderStatus.CANCELLED


@pytest.mark.unit
def test_buy_full_fill_from_injected_event_applies_costs() -> None:
    broker = _broker()
    proposal = _paper_proposal(quantity="2", limit_price="214.50")
    result = _submit(broker, proposal)
    assert result.outcome is BrokerSubmitOutcome.ACCEPTED

    # No fill without injection.
    assert broker.get_fills(AccountScope.PAPER) == ()

    fills = broker.inject_market_event(
        _event(bid="214.40", ask="214.45", last="214.42", liquidity="2")
    )
    assert len(fills) == 1
    fill = fills[0]
    assert fill.quantity == Decimal("2")

    # mid=214.425; adverse bps = 5+5=10 → buy price = mid * 1.0010, clipped to limit
    quant = Decimal("0.000001")
    mid = (Decimal("214.40") + Decimal("214.45")) / Decimal("2")
    expected_raw = mid * Decimal("1.0010")
    expected_price = min(expected_raw, Decimal("214.50")).quantize(quant)
    assert fill.price == expected_price

    fee = (expected_price * Decimal("2") * Decimal("10") / Decimal("10000")).quantize(quant)
    debit = (expected_price * Decimal("2") + fee).quantize(quant)
    account = broker.get_account(AccountScope.PAPER)
    assert account.cash == Decimal("10000.00") - debit
    assert len(account.positions) == 1
    assert account.positions[0].quantity == Decimal("2")

    orders = broker.get_orders(AccountScope.PAPER)
    assert orders[0].status is BrokerOrderStatus.FILLED


@pytest.mark.unit
def test_partial_then_full_fill_accounting() -> None:
    broker = _broker()
    proposal = _paper_proposal(quantity="5", limit_price="214.50", maximum_notional="1072.50")
    _submit(broker, proposal)

    first = broker.inject_market_event(
        _event(event_id="evt_a", liquidity="2", bid="214.40", ask="214.45")
    )
    assert len(first) == 1
    assert first[0].quantity == Decimal("2")
    orders = broker.get_orders(AccountScope.PAPER)
    assert orders[0].status is BrokerOrderStatus.PARTIALLY_FILLED

    second = broker.inject_market_event(
        _event(event_id="evt_b", liquidity="10", bid="214.40", ask="214.45")
    )
    assert len(second) == 1
    assert second[0].quantity == Decimal("3")
    orders = broker.get_orders(AccountScope.PAPER)
    assert orders[0].status is BrokerOrderStatus.FILLED
    fills = broker.get_fills(AccountScope.PAPER)
    assert sum((f.quantity for f in fills), Decimal("0")) == Decimal("5")


@pytest.mark.unit
def test_identical_events_yield_identical_outcomes() -> None:
    proposal = _paper_proposal()
    events = (
        _event(event_id="evt_1", liquidity="1"),
        _event(event_id="evt_2", liquidity="1"),
    )

    def run() -> tuple[tuple[tuple[str, str, str], ...], Decimal, Decimal]:
        clock = _FixedClock()
        broker = _broker(clock=clock)
        _submit(broker, proposal)
        fills = broker.inject_market_events(events)
        account = broker.get_account(AccountScope.PAPER)
        fill_key = tuple((f.fill_id, str(f.quantity), str(f.price)) for f in fills)
        pos_qty = account.positions[0].quantity if account.positions else Decimal("0")
        return fill_key, account.cash, pos_qty

    assert run() == run()


@pytest.mark.unit
def test_reject_insufficient_cash_and_no_oversell() -> None:
    broker = _broker(cash="100.00")
    expensive = _paper_proposal(quantity="2", limit_price="214.50")
    rejected = _submit(broker, expensive, client_order_id="client_poor")
    assert rejected.outcome is BrokerSubmitOutcome.REJECTED
    assert rejected.reason_code == PaperRejectReason.INSUFFICIENT_CASH.value

    # Seed a position via buy with enough cash, then try oversell.
    funded = _broker(cash="10000.00")
    buy = _paper_proposal(quantity="2", limit_price="214.50")
    _submit(funded, buy, client_order_id="buy_1")
    funded.inject_market_event(_event(liquidity="2"))
    sell = _paper_proposal(
        side="SELL",
        quantity="5",
        limit_price="214.00",
        maximum_notional="1070.00",
    )
    # Distinct proposal needs distinct client id.
    sell_result = _submit(funded, sell, client_order_id="sell_too_many")
    assert sell_result.outcome is BrokerSubmitOutcome.REJECTED
    assert sell_result.reason_code == PaperRejectReason.INSUFFICIENT_POSITION.value


@pytest.mark.unit
def test_sell_fill_credits_cash_minus_fee() -> None:
    clock = _FixedClock()
    broker = PaperBroker(
        cost_model=_costs(),
        clock=clock,
        initial_cash=Decimal("10000.00"),
    )
    buy = _paper_proposal(quantity="2", limit_price="214.50")
    _submit(broker, buy, client_order_id="buy_1")
    broker.inject_market_event(_event(liquidity="2"))
    cash_after_buy = broker.get_account(AccountScope.PAPER).cash

    sell = _paper_proposal(
        side="SELL",
        quantity="2",
        limit_price="214.00",
        maximum_notional="428.00",
    )
    accepted = _submit(broker, sell, client_order_id="sell_1")
    assert accepted.outcome is BrokerSubmitOutcome.ACCEPTED

    fills = broker.inject_market_event(
        _event(
            event_id="evt_sell",
            bid="214.20",
            ask="214.30",
            last="214.25",
            liquidity="2",
        )
    )
    assert len(fills) == 1
    fill = fills[0]
    mid = (Decimal("214.20") + Decimal("214.30")) / Decimal("2")
    quant = Decimal("0.000001")
    expected_raw = mid * Decimal("0.9990")
    expected_price = max(expected_raw, Decimal("214.00")).quantize(quant)
    assert fill.price == expected_price
    fee = (expected_price * Decimal("2") * Decimal("10") / Decimal("10000")).quantize(quant)
    proceeds = (expected_price * Decimal("2") - fee).quantize(quant)
    account = broker.get_account(AccountScope.PAPER)
    assert account.cash == cash_after_buy + proceeds
    assert account.positions == ()


@pytest.mark.unit
def test_non_marketable_event_does_not_fill() -> None:
    broker = _broker()
    _submit(broker, _paper_proposal(limit_price="214.50"))
    fills = broker.inject_market_event(
        _event(bid="215.00", ask="215.10", last="215.05", liquidity="10")
    )
    assert fills == ()
    assert broker.get_orders(AccountScope.PAPER)[0].status is BrokerOrderStatus.ACCEPTED


@pytest.mark.unit
def test_expired_proposal_rejected_on_submit() -> None:
    clock = _FixedClock(datetime(2026, 7, 24, 19, 0, 0, tzinfo=UTC))
    broker = _broker(clock=clock)
    proposal = _paper_proposal(expires_at="2026-07-24T18:32:12Z")
    result = _submit(broker, proposal)
    assert result.outcome is BrokerSubmitOutcome.REJECTED
    assert result.reason_code == PaperRejectReason.ORDER_EXPIRED.value


@pytest.mark.unit
def test_injected_rng_partial_fill_is_deterministic() -> None:
    proposal = _paper_proposal(quantity="10", maximum_notional="2145.00")
    event = _event(liquidity="10")

    def first_fill_qty(seed: int) -> Decimal:
        broker = PaperBroker(
            cost_model=_costs(),
            clock=_FixedClock(),
            initial_cash=Decimal("10000.00"),
            rng=Random(seed),
        )
        _submit(broker, proposal)
        fills = broker.inject_market_event(event)
        assert len(fills) == 1
        return fills[0].quantity

    assert first_fill_qty(7) == first_fill_qty(7)
    qty = first_fill_qty(7)
    assert Decimal("1") <= qty <= Decimal("10")


@pytest.mark.unit
def test_read_write_port_views_and_no_replace() -> None:
    broker = _broker()
    read = as_read_port(broker)
    write = as_write_port(broker)
    assert_read_port_has_no_write_methods(read)
    assert_no_replace_operation(write)
    assert_no_replace_operation(broker)
    account = read.get_account(AccountScope.PAPER)
    assert account.cash == Decimal("10000.00")
    assert account.buying_power == Decimal("10000.00")


@pytest.mark.unit
def test_fill_timestamp_uses_event_time_not_clock() -> None:
    """Fills must stamp event.observed_at even when the injected clock is ahead."""
    clock = _FixedClock(datetime(2026, 7, 24, 19, 0, 0, tzinfo=UTC))
    broker = _broker(clock=clock, cash="10000.00")
    # Proposal must still be unexpired relative to clock.
    proposal = _paper_proposal(expires_at="2026-07-24T19:05:00Z")
    _submit(broker, proposal)
    event_time = datetime(2026, 7, 24, 18, 45, 0, tzinfo=UTC)
    fills = broker.inject_market_event(
        _event(liquidity="2", observed_at=event_time, bid="214.40", ask="214.45")
    )
    assert len(fills) == 1
    assert fills[0].filled_at == event_time
    quotes = broker.get_quotes(("rh_inst_aapl_xnas",))
    assert quotes[0].provenance.observed_at == event_time


@pytest.mark.unit
def test_reject_non_paper_account_scope_on_read() -> None:
    broker = _broker()
    from ainvest.execution.broker import BrokerRejectedError

    with pytest.raises(BrokerRejectedError):
        broker.get_account(AccountScope.AGENTIC)
