"""Unit tests for the deterministic Paper Broker (P03-T14)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from random import Random

import pytest
from paper_fixtures import (
    FixedClock,
    make_cancel_command,
    make_cost_model,
    make_market_event,
    make_paper_broker,
    make_paper_proposal,
    submit_paper,
)
from pydantic import ValidationError

from ainvest.execution.broker import (
    BrokerInvalidOrderError,
    BrokerRejectedError,
    BrokerSubmitOutcome,
    assert_no_replace_operation,
    assert_read_port_has_no_write_methods,
)
from ainvest.execution.paper import (
    PaperBroker,
    PaperCostModel,
    PaperRejectReason,
    as_read_port,
    as_write_port,
)
from ainvest.schemas.broker import BrokerOrderStatus, CancelStatus
from ainvest.schemas.portfolio import AccountScope


@pytest.mark.unit
def test_cost_model_requires_explicit_components() -> None:
    model = make_cost_model(fee_bps="0", half_spread_bps="0", slippage_bps="0")
    assert model.fee_bps == Decimal("0")
    with pytest.raises(ValidationError):
        PaperCostModel()  # type: ignore[call-arg]


@pytest.mark.unit
def test_submit_accept_cancel_and_idempotent_resubmit() -> None:
    clock = FixedClock()
    broker = make_paper_broker(clock=clock)
    proposal = make_paper_proposal()
    first = submit_paper(broker, proposal)
    assert first.outcome is BrokerSubmitOutcome.ACCEPTED
    assert first.broker_order is not None
    assert first.broker_order.status is BrokerOrderStatus.ACCEPTED

    cash_before = broker.get_account(AccountScope.PAPER).cash
    second = submit_paper(broker, proposal)
    assert second.outcome is BrokerSubmitOutcome.ACCEPTED
    assert second.broker_order is not None
    assert second.broker_order.broker_order_id == first.broker_order.broker_order_id
    assert broker.get_account(AccountScope.PAPER).cash == cash_before

    conflict = make_paper_proposal(quantity="1", maximum_notional="214.50")
    with pytest.raises(BrokerInvalidOrderError, match="idempotency"):
        submit_paper(broker, conflict)

    cancel = broker.cancel(
        make_cancel_command(
            proposal=proposal,
            broker_order_id=first.broker_order.broker_order_id,
        )
    )
    assert cancel.status is CancelStatus.CONFIRMED
    orders = broker.get_orders(AccountScope.PAPER)
    assert orders[0].status is BrokerOrderStatus.CANCELLED

    # Same cancel command is idempotent (returns stored result).
    again = broker.cancel(
        make_cancel_command(
            proposal=proposal,
            broker_order_id=first.broker_order.broker_order_id,
        )
    )
    assert again == cancel


@pytest.mark.unit
def test_cancel_rejects_order_not_found() -> None:
    broker = make_paper_broker()
    proposal = make_paper_proposal()
    result = broker.cancel(
        make_cancel_command(proposal=proposal, broker_order_id="paper_missing_order")
    )
    assert result.status is CancelStatus.REJECTED
    assert result.reason_code == PaperRejectReason.ORDER_NOT_FOUND.value


@pytest.mark.unit
def test_cancel_rejects_when_order_already_filled() -> None:
    broker = make_paper_broker()
    proposal = make_paper_proposal(quantity="2", limit_price="214.50")
    submitted = submit_paper(broker, proposal)
    assert submitted.broker_order is not None
    broker.inject_market_event(make_market_event(liquidity="2"))
    assert broker.get_orders(AccountScope.PAPER)[0].status is BrokerOrderStatus.FILLED

    result = broker.cancel(
        make_cancel_command(
            proposal=proposal,
            broker_order_id=submitted.broker_order.broker_order_id,
        )
    )
    assert result.status is CancelStatus.REJECTED
    assert result.reason_code == PaperRejectReason.ORDER_NOT_CANCELABLE.value


@pytest.mark.unit
def test_cancel_rejects_when_order_already_cancelled() -> None:
    broker = make_paper_broker()
    proposal = make_paper_proposal()
    submitted = submit_paper(broker, proposal)
    assert submitted.broker_order is not None
    broker_order_id = submitted.broker_order.broker_order_id

    first = broker.cancel(
        make_cancel_command(
            proposal=proposal,
            broker_order_id=broker_order_id,
            idempotency_key="cancel-key-first",
        )
    )
    assert first.status is CancelStatus.CONFIRMED

    second = broker.cancel(
        make_cancel_command(
            proposal=proposal,
            broker_order_id=broker_order_id,
            cancel_id="cncl_01HZYEXAMPLE0002",
            idempotency_key="cancel-key-second",
        )
    )
    assert second.status is CancelStatus.REJECTED
    assert second.reason_code == PaperRejectReason.ORDER_NOT_CANCELABLE.value


@pytest.mark.unit
def test_cancel_rejects_non_paper_account_scope() -> None:
    broker = make_paper_broker()
    proposal = make_paper_proposal()
    submitted = submit_paper(broker, proposal)
    assert submitted.broker_order is not None

    result = broker.cancel(
        make_cancel_command(
            proposal=proposal,
            broker_order_id=submitted.broker_order.broker_order_id,
            account_scope=AccountScope.AGENTIC,
        )
    )
    assert result.status is CancelStatus.REJECTED
    assert result.reason_code == PaperRejectReason.ACCOUNT_SCOPE_NOT_PAPER.value
    assert broker.get_orders(AccountScope.PAPER)[0].status is BrokerOrderStatus.ACCEPTED


@pytest.mark.unit
def test_cancel_rejects_order_hash_mismatch() -> None:
    broker = make_paper_broker()
    proposal = make_paper_proposal()
    submitted = submit_paper(broker, proposal)
    assert submitted.broker_order is not None

    result = broker.cancel(
        make_cancel_command(
            proposal=proposal,
            broker_order_id=submitted.broker_order.broker_order_id,
            order_hash="sha256:" + ("b" * 64),
        )
    )
    assert result.status is CancelStatus.REJECTED
    assert result.reason_code == PaperRejectReason.IDEMPOTENCY_CONFLICT.value
    assert broker.get_orders(AccountScope.PAPER)[0].status is BrokerOrderStatus.ACCEPTED


@pytest.mark.unit
def test_cancel_rejects_proposal_id_mismatch() -> None:
    broker = make_paper_broker()
    proposal = make_paper_proposal()
    submitted = submit_paper(broker, proposal)
    assert submitted.broker_order is not None

    result = broker.cancel(
        make_cancel_command(
            proposal=proposal,
            broker_order_id=submitted.broker_order.broker_order_id,
            proposal_id="ordp_01HZYEXAMPLE9999",
        )
    )
    assert result.status is CancelStatus.REJECTED
    assert result.reason_code == PaperRejectReason.IDEMPOTENCY_CONFLICT.value


@pytest.mark.unit
def test_cancel_idempotency_key_conflict_raises() -> None:
    broker = make_paper_broker()
    proposal = make_paper_proposal()
    submitted = submit_paper(broker, proposal)
    assert submitted.broker_order is not None
    broker_order_id = submitted.broker_order.broker_order_id

    first = broker.cancel(
        make_cancel_command(
            proposal=proposal,
            broker_order_id=broker_order_id,
            cancel_id="cncl_01HZYEXAMPLE0001",
            idempotency_key="cancel-key-reuse",
        )
    )
    assert first.status is CancelStatus.CONFIRMED

    with pytest.raises(BrokerInvalidOrderError, match="idempotency") as exc_info:
        broker.cancel(
            make_cancel_command(
                proposal=proposal,
                broker_order_id=broker_order_id,
                cancel_id="cncl_01HZYEXAMPLE0002",
                idempotency_key="cancel-key-reuse",
            )
        )
    assert exc_info.value.reason_code == PaperRejectReason.IDEMPOTENCY_CONFLICT.value


@pytest.mark.unit
def test_cancel_idempotency_broker_order_id_conflict_raises() -> None:
    broker = make_paper_broker()
    first_proposal = make_paper_proposal()
    first = submit_paper(broker, first_proposal, client_order_id="client_a")
    assert first.broker_order is not None

    second_proposal = make_paper_proposal(quantity="1", maximum_notional="214.50")
    second = submit_paper(broker, second_proposal, client_order_id="client_b")
    assert second.broker_order is not None

    broker.cancel(
        make_cancel_command(
            proposal=first_proposal,
            broker_order_id=first.broker_order.broker_order_id,
            idempotency_key="cancel-key-shared",
        )
    )
    with pytest.raises(BrokerInvalidOrderError, match="idempotency") as exc_info:
        broker.cancel(
            make_cancel_command(
                proposal=second_proposal,
                broker_order_id=second.broker_order.broker_order_id,
                cancel_id="cncl_01HZYEXAMPLE0002",
                idempotency_key="cancel-key-shared",
            )
        )
    assert exc_info.value.reason_code == PaperRejectReason.IDEMPOTENCY_CONFLICT.value


@pytest.mark.unit
def test_buy_full_fill_from_injected_event_applies_costs() -> None:
    broker = make_paper_broker()
    proposal = make_paper_proposal(quantity="2", limit_price="214.50")
    result = submit_paper(broker, proposal)
    assert result.outcome is BrokerSubmitOutcome.ACCEPTED

    # No fill without injection.
    assert broker.get_fills(AccountScope.PAPER) == ()

    fills = broker.inject_market_event(
        make_market_event(bid="214.40", ask="214.45", last="214.42", liquidity="2")
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
    broker = make_paper_broker()
    proposal = make_paper_proposal(quantity="5", limit_price="214.50", maximum_notional="1072.50")
    submit_paper(broker, proposal)

    first = broker.inject_market_event(
        make_market_event(event_id="evt_a", liquidity="2", bid="214.40", ask="214.45")
    )
    assert len(first) == 1
    assert first[0].quantity == Decimal("2")
    orders = broker.get_orders(AccountScope.PAPER)
    assert orders[0].status is BrokerOrderStatus.PARTIALLY_FILLED

    second = broker.inject_market_event(
        make_market_event(event_id="evt_b", liquidity="10", bid="214.40", ask="214.45")
    )
    assert len(second) == 1
    assert second[0].quantity == Decimal("3")
    orders = broker.get_orders(AccountScope.PAPER)
    assert orders[0].status is BrokerOrderStatus.FILLED
    fills = broker.get_fills(AccountScope.PAPER)
    assert sum((f.quantity for f in fills), Decimal("0")) == Decimal("5")


@pytest.mark.unit
def test_identical_events_yield_identical_outcomes() -> None:
    proposal = make_paper_proposal()
    events = (
        make_market_event(event_id="evt_1", liquidity="1"),
        make_market_event(event_id="evt_2", liquidity="1"),
    )

    def run() -> tuple[tuple[tuple[str, str, str], ...], Decimal, Decimal]:
        clock = FixedClock()
        broker = make_paper_broker(clock=clock)
        submit_paper(broker, proposal)
        fills = broker.inject_market_events(events)
        account = broker.get_account(AccountScope.PAPER)
        fill_key = tuple((f.fill_id, str(f.quantity), str(f.price)) for f in fills)
        pos_qty = account.positions[0].quantity if account.positions else Decimal("0")
        return fill_key, account.cash, pos_qty

    assert run() == run()


@pytest.mark.unit
def test_reject_insufficient_cash_and_no_oversell() -> None:
    broker = make_paper_broker(cash="100.00")
    expensive = make_paper_proposal(quantity="2", limit_price="214.50")
    rejected = submit_paper(broker, expensive, client_order_id="client_poor")
    assert rejected.outcome is BrokerSubmitOutcome.REJECTED
    assert rejected.reason_code == PaperRejectReason.INSUFFICIENT_CASH.value

    # Seed a position via buy with enough cash, then try oversell.
    funded = make_paper_broker(cash="10000.00")
    buy = make_paper_proposal(quantity="2", limit_price="214.50")
    submit_paper(funded, buy, client_order_id="buy_1")
    funded.inject_market_event(make_market_event(liquidity="2"))
    sell = make_paper_proposal(
        side="SELL",
        quantity="5",
        limit_price="214.00",
        maximum_notional="1070.00",
    )
    # Distinct proposal needs distinct client id.
    sell_result = submit_paper(funded, sell, client_order_id="sell_too_many")
    assert sell_result.outcome is BrokerSubmitOutcome.REJECTED
    assert sell_result.reason_code == PaperRejectReason.INSUFFICIENT_POSITION.value


@pytest.mark.unit
def test_sell_fill_credits_cash_minus_fee() -> None:
    clock = FixedClock()
    broker = PaperBroker(
        cost_model=make_cost_model(),
        clock=clock,
        initial_cash=Decimal("10000.00"),
    )
    buy = make_paper_proposal(quantity="2", limit_price="214.50")
    submit_paper(broker, buy, client_order_id="buy_1")
    broker.inject_market_event(make_market_event(liquidity="2"))
    cash_after_buy = broker.get_account(AccountScope.PAPER).cash

    sell = make_paper_proposal(
        side="SELL",
        quantity="2",
        limit_price="214.00",
        maximum_notional="428.00",
    )
    accepted = submit_paper(broker, sell, client_order_id="sell_1")
    assert accepted.outcome is BrokerSubmitOutcome.ACCEPTED

    fills = broker.inject_market_event(
        make_market_event(
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
    broker = make_paper_broker()
    submit_paper(broker, make_paper_proposal(limit_price="214.50"))
    fills = broker.inject_market_event(
        make_market_event(bid="215.00", ask="215.10", last="215.05", liquidity="10")
    )
    assert fills == ()
    assert broker.get_orders(AccountScope.PAPER)[0].status is BrokerOrderStatus.ACCEPTED


@pytest.mark.unit
def test_expired_proposal_rejected_on_submit() -> None:
    clock = FixedClock(datetime(2026, 7, 24, 19, 0, 0, tzinfo=UTC))
    broker = make_paper_broker(clock=clock)
    proposal = make_paper_proposal(expires_at="2026-07-24T18:32:12Z")
    result = submit_paper(broker, proposal)
    assert result.outcome is BrokerSubmitOutcome.REJECTED
    assert result.reason_code == PaperRejectReason.ORDER_EXPIRED.value


@pytest.mark.unit
def test_injected_rng_partial_fill_is_deterministic() -> None:
    proposal = make_paper_proposal(quantity="10", maximum_notional="2145.00")
    event = make_market_event(liquidity="10")

    def first_fill_qty(seed: int) -> Decimal:
        broker = make_paper_broker(rng=Random(seed))
        submit_paper(broker, proposal)
        fills = broker.inject_market_event(event)
        assert len(fills) == 1
        return fills[0].quantity

    assert first_fill_qty(7) == first_fill_qty(7)
    qty = first_fill_qty(7)
    assert Decimal("1") <= qty <= Decimal("10")


@pytest.mark.unit
def test_read_write_port_views_and_no_replace() -> None:
    broker = make_paper_broker()
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
    clock = FixedClock(datetime(2026, 7, 24, 19, 0, 0, tzinfo=UTC))
    broker = make_paper_broker(clock=clock, cash="10000.00")
    # Proposal must still be unexpired relative to clock.
    proposal = make_paper_proposal(expires_at="2026-07-24T19:05:00Z")
    submit_paper(broker, proposal)
    event_time = datetime(2026, 7, 24, 18, 45, 0, tzinfo=UTC)
    fills = broker.inject_market_event(
        make_market_event(liquidity="2", observed_at=event_time, bid="214.40", ask="214.45")
    )
    assert len(fills) == 1
    assert fills[0].filled_at == event_time
    quotes = broker.get_quotes(("rh_inst_aapl_xnas",))
    assert quotes[0].provenance.observed_at == event_time


@pytest.mark.unit
def test_reject_non_paper_account_scope_on_read() -> None:
    broker = make_paper_broker()
    with pytest.raises(BrokerRejectedError):
        broker.get_account(AccountScope.AGENTIC)
