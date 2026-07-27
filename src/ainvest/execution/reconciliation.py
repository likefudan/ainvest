"""Reconcile local order expectations against broker truth (P03-T15).

Compares client order IDs, quantities, prices, and states. Duplicate,
out-of-order, and late fills are classified idempotently. Discrepancies route
to ``MANUAL_REVIEW`` with alerts — money facts are never silently rewritten.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableSequence, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Protocol, runtime_checkable

from pydantic import StringConstraints

from ainvest.execution.state_machine import OrderLifecycleState
from ainvest.portfolio.ledger import (
    FillApplyResult,
    LedgerApplyStatus,
    PortfolioLedger,
)
from ainvest.schemas.broker import (
    BrokerFill,
    BrokerOrder,
    BrokerOrderStatus,
    ReconciliationOutcome,
    ReconciliationResult,
)
from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    AssetType,
    DomainModel,
    InstrumentIdentity,
    MachineCode,
    OrderSide,
    PositiveDecimal,
    Price,
    Quantity,
    SchemaVersion,
    StableId,
    UtcDateTime,
    canonicalize_decimal,
    ensure_utc,
    parse_decimal,
)

ZERO = Decimal("0")

ReasonCode = MachineCode
ClientOrderId = Annotated[str, StringConstraints(min_length=3, max_length=128)]
BrokerOrderId = Annotated[str, StringConstraints(min_length=3, max_length=128)]


class DiscrepancyCode(StrEnum):
    """Stable machine codes for reconciliation discrepancies."""

    MATCHED = "MATCHED"
    MISSING_BROKER_ORDER = "MISSING_BROKER_ORDER"
    UNKNOWN_BROKER_ORDER = "UNKNOWN_BROKER_ORDER"
    CLIENT_ORDER_ID_MISMATCH = "CLIENT_ORDER_ID_MISMATCH"
    BROKER_ORDER_ID_MISMATCH = "BROKER_ORDER_ID_MISMATCH"
    SIDE_MISMATCH = "SIDE_MISMATCH"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    STATUS_MISMATCH = "STATUS_MISMATCH"
    UNKNOWN_FILL = "UNKNOWN_FILL"
    FILL_QUANTITY_MISMATCH = "FILL_QUANTITY_MISMATCH"
    MULTIPLE_BROKER_MATCHES = "MULTIPLE_BROKER_MATCHES"
    BROKER_ORDER_UNKNOWN_STATUS = "BROKER_ORDER_UNKNOWN_STATUS"


class AlertSeverity(StrEnum):
    """Alert urgency for operator notification sinks."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class ReconciliationAlert(DomainModel):
    """Operator-facing alert emitted on divergence / manual review."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    alert_id: StableId
    severity: AlertSeverity
    reason_code: ReasonCode
    message: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    occurred_at: UtcDateTime
    proposal_id: StableId | None = None
    client_order_id: ClientOrderId | None = None
    broker_order_id: BrokerOrderId | None = None
    details: tuple[str, ...] = ()


@runtime_checkable
class AlertSink(Protocol):
    """Port for reconciliation alerts (Telegram / ops later)."""

    def emit(self, alert: ReconciliationAlert) -> None:
        """Deliver one alert. Implementations must be side-effect safe to retry."""


class InMemoryAlertSink:
    """Test double that records alerts in order."""

    def __init__(self) -> None:
        self.alerts: MutableSequence[ReconciliationAlert] = []

    def emit(self, alert: ReconciliationAlert) -> None:
        self.alerts.append(alert)


class LocalOrderExpectation(DomainModel):
    """Local view of an order the system believes it submitted."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    client_order_id: ClientOrderId
    proposal_id: StableId
    side: OrderSide
    expected_quantity: PositiveDecimal
    expected_limit_price: Price
    instrument: InstrumentIdentity
    local_lifecycle: OrderLifecycleState
    broker_order_id: BrokerOrderId | None = None
    known_fill_ids: tuple[str, ...] = ()
    filled_quantity: Quantity = Decimal("0")


class OrderReconciliationReport(DomainModel):
    """Detailed comparison result for one local expectation."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    outcome: ReconciliationOutcome
    reason_code: ReasonCode
    proposal_id: StableId
    client_order_id: ClientOrderId
    broker_order_id: BrokerOrderId | None = None
    observed_at: UtcDateTime
    discrepancy_codes: tuple[DiscrepancyCode, ...] = ()
    broker_filled_quantity: Quantity | None = None
    new_fill_ids: tuple[str, ...] = ()
    duplicate_fill_ids: tuple[str, ...] = ()
    result: ReconciliationResult | None = None
    requires_manual_review: bool = False


_WORKING_OR_UNCERTAIN = frozenset(
    {
        OrderLifecycleState.SUBMITTING,
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.PARTIALLY_FILLED,
        OrderLifecycleState.SUBMIT_UNKNOWN,
        OrderLifecycleState.RECONCILING,
    }
)


def expected_broker_statuses(local: OrderLifecycleState) -> frozenset[BrokerOrderStatus]:
    """Map local lifecycle to acceptable broker statuses (empty = unspecified)."""
    if local in {
        OrderLifecycleState.SUBMITTING,
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.SUBMIT_UNKNOWN,
        OrderLifecycleState.RECONCILING,
    }:
        return frozenset(
            {
                BrokerOrderStatus.ACCEPTED,
                BrokerOrderStatus.PARTIALLY_FILLED,
                BrokerOrderStatus.FILLED,
                BrokerOrderStatus.CANCELLED,
                BrokerOrderStatus.REJECTED,
            }
        )
    if local is OrderLifecycleState.PARTIALLY_FILLED:
        return frozenset(
            {
                BrokerOrderStatus.PARTIALLY_FILLED,
                BrokerOrderStatus.FILLED,
                BrokerOrderStatus.CANCELLED,
            }
        )
    if local is OrderLifecycleState.FILLED:
        return frozenset({BrokerOrderStatus.FILLED})
    if local is OrderLifecycleState.CANCELLED:
        return frozenset({BrokerOrderStatus.CANCELLED, BrokerOrderStatus.PARTIALLY_FILLED})
    if local is OrderLifecycleState.REJECTED:
        return frozenset({BrokerOrderStatus.REJECTED})
    if local is OrderLifecycleState.MANUAL_REVIEW:
        return frozenset(BrokerOrderStatus)
    return frozenset()


@dataclass
class OrderReconciler:
    """Compare local expectations to broker orders/fills without mutating money.

    Matched new fills may be applied to an injected :class:`PortfolioLedger`
    only when the comparison is clean, and only via an all-or-nothing batch.
    Divergent cases alert and return ``MANUAL_REVIEW`` with the ledger left
    unchanged — never a partial money rewrite.
    """

    alert_sink: AlertSink | None = None
    _alert_seq: int = field(default=0, init=False)

    def reconcile(
        self,
        local: LocalOrderExpectation,
        *,
        broker_orders: Sequence[BrokerOrder],
        broker_fills: Sequence[BrokerFill],
        observed_at: datetime,
        reconciliation_id: StableId,
        ledger: PortfolioLedger | None = None,
        fill_fees: Mapping[str, Decimal] | None = None,
    ) -> OrderReconciliationReport:
        """Reconcile one local order. Never silently rewrites money facts."""
        observed = ensure_utc(observed_at)
        fees = fill_fees or {}
        codes: list[DiscrepancyCode] = []

        client_matches = [
            order for order in broker_orders if order.client_order_id == local.client_order_id
        ]
        id_matches = [
            order
            for order in broker_orders
            if local.broker_order_id is not None and order.broker_order_id == local.broker_order_id
        ]

        if len(client_matches) > 1:
            return self._manual(
                local=local,
                observed=observed,
                reconciliation_id=reconciliation_id,
                codes=(DiscrepancyCode.MULTIPLE_BROKER_MATCHES,),
                reason_code="MULTIPLE_BROKER_MATCHES",
                message="multiple broker orders share the same client_order_id",
                broker_order_id=None,
            )

        if len(client_matches) == 1:
            broker = client_matches[0]
        elif len(id_matches) == 1:
            broker = id_matches[0]
            if broker.client_order_id != local.client_order_id:
                codes.append(DiscrepancyCode.CLIENT_ORDER_ID_MISMATCH)
        elif len(id_matches) > 1:
            return self._manual(
                local=local,
                observed=observed,
                reconciliation_id=reconciliation_id,
                codes=(DiscrepancyCode.MULTIPLE_BROKER_MATCHES,),
                reason_code="MULTIPLE_BROKER_MATCHES",
                message="ambiguous broker order matches for local expectation",
                broker_order_id=local.broker_order_id,
            )
        else:
            if local.local_lifecycle in _WORKING_OR_UNCERTAIN:
                return self._manual(
                    local=local,
                    observed=observed,
                    reconciliation_id=reconciliation_id,
                    codes=(DiscrepancyCode.MISSING_BROKER_ORDER,),
                    reason_code="MISSING_BROKER_ORDER",
                    message="local order has no matching broker order",
                    broker_order_id=local.broker_order_id,
                )
            return self._report(
                local=local,
                observed=observed,
                reconciliation_id=reconciliation_id,
                outcome=ReconciliationOutcome.UNKNOWN,
                reason_code="NO_BROKER_ORDER",
                codes=(DiscrepancyCode.MISSING_BROKER_ORDER,),
                broker_order_id=local.broker_order_id,
                requires_manual_review=False,
            )

        if broker.status is BrokerOrderStatus.UNKNOWN:
            return self._manual(
                local=local,
                observed=observed,
                reconciliation_id=reconciliation_id,
                codes=(DiscrepancyCode.BROKER_ORDER_UNKNOWN_STATUS,),
                reason_code="BROKER_ORDER_UNKNOWN",
                message="broker order status is UNKNOWN; do not assume fills",
                broker_order_id=broker.broker_order_id,
            )

        if broker.side is not local.side:
            codes.append(DiscrepancyCode.SIDE_MISMATCH)

        allowed = expected_broker_statuses(local.local_lifecycle)
        if allowed and broker.status not in allowed:
            codes.append(DiscrepancyCode.STATUS_MISMATCH)

        if local.broker_order_id is not None and broker.broker_order_id != local.broker_order_id:
            codes.append(DiscrepancyCode.BROKER_ORDER_ID_MISMATCH)

        order_fills = sorted(
            (fill for fill in broker_fills if fill.broker_order_id == broker.broker_order_id),
            key=lambda item: (ensure_utc(item.filled_at), item.fill_id),
        )
        orphan_fills = [
            fill for fill in broker_fills if fill.broker_order_id != broker.broker_order_id
        ]
        if orphan_fills:
            codes.append(DiscrepancyCode.UNKNOWN_FILL)

        known = set(local.known_fill_ids)
        new_fills: list[BrokerFill] = []
        duplicate_ids: list[str] = []
        for fill in order_fills:
            if fill.fill_id in known:
                duplicate_ids.append(fill.fill_id)
            else:
                new_fills.append(fill)

        broker_filled_qty = canonicalize_decimal(
            sum((parse_decimal(fill.quantity) for fill in order_fills), ZERO)
        )
        known_fill_qty = canonicalize_decimal(
            sum(
                (parse_decimal(fill.quantity) for fill in order_fills if fill.fill_id in known),
                ZERO,
            )
        )
        local_filled = canonicalize_decimal(parse_decimal(local.filled_quantity))
        expected_qty = canonicalize_decimal(parse_decimal(local.expected_quantity))

        if broker_filled_qty > expected_qty:
            codes.append(DiscrepancyCode.QUANTITY_MISMATCH)
        if broker.status is BrokerOrderStatus.FILLED and broker_filled_qty < expected_qty:
            codes.append(DiscrepancyCode.QUANTITY_MISMATCH)
        # Local filled_quantity must agree with quantities of known (already-seen) fills,
        # even when unseen broker fills are also present — otherwise inconsistent books
        # are treated as MATCHED and new fills can double-apply.
        if local_filled != known_fill_qty:
            codes.append(DiscrepancyCode.FILL_QUANTITY_MISMATCH)

        limit = canonicalize_decimal(parse_decimal(local.expected_limit_price))
        for fill in order_fills:
            px = canonicalize_decimal(parse_decimal(fill.price))
            if local.side is OrderSide.BUY and px > limit:
                codes.append(DiscrepancyCode.PRICE_MISMATCH)
                break
            if local.side is OrderSide.SELL and px < limit:
                codes.append(DiscrepancyCode.PRICE_MISMATCH)
                break

        unique_codes = _unique(codes)
        hard = {
            DiscrepancyCode.QUANTITY_MISMATCH,
            DiscrepancyCode.PRICE_MISMATCH,
            DiscrepancyCode.STATUS_MISMATCH,
            DiscrepancyCode.CLIENT_ORDER_ID_MISMATCH,
            DiscrepancyCode.BROKER_ORDER_ID_MISMATCH,
            DiscrepancyCode.SIDE_MISMATCH,
            DiscrepancyCode.FILL_QUANTITY_MISMATCH,
            DiscrepancyCode.UNKNOWN_FILL,
        }
        if hard.intersection(unique_codes):
            return self._manual(
                local=local,
                observed=observed,
                reconciliation_id=reconciliation_id,
                codes=tuple(unique_codes),
                reason_code=unique_codes[0].value,
                message="order/fill discrepancy requires manual review",
                broker_order_id=broker.broker_order_id,
                broker_filled_quantity=broker_filled_qty,
                new_fill_ids=tuple(fill.fill_id for fill in new_fills),
                duplicate_fill_ids=tuple(duplicate_ids),
            )

        applied_new: list[str] = []
        if ledger is not None and new_fills:
            batch = [
                (fill, local.side, local.instrument, fees.get(fill.fill_id, ZERO))
                for fill in new_fills
            ]
            apply_results = ledger.apply_fills_atomic(batch)
            rejected = next(
                (item for item in apply_results if item.status is LedgerApplyStatus.REJECTED),
                None,
            )
            if rejected is not None:
                return self._manual(
                    local=local,
                    observed=observed,
                    reconciliation_id=reconciliation_id,
                    codes=(DiscrepancyCode.FILL_QUANTITY_MISMATCH,),
                    reason_code=rejected.reason_code or "LEDGER_REJECTED",
                    message=(
                        "matched fill could not be applied to ledger; refusing silent rewrite"
                    ),
                    broker_order_id=broker.broker_order_id,
                    broker_filled_quantity=broker_filled_qty,
                    new_fill_ids=tuple(item.fill_id for item in new_fills),
                    duplicate_fill_ids=tuple(duplicate_ids),
                )
            applied_new = [
                item.fill_id for item in apply_results if item.status is LedgerApplyStatus.APPLIED
            ]

        return self._report(
            local=local,
            observed=observed,
            reconciliation_id=reconciliation_id,
            outcome=ReconciliationOutcome.MATCHED,
            reason_code=DiscrepancyCode.MATCHED.value,
            codes=(DiscrepancyCode.MATCHED,),
            broker_order_id=broker.broker_order_id,
            broker_filled_quantity=broker_filled_qty,
            new_fill_ids=tuple(
                applied_new if ledger is not None else (f.fill_id for f in new_fills)
            ),
            duplicate_fill_ids=tuple(duplicate_ids),
            requires_manual_review=False,
        )

    def find_unknown_broker_orders(
        self,
        *,
        locals_: Sequence[LocalOrderExpectation],
        broker_orders: Sequence[BrokerOrder],
        observed_at: datetime,
    ) -> tuple[OrderReconciliationReport, ...]:
        """Emit MANUAL_REVIEW for broker orders with no local client_order_id."""
        observed = ensure_utc(observed_at)
        known_clients = {item.client_order_id for item in locals_}
        known_brokers = {
            item.broker_order_id for item in locals_ if item.broker_order_id is not None
        }
        reports: list[OrderReconciliationReport] = []
        for index, order in enumerate(broker_orders):
            if order.client_order_id in known_clients or order.broker_order_id in known_brokers:
                continue
            placeholder = LocalOrderExpectation(
                client_order_id=order.client_order_id,
                proposal_id=order.proposal_id,
                side=order.side,
                expected_quantity=Decimal("1"),
                expected_limit_price=Decimal("1"),
                instrument=InstrumentIdentity(
                    instrument_id="unknown_instrument",
                    symbol="ZZZZ",
                    exchange="XNAS",
                    currency="USD",
                    asset_type=AssetType.EQUITY,
                    identity_as_of=observed,
                ),
                local_lifecycle=OrderLifecycleState.RECONCILING,
                broker_order_id=order.broker_order_id,
            )
            # StableId: recon_<suffix>
            suffix = "".join(ch for ch in order.broker_order_id if ch.isalnum())[-12:]
            recon_id = f"recon_{suffix}_{index:04d}"
            reports.append(
                self._manual(
                    local=placeholder,
                    observed=observed,
                    reconciliation_id=recon_id,
                    codes=(DiscrepancyCode.UNKNOWN_BROKER_ORDER,),
                    reason_code="UNKNOWN_BROKER_ORDER",
                    message="broker order has no matching local client_order_id",
                    broker_order_id=order.broker_order_id,
                )
            )
        return tuple(reports)

    def _manual(
        self,
        *,
        local: LocalOrderExpectation,
        observed: datetime,
        reconciliation_id: StableId,
        codes: tuple[DiscrepancyCode, ...],
        reason_code: str,
        message: str,
        broker_order_id: str | None,
        broker_filled_quantity: Decimal | None = None,
        new_fill_ids: tuple[str, ...] = (),
        duplicate_fill_ids: tuple[str, ...] = (),
    ) -> OrderReconciliationReport:
        self._emit_alert(
            severity=AlertSeverity.CRITICAL,
            reason_code=reason_code,
            message=message,
            occurred_at=observed,
            proposal_id=local.proposal_id,
            client_order_id=local.client_order_id,
            broker_order_id=broker_order_id,
            details=tuple(code.value for code in codes),
        )
        return self._report(
            local=local,
            observed=observed,
            reconciliation_id=reconciliation_id,
            outcome=ReconciliationOutcome.MANUAL_REVIEW,
            reason_code=reason_code,
            codes=codes,
            broker_order_id=broker_order_id,
            broker_filled_quantity=broker_filled_quantity,
            new_fill_ids=new_fill_ids,
            duplicate_fill_ids=duplicate_fill_ids,
            requires_manual_review=True,
        )

    def _report(
        self,
        *,
        local: LocalOrderExpectation,
        observed: datetime,
        reconciliation_id: StableId,
        outcome: ReconciliationOutcome,
        reason_code: str,
        codes: tuple[DiscrepancyCode, ...],
        broker_order_id: str | None,
        broker_filled_quantity: Decimal | None = None,
        new_fill_ids: tuple[str, ...] = (),
        duplicate_fill_ids: tuple[str, ...] = (),
        requires_manual_review: bool,
    ) -> OrderReconciliationReport:
        result = ReconciliationResult(
            reconciliation_id=reconciliation_id,
            proposal_id=local.proposal_id,
            broker_order_id=broker_order_id,
            cancel_id=None,
            outcome=outcome,
            reason_code=reason_code,
            observed_at=observed,
        )
        return OrderReconciliationReport(
            outcome=outcome,
            reason_code=reason_code,
            proposal_id=local.proposal_id,
            client_order_id=local.client_order_id,
            broker_order_id=broker_order_id,
            observed_at=observed,
            discrepancy_codes=codes,
            broker_filled_quantity=broker_filled_quantity,
            new_fill_ids=new_fill_ids,
            duplicate_fill_ids=duplicate_fill_ids,
            result=result,
            requires_manual_review=requires_manual_review,
        )

    def _emit_alert(
        self,
        *,
        severity: AlertSeverity,
        reason_code: str,
        message: str,
        occurred_at: datetime,
        proposal_id: StableId | None,
        client_order_id: str | None,
        broker_order_id: str | None,
        details: tuple[str, ...],
    ) -> None:
        if self.alert_sink is None:
            return
        self._alert_seq += 1
        self.alert_sink.emit(
            ReconciliationAlert(
                alert_id=f"alrt_{self._alert_seq:08d}_recon",
                severity=severity,
                reason_code=reason_code,
                message=message,
                occurred_at=occurred_at,
                proposal_id=proposal_id,
                client_order_id=client_order_id,
                broker_order_id=broker_order_id,
                details=details,
            )
        )


def apply_matched_fills(
    ledger: PortfolioLedger,
    *,
    fills: Sequence[BrokerFill],
    side: OrderSide,
    instrument: InstrumentIdentity,
    fees: Mapping[str, Decimal] | None = None,
) -> tuple[FillApplyResult, ...]:
    """Apply fills to a ledger in ``(filled_at, fill_id)`` order (idempotent)."""
    fee_map = fees or {}
    payload = [(fill, side, instrument, fee_map.get(fill.fill_id, ZERO)) for fill in fills]
    return ledger.apply_fills_sorted(payload)


def _unique(codes: Sequence[DiscrepancyCode]) -> list[DiscrepancyCode]:
    seen: set[DiscrepancyCode] = set()
    out: list[DiscrepancyCode] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


__all__ = [
    "AlertSeverity",
    "AlertSink",
    "DiscrepancyCode",
    "InMemoryAlertSink",
    "LocalOrderExpectation",
    "OrderReconciler",
    "OrderReconciliationReport",
    "ReconciliationAlert",
    "apply_matched_fills",
    "expected_broker_statuses",
]
