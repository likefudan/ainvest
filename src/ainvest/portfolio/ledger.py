"""Append-only portfolio ledger with conservation checks (P03-T15).

The ledger reconstructs cash and positions from opening balances and fills.
It never silently rewrites money facts: applying a fill either succeeds
idempotently (same ``fill_id``) or fails closed. Snapshot assembly and
foundational realized / unrealized P&L live here; broker comparison lives in
``ainvest.execution.reconciliation``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Final

from pydantic import StringConstraints

from ainvest.schemas.broker import BrokerFill
from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    CurrencyCode,
    DomainModel,
    InstrumentIdentity,
    Money,
    OrderSide,
    PnL,
    Provenance,
    SchemaVersion,
    StableId,
    UtcDateTime,
    canonicalize_decimal,
    ensure_utc,
    parse_decimal,
)
from ainvest.schemas.portfolio import (
    WEIGHT_TOLERANCE,
    AccountScope,
    ExposureSnapshot,
    OpenOrderSnapshot,
    PortfolioSnapshot,
    PositionSnapshot,
)

ZERO: Final[Decimal] = Decimal("0")
_MONEY_QUANT: Final[Decimal] = Decimal("0.000001")
_WEIGHT_QUANT: Final[Decimal] = Decimal("0.00000001")
_LEDGER_SOURCE: Final[str] = "ainvest.portfolio.ledger"

FillId = Annotated[str, StringConstraints(min_length=3, max_length=160)]
EntryId = Annotated[str, StringConstraints(min_length=3, max_length=160)]


class LedgerEntryKind(StrEnum):
    """Kinds of append-only ledger postings."""

    OPENING_CASH = "OPENING_CASH"
    FILL_BUY = "FILL_BUY"
    FILL_SELL = "FILL_SELL"


class LedgerApplyStatus(StrEnum):
    """Result of attempting to post a fill."""

    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"


class LedgerError(ValueError):
    """Fail-closed ledger mutation error."""


def _money(value: Decimal) -> Decimal:
    return canonicalize_decimal(Decimal(value).quantize(_MONEY_QUANT))


def _weight(value: Decimal) -> Decimal:
    return canonicalize_decimal(Decimal(value).quantize(_WEIGHT_QUANT))


class LedgerEntry(DomainModel):
    """One immutable cash/position posting."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    entry_id: EntryId
    kind: LedgerEntryKind
    occurred_at: UtcDateTime
    cash_delta: PnL
    quantity_delta: PnL = Decimal("0")
    price: Money | None = None
    fee: Money = Decimal("0")
    fill_id: FillId | None = None
    broker_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)] | None = None
    instrument_id: Annotated[str, StringConstraints(min_length=3, max_length=128)] | None = None
    realized_pnl_delta: PnL = Decimal("0")


class FillApplyResult(DomainModel):
    """Outcome of :meth:`PortfolioLedger.apply_fill`."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    status: LedgerApplyStatus
    fill_id: FillId
    entry_id: EntryId | None = None
    reason_code: Annotated[str, StringConstraints(min_length=2, max_length=64)] | None = None


class ConservationReport(DomainModel):
    """Ledger conservation identity used by tests and operators.

    ``cost_equity`` is cash plus cost basis of open positions. It must equal
    ``opening_cash + realized_pnl - buy_fees`` (no silent rewrites).
    """

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    cash: Money
    cost_basis: Money
    cost_equity: Money
    opening_cash: Money
    realized_pnl: PnL
    expected_cost_equity: Money
    holds: bool


@dataclass
class _Lot:
    instrument: InstrumentIdentity
    quantity: Decimal
    average_cost: Decimal


@dataclass
class PortfolioLedger:
    """Mutable in-process ledger with append-only entry history.

    Not a hidden broker cache: callers must feed fills explicitly. Duplicate
    ``fill_id`` values are no-ops. Out-of-order / late fills are accepted when
    their ``fill_id`` is new; callers that need chronological reconstruction
    should sort before applying.
    """

    account_scope: AccountScope
    currency: CurrencyCode
    opening_cash: Decimal
    as_of: datetime
    _cash: Decimal = field(init=False)
    _lots: dict[str, _Lot] = field(default_factory=dict, init=False)
    _entries: list[LedgerEntry] = field(default_factory=list, init=False)
    _seen_fills: dict[str, str] = field(default_factory=dict, init=False)  # fill_id -> entry_id
    _realized_pnl: Decimal = field(default_factory=lambda: ZERO, init=False)
    _entry_seq: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.opening_cash = _money(parse_decimal(self.opening_cash))
        if self.opening_cash < ZERO:
            raise LedgerError("opening_cash must be >= 0")
        self.as_of = ensure_utc(self.as_of)
        self._cash = self.opening_cash
        self._entry_seq += 1
        self._entries.append(
            LedgerEntry(
                entry_id=f"led_{self._entry_seq:08d}",
                kind=LedgerEntryKind.OPENING_CASH,
                occurred_at=self.as_of,
                cash_delta=self.opening_cash,
            )
        )

    @property
    def cash(self) -> Decimal:
        return self._cash

    @property
    def realized_pnl(self) -> Decimal:
        return self._realized_pnl

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    @property
    def applied_fill_ids(self) -> frozenset[str]:
        return frozenset(self._seen_fills)

    def position_quantity(self, instrument_id: str) -> Decimal:
        lot = self._lots.get(instrument_id)
        return lot.quantity if lot is not None else ZERO

    def positions(self) -> Mapping[str, tuple[InstrumentIdentity, Decimal, Decimal]]:
        """Return ``instrument_id -> (identity, qty, average_cost)``."""
        return {
            key: (lot.instrument, lot.quantity, lot.average_cost)
            for key, lot in sorted(self._lots.items())
        }

    def apply_fill(
        self,
        fill: BrokerFill,
        *,
        side: OrderSide,
        instrument: InstrumentIdentity,
        fee: Decimal | str | int = ZERO,
    ) -> FillApplyResult:
        """Post a broker fill. Duplicate ``fill_id`` returns DUPLICATE (no mutation)."""
        fill_id = fill.fill_id
        prior = self._seen_fills.get(fill_id)
        if prior is not None:
            return FillApplyResult(
                status=LedgerApplyStatus.DUPLICATE,
                fill_id=fill_id,
                entry_id=prior,
                reason_code="DUPLICATE_FILL",
            )

        if instrument.instrument_id.strip() == "":
            raise LedgerError("instrument_id must be non-empty")
        if instrument.currency != self.currency:
            return FillApplyResult(
                status=LedgerApplyStatus.REJECTED,
                fill_id=fill_id,
                reason_code="CURRENCY_MISMATCH",
            )

        qty = canonicalize_decimal(parse_decimal(fill.quantity))
        price = _money(parse_decimal(fill.price))
        fee_amt = _money(parse_decimal(fee))
        if qty <= ZERO:
            return FillApplyResult(
                status=LedgerApplyStatus.REJECTED,
                fill_id=fill_id,
                reason_code="NON_POSITIVE_QUANTITY",
            )
        if fee_amt < ZERO:
            return FillApplyResult(
                status=LedgerApplyStatus.REJECTED,
                fill_id=fill_id,
                reason_code="NEGATIVE_FEE",
            )

        notional = _money(qty * price)
        occurred = ensure_utc(fill.filled_at)
        realized_delta = ZERO

        if side is OrderSide.BUY:
            debit = _money(notional + fee_amt)
            if debit > self._cash:
                return FillApplyResult(
                    status=LedgerApplyStatus.REJECTED,
                    fill_id=fill_id,
                    reason_code="INSUFFICIENT_CASH",
                )
            self._cash = canonicalize_decimal(self._cash - debit)
            self._credit(instrument, quantity=qty, price=price)
            kind = LedgerEntryKind.FILL_BUY
            cash_delta = -debit
            qty_delta = qty
        else:
            proceeds = _money(notional - fee_amt)
            if proceeds < ZERO:
                return FillApplyResult(
                    status=LedgerApplyStatus.REJECTED,
                    fill_id=fill_id,
                    reason_code="FEE_EXCEEDS_PROCEEDS",
                )
            held = self.position_quantity(instrument.instrument_id)
            if qty > held:
                return FillApplyResult(
                    status=LedgerApplyStatus.REJECTED,
                    fill_id=fill_id,
                    reason_code="INSUFFICIENT_POSITION",
                )
            lot = self._lots[instrument.instrument_id]
            realized_delta = _money((price - lot.average_cost) * qty - fee_amt)
            self._debit(instrument.instrument_id, quantity=qty)
            self._cash = canonicalize_decimal(self._cash + proceeds)
            self._realized_pnl = canonicalize_decimal(self._realized_pnl + realized_delta)
            kind = LedgerEntryKind.FILL_SELL
            cash_delta = proceeds
            qty_delta = -qty

        self._entry_seq += 1
        entry_id = f"led_{self._entry_seq:08d}"
        entry = LedgerEntry(
            entry_id=entry_id,
            kind=kind,
            occurred_at=occurred,
            cash_delta=cash_delta,
            quantity_delta=qty_delta,
            price=price,
            fee=fee_amt,
            fill_id=fill_id,
            broker_order_id=fill.broker_order_id,
            instrument_id=instrument.instrument_id,
            realized_pnl_delta=realized_delta,
        )
        self._entries.append(entry)
        self._seen_fills[fill_id] = entry_id
        if occurred > self.as_of:
            self.as_of = occurred
        return FillApplyResult(status=LedgerApplyStatus.APPLIED, fill_id=fill_id, entry_id=entry_id)

    def apply_fills_sorted(
        self,
        fills: Sequence[tuple[BrokerFill, OrderSide, InstrumentIdentity, Decimal]],
    ) -> tuple[FillApplyResult, ...]:
        """Apply fills sorted by ``(filled_at, fill_id)`` for stable late/out-of-order ingest."""
        ordered = sorted(fills, key=lambda item: (ensure_utc(item[0].filled_at), item[0].fill_id))
        return tuple(
            self.apply_fill(fill, side=side, instrument=instrument, fee=fee)
            for fill, side, instrument, fee in ordered
        )

    def cost_basis(self) -> Decimal:
        total = ZERO
        for lot in self._lots.values():
            total = canonicalize_decimal(total + _money(lot.quantity * lot.average_cost))
        return total

    def conservation(self) -> ConservationReport:
        """Verify cost-equity conservation including buy fees.

        Buy fees leave cash without increasing cost basis or realized P&L, so::

            cash + cost_basis == opening_cash + realized_pnl - buy_fees
        """
        cash = self._cash
        basis = self.cost_basis()
        cost_equity = canonicalize_decimal(cash + basis)
        buy_fees = sum(
            (entry.fee for entry in self._entries if entry.kind is LedgerEntryKind.FILL_BUY),
            ZERO,
        )
        expected = canonicalize_decimal(self.opening_cash + self._realized_pnl - buy_fees)
        return ConservationReport(
            cash=cash,
            cost_basis=basis,
            cost_equity=cost_equity,
            opening_cash=self.opening_cash,
            realized_pnl=self._realized_pnl,
            expected_cost_equity=expected,
            holds=cost_equity == expected,
        )

    def assert_conservation(self) -> ConservationReport:
        report = self.conservation()
        if not report.holds:
            raise LedgerError(
                "ledger conservation violated: "
                f"cost_equity={report.cost_equity} expected={report.expected_cost_equity}"
            )
        return report

    def build_snapshot(
        self,
        *,
        snapshot_id: StableId,
        as_of: datetime | None = None,
        marks: Mapping[str, Decimal] | None = None,
        open_orders: Sequence[OpenOrderSnapshot] = (),
        buying_power: Decimal | None = None,
    ) -> PortfolioSnapshot:
        """Assemble a :class:`PortfolioSnapshot` with foundational unrealized P&L."""
        moment = ensure_utc(as_of or self.as_of)
        mark_map = {key: _money(parse_decimal(value)) for key, value in (marks or {}).items()}
        position_snaps: list[PositionSnapshot] = []
        gross = ZERO
        for instrument_id, lot in sorted(self._lots.items()):
            mark = mark_map.get(instrument_id, lot.average_cost)
            mv = _money(lot.quantity * mark)
            gross = canonicalize_decimal(gross + mv)
            instrument = lot.instrument
            if instrument.identity_as_of > moment:
                instrument = instrument.model_copy(update={"identity_as_of": moment})
            position_snaps.append(
                PositionSnapshot(
                    instrument=instrument,
                    quantity=lot.quantity,
                    market_value=mv,
                    portfolio_weight=ZERO,
                    average_cost=lot.average_cost,
                    unrealized_pnl=_money((mark - lot.average_cost) * lot.quantity),
                    currency=instrument.currency,
                )
            )

        cash = self._cash
        equity = canonicalize_decimal(cash + gross)
        bp = cash if buying_power is None else _money(parse_decimal(buying_power))
        if self.account_scope is AccountScope.PAPER and bp > equity:
            bp = equity

        weighted: list[PositionSnapshot] = []
        for pos in position_snaps:
            weight = ZERO if equity == ZERO else _weight(pos.market_value / equity)
            weighted.append(pos.model_copy(update={"portfolio_weight": weight}))

        largest = ZERO
        if weighted:
            largest = max(p.portfolio_weight for p in weighted)
            # Snap within schema tolerance so float-free Decimal weights validate.
            if abs(largest - max(p.portfolio_weight for p in weighted)) > WEIGHT_TOLERANCE:
                largest = max(p.portfolio_weight for p in weighted)

        return PortfolioSnapshot(
            snapshot_id=snapshot_id,
            account_scope=self.account_scope,
            as_of=moment,
            currency=self.currency,
            cash=cash,
            buying_power=bp,
            equity=equity,
            positions=tuple(weighted),
            open_orders=tuple(open_orders),
            exposure=ExposureSnapshot(
                cash=cash,
                equity=equity,
                gross_market_value=gross,
                net_market_value=gross,
                largest_position_weight=largest,
                position_count=len(weighted),
            ),
            provenance=Provenance(
                source=_LEDGER_SOURCE,
                observed_at=moment,
                received_at=moment,
                timezone="UTC",
                is_delayed=False,
                quality_flags=(),
            ),
        )

    def _credit(self, instrument: InstrumentIdentity, *, quantity: Decimal, price: Decimal) -> None:
        lot = self._lots.get(instrument.instrument_id)
        if lot is None:
            self._lots[instrument.instrument_id] = _Lot(
                instrument=instrument,
                quantity=quantity,
                average_cost=price,
            )
            return
        new_qty = canonicalize_decimal(lot.quantity + quantity)
        if new_qty == ZERO:
            self._lots.pop(instrument.instrument_id, None)
            return
        new_cost = _money((lot.average_cost * lot.quantity + price * quantity) / new_qty)
        lot.quantity = new_qty
        lot.average_cost = new_cost
        lot.instrument = instrument

    def _debit(self, instrument_id: str, *, quantity: Decimal) -> None:
        lot = self._lots[instrument_id]
        new_qty = canonicalize_decimal(lot.quantity - quantity)
        if new_qty < ZERO:
            raise LedgerError("position went negative")
        if new_qty == ZERO:
            self._lots.pop(instrument_id, None)
        else:
            lot.quantity = new_qty


__all__ = [
    "ConservationReport",
    "FillApplyResult",
    "LedgerApplyStatus",
    "LedgerEntry",
    "LedgerEntryKind",
    "LedgerError",
    "PortfolioLedger",
]
