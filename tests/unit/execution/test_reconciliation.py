"""Unit tests for paper order reconciliation (P03-T15)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ainvest.execution.reconciliation import (
    AlertSeverity,
    DiscrepancyCode,
    InMemoryAlertSink,
    LocalOrderExpectation,
    OrderReconciler,
    apply_matched_fills,
)
from ainvest.execution.state_machine import OrderLifecycleState
from ainvest.portfolio.ledger import LedgerApplyStatus, PortfolioLedger
from ainvest.schemas.broker import (
    BrokerFill,
    BrokerOrder,
    BrokerOrderStatus,
    ReconciliationOutcome,
)
from ainvest.schemas.common import AssetType, InstrumentIdentity, OrderSide
from ainvest.schemas.portfolio import AccountScope

AS_OF = datetime(2026, 7, 27, 16, 0, 0, tzinfo=UTC)
ORDER_HASH = "sha256:" + ("ab" * 32)


def _instrument() -> InstrumentIdentity:
    return InstrumentIdentity(
        instrument_id="rh_inst_aapl",
        symbol="AAPL",
        exchange="XNAS",
        currency="USD",
        asset_type=AssetType.EQUITY,
        identity_as_of=AS_OF,
    )


def _broker_order(
    *,
    client_order_id: str = "client_ord_1",
    broker_order_id: str = "paper_client_ord_1",
    status: BrokerOrderStatus = BrokerOrderStatus.ACCEPTED,
    side: OrderSide = OrderSide.BUY,
) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        proposal_id="ordp_01HZYRECONCILE0001",
        order_hash=ORDER_HASH,
        account_scope=AccountScope.PAPER,
        side=side,
        status=status,
        submitted_at=AS_OF,
        updated_at=AS_OF,
    )


def _local(
    *,
    quantity: str = "2",
    limit: str = "214.50",
    filled: str = "0",
    known_fill_ids: tuple[str, ...] = (),
    lifecycle: OrderLifecycleState = OrderLifecycleState.SUBMITTED,
    broker_order_id: str | None = "paper_client_ord_1",
    side: OrderSide = OrderSide.BUY,
) -> LocalOrderExpectation:
    return LocalOrderExpectation(
        client_order_id="client_ord_1",
        proposal_id="ordp_01HZYRECONCILE0001",
        side=side,
        expected_quantity=Decimal(quantity),
        expected_limit_price=Decimal(limit),
        instrument=_instrument(),
        local_lifecycle=lifecycle,
        broker_order_id=broker_order_id,
        known_fill_ids=known_fill_ids,
        filled_quantity=Decimal(filled),
    )


def _fill(
    *,
    fill_id: str,
    quantity: str = "1",
    price: str = "214.00",
    filled_at: datetime | None = None,
    broker_order_id: str = "paper_client_ord_1",
) -> BrokerFill:
    return BrokerFill(
        fill_id=fill_id,
        broker_order_id=broker_order_id,
        quantity=Decimal(quantity),
        price=Decimal(price),
        filled_at=filled_at or AS_OF,
    )


@pytest.mark.unit
def test_matched_order_applies_new_fills_to_ledger() -> None:
    alerts = InMemoryAlertSink()
    reconciler = OrderReconciler(alert_sink=alerts)
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("10000"),
        as_of=AS_OF,
    )
    fill = _fill(fill_id="fill_new_1", quantity="2", price="214.00")
    report = reconciler.reconcile(
        _local(),
        broker_orders=(_broker_order(status=BrokerOrderStatus.FILLED),),
        broker_fills=(fill,),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYMATCHED00001",
        ledger=ledger,
    )
    assert report.outcome is ReconciliationOutcome.MATCHED
    assert report.new_fill_ids == ("fill_new_1",)
    assert ledger.position_quantity("rh_inst_aapl") == Decimal("2")
    assert alerts.alerts == []
    ledger.assert_conservation()


@pytest.mark.unit
def test_duplicate_and_late_fills_are_idempotent() -> None:
    reconciler = OrderReconciler()
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("10000"),
        as_of=AS_OF,
    )
    early = _fill(
        fill_id="fill_dup_1",
        quantity="1",
        price="210",
        filled_at=AS_OF + timedelta(seconds=1),
    )
    # First reconcile applies the fill.
    first = reconciler.reconcile(
        _local(lifecycle=OrderLifecycleState.PARTIALLY_FILLED),
        broker_orders=(_broker_order(status=BrokerOrderStatus.PARTIALLY_FILLED),),
        broker_fills=(early,),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYDUP00000001",
        ledger=ledger,
    )
    assert first.outcome is ReconciliationOutcome.MATCHED
    cash_after = ledger.cash

    # Replay with known fill + out-of-order late fill already applied.
    late_duplicate = _fill(
        fill_id="fill_dup_1",
        quantity="1",
        price="210",
        filled_at=AS_OF + timedelta(seconds=30),
    )
    second = reconciler.reconcile(
        _local(
            lifecycle=OrderLifecycleState.PARTIALLY_FILLED,
            known_fill_ids=("fill_dup_1",),
            filled="1",
        ),
        broker_orders=(_broker_order(status=BrokerOrderStatus.PARTIALLY_FILLED),),
        broker_fills=(late_duplicate,),
        observed_at=AS_OF + timedelta(minutes=1),
        reconciliation_id="recon_01HZYDUP00000002",
        ledger=ledger,
    )
    assert second.outcome is ReconciliationOutcome.MATCHED
    assert second.duplicate_fill_ids == ("fill_dup_1",)
    assert second.new_fill_ids == ()
    assert ledger.cash == cash_after


@pytest.mark.unit
def test_quantity_mismatch_routes_to_manual_review_without_ledger_write() -> None:
    alerts = InMemoryAlertSink()
    reconciler = OrderReconciler(alert_sink=alerts)
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("10000"),
        as_of=AS_OF,
    )
    overfill = _fill(fill_id="fill_over_1", quantity="5", price="214")
    report = reconciler.reconcile(
        _local(quantity="2"),
        broker_orders=(_broker_order(status=BrokerOrderStatus.FILLED),),
        broker_fills=(overfill,),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYQTYMISMATCH01",
        ledger=ledger,
    )
    assert report.outcome is ReconciliationOutcome.MANUAL_REVIEW
    assert report.requires_manual_review
    assert DiscrepancyCode.QUANTITY_MISMATCH in report.discrepancy_codes
    assert ledger.cash == Decimal("10000")
    assert ledger.positions() == {}
    assert len(alerts.alerts) == 1
    assert alerts.alerts[0].severity is AlertSeverity.CRITICAL


@pytest.mark.unit
def test_price_above_limit_is_manual_review() -> None:
    alerts = InMemoryAlertSink()
    reconciler = OrderReconciler(alert_sink=alerts)
    report = reconciler.reconcile(
        _local(limit="214.50"),
        broker_orders=(_broker_order(status=BrokerOrderStatus.FILLED),),
        broker_fills=(_fill(fill_id="fill_px_1", quantity="2", price="220.00"),),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYPRICEMISMATCH1",
    )
    assert report.outcome is ReconciliationOutcome.MANUAL_REVIEW
    assert DiscrepancyCode.PRICE_MISMATCH in report.discrepancy_codes
    assert alerts.alerts


@pytest.mark.unit
def test_missing_broker_order_for_submitted_local() -> None:
    alerts = InMemoryAlertSink()
    reconciler = OrderReconciler(alert_sink=alerts)
    report = reconciler.reconcile(
        _local(lifecycle=OrderLifecycleState.SUBMIT_UNKNOWN),
        broker_orders=(),
        broker_fills=(),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYMISSINGORDER1",
    )
    assert report.outcome is ReconciliationOutcome.MANUAL_REVIEW
    assert DiscrepancyCode.MISSING_BROKER_ORDER in report.discrepancy_codes
    assert alerts.alerts[0].reason_code == "MISSING_BROKER_ORDER"


@pytest.mark.unit
def test_unknown_broker_order_flagged() -> None:
    alerts = InMemoryAlertSink()
    reconciler = OrderReconciler(alert_sink=alerts)
    orphan = _broker_order(
        client_order_id="client_orphan_9",
        broker_order_id="paper_orphan_9",
    )
    reports = reconciler.find_unknown_broker_orders(
        locals_=(_local(),),
        broker_orders=(orphan, _broker_order()),
        observed_at=AS_OF,
    )
    assert len(reports) == 1
    assert reports[0].outcome is ReconciliationOutcome.MANUAL_REVIEW
    assert DiscrepancyCode.UNKNOWN_BROKER_ORDER in reports[0].discrepancy_codes
    assert alerts.alerts


@pytest.mark.unit
def test_unknown_broker_status_does_not_apply_fills() -> None:
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("10000"),
        as_of=AS_OF,
    )
    reconciler = OrderReconciler(alert_sink=InMemoryAlertSink())
    report = reconciler.reconcile(
        _local(lifecycle=OrderLifecycleState.RECONCILING),
        broker_orders=(_broker_order(status=BrokerOrderStatus.UNKNOWN),),
        broker_fills=(_fill(fill_id="fill_unknown_status", quantity="2"),),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYUNKNOWNSTAT01",
        ledger=ledger,
    )
    assert report.outcome is ReconciliationOutcome.MANUAL_REVIEW
    assert ledger.cash == Decimal("10000")


@pytest.mark.unit
def test_apply_matched_fills_helper_sorts_and_dedupes() -> None:
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("10000"),
        as_of=AS_OF,
    )
    instrument = _instrument()
    fills = (
        _fill(fill_id="fill_f2", quantity="1", price="100", filled_at=AS_OF + timedelta(seconds=2)),
        _fill(fill_id="fill_f1", quantity="1", price="100", filled_at=AS_OF + timedelta(seconds=1)),
        _fill(fill_id="fill_f1", quantity="1", price="100", filled_at=AS_OF + timedelta(seconds=1)),
    )
    results = apply_matched_fills(
        ledger,
        fills=fills,
        side=OrderSide.BUY,
        instrument=instrument,
    )
    # Sorted by filled_at then fill_id: fill_f1, fill_f1 (dup), fill_f2.
    assert [item.status for item in results] == [
        LedgerApplyStatus.APPLIED,
        LedgerApplyStatus.DUPLICATE,
        LedgerApplyStatus.APPLIED,
    ]
    assert ledger.position_quantity(instrument.instrument_id) == Decimal("2")
    ledger.assert_conservation()


@pytest.mark.unit
def test_apply_matched_fills_helper_is_atomic_on_reject() -> None:
    """Public helper must not leave partial cash mutations on mid-batch reject."""
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("250"),
        as_of=AS_OF,
    )
    instrument = _instrument()
    fills = (
        _fill(fill_id="fill_ok_100", quantity="1", price="100"),
        _fill(
            fill_id="fill_fail_200",
            quantity="1",
            price="200",
            filled_at=AS_OF + timedelta(seconds=1),
        ),
    )
    results = apply_matched_fills(
        ledger,
        fills=fills,
        side=OrderSide.BUY,
        instrument=instrument,
    )
    assert ledger.cash == Decimal("250")
    assert ledger.positions() == {}
    assert ledger.applied_fill_ids == frozenset()
    assert [item.status for item in results] == [
        LedgerApplyStatus.REJECTED,
        LedgerApplyStatus.REJECTED,
    ]
    assert results[0].reason_code == "BATCH_ROLLED_BACK"
    assert results[1].reason_code == "INSUFFICIENT_CASH"


@pytest.mark.unit
def test_status_mismatch_manual_review() -> None:
    reconciler = OrderReconciler(alert_sink=InMemoryAlertSink())
    report = reconciler.reconcile(
        _local(lifecycle=OrderLifecycleState.FILLED, filled="2"),
        broker_orders=(_broker_order(status=BrokerOrderStatus.ACCEPTED),),
        broker_fills=(),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYSTATUSMISM01",
    )
    assert report.outcome is ReconciliationOutcome.MANUAL_REVIEW
    assert DiscrepancyCode.STATUS_MISMATCH in report.discrepancy_codes


@pytest.mark.unit
def test_partial_ledger_apply_rolls_back_on_reject() -> None:
    """Cash 250 + fills 100 then 200 → MANUAL_REVIEW with cash still 250."""
    alerts = InMemoryAlertSink()
    reconciler = OrderReconciler(alert_sink=alerts)
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("250"),
        as_of=AS_OF,
    )
    first = _fill(fill_id="fill_ok_100", quantity="1", price="100")
    second = _fill(
        fill_id="fill_fail_200",
        quantity="1",
        price="200",
        filled_at=AS_OF + timedelta(seconds=1),
    )
    report = reconciler.reconcile(
        _local(quantity="3", limit="200"),
        broker_orders=(_broker_order(status=BrokerOrderStatus.PARTIALLY_FILLED),),
        broker_fills=(first, second),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYPARTIALROLL1",
        ledger=ledger,
    )
    assert report.outcome is ReconciliationOutcome.MANUAL_REVIEW
    assert report.requires_manual_review
    assert DiscrepancyCode.LEDGER_REJECTED in report.discrepancy_codes
    assert DiscrepancyCode.FILL_QUANTITY_MISMATCH not in report.discrepancy_codes
    assert report.reason_code == "INSUFFICIENT_CASH"
    assert ledger.cash == Decimal("250")
    assert ledger.positions() == {}
    assert ledger.applied_fill_ids == frozenset()
    assert alerts.alerts


@pytest.mark.unit
def test_side_mismatch_buy_local_sell_broker_is_manual_review() -> None:
    alerts = InMemoryAlertSink()
    reconciler = OrderReconciler(alert_sink=alerts)
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("10000"),
        as_of=AS_OF,
    )
    report = reconciler.reconcile(
        _local(side=OrderSide.BUY),
        broker_orders=(_broker_order(status=BrokerOrderStatus.FILLED, side=OrderSide.SELL),),
        broker_fills=(_fill(fill_id="fill_side_1", quantity="2", price="214"),),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYSIDEMISMATCH1",
        ledger=ledger,
    )
    assert report.outcome is ReconciliationOutcome.MANUAL_REVIEW
    assert DiscrepancyCode.SIDE_MISMATCH in report.discrepancy_codes
    assert ledger.cash == Decimal("10000")
    assert ledger.positions() == {}
    assert alerts.alerts


@pytest.mark.unit
def test_orphan_fill_assigns_unknown_fill() -> None:
    alerts = InMemoryAlertSink()
    reconciler = OrderReconciler(alert_sink=alerts)
    orphan = _fill(
        fill_id="fill_orphan_1",
        quantity="1",
        price="100",
        broker_order_id="paper_unknown_99",
    )
    report = reconciler.reconcile(
        _local(),
        broker_orders=(_broker_order(status=BrokerOrderStatus.ACCEPTED),),
        broker_fills=(orphan,),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYORPHANFILL01",
    )
    assert report.outcome is ReconciliationOutcome.MANUAL_REVIEW
    assert DiscrepancyCode.UNKNOWN_FILL in report.discrepancy_codes
    assert DiscrepancyCode.UNKNOWN_FILL.value in alerts.alerts[0].details


@pytest.mark.unit
def test_sibling_fill_in_shared_feed_is_not_unknown_fill() -> None:
    """Own + sibling fills with both broker orders present → MATCHED (clean)."""
    alerts = InMemoryAlertSink()
    reconciler = OrderReconciler(alert_sink=alerts)
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("10000"),
        as_of=AS_OF,
    )
    sibling_order = _broker_order(
        client_order_id="client_ord_sibling",
        broker_order_id="paper_client_ord_sibling",
        status=BrokerOrderStatus.PARTIALLY_FILLED,
    )
    own_order = _broker_order(status=BrokerOrderStatus.FILLED)
    own_fill = _fill(fill_id="fill_own_1", quantity="2", price="214")
    sibling_fill = _fill(
        fill_id="fill_sibling_1",
        quantity="1",
        price="210",
        broker_order_id="paper_client_ord_sibling",
    )
    report = reconciler.reconcile(
        _local(),
        broker_orders=(own_order, sibling_order),
        broker_fills=(own_fill, sibling_fill),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYSIBLINGFILL01",
        ledger=ledger,
    )
    assert report.outcome is ReconciliationOutcome.MATCHED
    assert DiscrepancyCode.UNKNOWN_FILL not in report.discrepancy_codes
    assert report.new_fill_ids == ("fill_own_1",)
    assert ledger.position_quantity("rh_inst_aapl") == Decimal("2")
    assert alerts.alerts == []


@pytest.mark.unit
def test_broker_filled_underfill_is_manual_review() -> None:
    """Broker FILLED with fill qty < expected, including local FILLED filled_qty=0."""
    alerts = InMemoryAlertSink()
    reconciler = OrderReconciler(alert_sink=alerts)
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("10000"),
        as_of=AS_OF,
    )
    report = reconciler.reconcile(
        _local(lifecycle=OrderLifecycleState.FILLED, filled="0", quantity="2"),
        broker_orders=(_broker_order(status=BrokerOrderStatus.FILLED),),
        broker_fills=(_fill(fill_id="fill_short_1", quantity="1", price="214"),),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYUNDERFILL0001",
        ledger=ledger,
    )
    assert report.outcome is ReconciliationOutcome.MANUAL_REVIEW
    assert DiscrepancyCode.QUANTITY_MISMATCH in report.discrepancy_codes
    assert ledger.cash == Decimal("10000")
    assert ledger.applied_fill_ids == frozenset()
    assert alerts.alerts


@pytest.mark.unit
def test_fill_quantity_mismatch_even_when_new_fills_exist() -> None:
    """Local filled_quantity disagrees with known fills; do not skip when unseen fills exist."""
    alerts = InMemoryAlertSink()
    reconciler = OrderReconciler(alert_sink=alerts)
    ledger = PortfolioLedger(
        account_scope=AccountScope.PAPER,
        currency="USD",
        opening_cash=Decimal("10000"),
        as_of=AS_OF,
    )
    report = reconciler.reconcile(
        _local(
            lifecycle=OrderLifecycleState.PARTIALLY_FILLED,
            known_fill_ids=(),
            filled="1",
            quantity="2",
        ),
        broker_orders=(_broker_order(status=BrokerOrderStatus.PARTIALLY_FILLED),),
        broker_fills=(_fill(fill_id="fill_a", quantity="1", price="214"),),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYQTYSKIP00001",
        ledger=ledger,
    )
    assert report.outcome is ReconciliationOutcome.MANUAL_REVIEW
    assert DiscrepancyCode.FILL_QUANTITY_MISMATCH in report.discrepancy_codes
    assert ledger.cash == Decimal("10000")
    assert ledger.applied_fill_ids == frozenset()
    assert alerts.alerts


@pytest.mark.unit
def test_broker_order_id_mismatch_uses_distinct_code() -> None:
    alerts = InMemoryAlertSink()
    reconciler = OrderReconciler(alert_sink=alerts)
    # Match via client_order_id, but local remembers a different broker_order_id.
    report = reconciler.reconcile(
        _local(broker_order_id="paper_stale_id_99"),
        broker_orders=(
            _broker_order(
                broker_order_id="paper_client_ord_1",
                status=BrokerOrderStatus.ACCEPTED,
            ),
        ),
        broker_fills=(),
        observed_at=AS_OF,
        reconciliation_id="recon_01HZYBROKERIDMIS01",
    )
    assert report.outcome is ReconciliationOutcome.MANUAL_REVIEW
    assert DiscrepancyCode.BROKER_ORDER_ID_MISMATCH in report.discrepancy_codes
    assert DiscrepancyCode.CLIENT_ORDER_ID_MISMATCH not in report.discrepancy_codes
    assert alerts.alerts
