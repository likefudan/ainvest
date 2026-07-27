"""Deterministic Paper Broker and fill simulator (P03-T14).

No real money. Fills occur only from **injected** market events. Clocks and
randomness are caller-injected (never ``datetime.now`` / global ``random``).
Fees, half-spread, and slippage are explicit cost-model inputs — never assumed
zero by omission.

Implements :class:`~ainvest.execution.broker.BrokerReadPort` and
:class:`~ainvest.execution.broker.BrokerWritePort` for ``account_scope=paper``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from random import Random
from typing import Final, Protocol

from pydantic import model_validator

from ainvest.execution.broker import (
    BrokerInvalidOrderError,
    BrokerReadPort,
    BrokerRejectedError,
    BrokerSubmitOutcome,
    BrokerSubmitRequest,
    BrokerSubmitResult,
    BrokerWritePort,
    assert_no_replace_operation,
    assert_read_port_has_no_write_methods,
)
from ainvest.schemas.broker import (
    BrokerFill,
    BrokerOrder,
    BrokerOrderStatus,
    CancelCommand,
    CancelResult,
    CancelStatus,
)
from ainvest.schemas.common import (
    AssetType,
    DomainModel,
    InstrumentIdentity,
    NonNegativeDecimal,
    OrderSide,
    PositiveDecimal,
    Price,
    Provenance,
    UtcDateTime,
    canonicalize_decimal,
    ensure_utc,
)
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.orders import OrderProposal
from ainvest.schemas.portfolio import (
    AccountScope,
    ExposureSnapshot,
    OpenOrderSnapshot,
    PortfolioSnapshot,
    PositionSnapshot,
)

ZERO: Final[Decimal] = Decimal("0")
BPS_DENOM: Final[Decimal] = Decimal("10000")
_PAPER_SOURCE: Final[str] = "ainvest.paper.broker"
_WEIGHT_QUANT: Final[Decimal] = Decimal("0.00000001")
_MONEY_QUANT: Final[Decimal] = Decimal("0.000001")


class PaperRejectReason(StrEnum):
    """Stable machine codes for paper submit / cancel rejections."""

    ACCOUNT_SCOPE_NOT_PAPER = "ACCOUNT_SCOPE_NOT_PAPER"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    INSUFFICIENT_POSITION = "INSUFFICIENT_POSITION"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    ORDER_NOT_CANCELABLE = "ORDER_NOT_CANCELABLE"
    ORDER_EXPIRED = "ORDER_EXPIRED"
    INVALID_MARKET_EVENT = "INVALID_MARKET_EVENT"


class PaperClock(Protocol):
    """Injected clock; implementations must be deterministic under test."""

    def __call__(self) -> datetime: ...


class PaperCostModel(DomainModel):
    """Explicit trading-cost parameters (never omitted as implicit zero).

    All three fields are required. Callers that intentionally want zero cost
    must pass ``Decimal(\"0\")`` explicitly for each component.
    """

    fee_bps: NonNegativeDecimal
    half_spread_bps: NonNegativeDecimal
    slippage_bps: NonNegativeDecimal


class PaperMarketEvent(DomainModel):
    """Injected market observation used to drive limit fills.

    ``liquidity`` is the maximum share quantity this event may fill against
    working orders (FIFO by submission time).
    """

    event_id: str
    instrument_id: str
    bid: Price
    ask: Price
    last: Price
    liquidity: PositiveDecimal
    observed_at: UtcDateTime

    @model_validator(mode="after")
    def _quote_invariants(self) -> PaperMarketEvent:
        if self.bid <= ZERO or self.ask <= ZERO or self.last <= ZERO:
            raise ValueError("bid, ask, and last must be > 0")
        if self.bid > self.ask:
            raise ValueError("bid must be <= ask")
        return self


@dataclass(frozen=True)
class _SubmitFingerprint:
    proposal_id: str
    order_hash: str
    side: OrderSide
    quantity: Decimal
    limit_price: Decimal
    instrument_id: str


@dataclass
class _WorkingOrder:
    broker_order: BrokerOrder
    proposal: OrderProposal
    remaining: Decimal
    reserved_cash: Decimal
    reserved_qty: Decimal
    filled_qty: Decimal = ZERO
    fills: list[BrokerFill] = field(default_factory=list)


@dataclass
class _PositionBook:
    instrument: InstrumentIdentity
    quantity: Decimal
    average_cost: Decimal


def _money(value: Decimal) -> Decimal:
    quantized = Decimal(value).quantize(_MONEY_QUANT)
    return canonicalize_decimal(quantized)


def _weight(value: Decimal) -> Decimal:
    quantized = Decimal(value).quantize(_WEIGHT_QUANT)
    return canonicalize_decimal(quantized)


def _require_utc(moment: datetime) -> datetime:
    return ensure_utc(moment)


def _adverse_fill_price(
    *,
    side: OrderSide,
    bid: Decimal,
    ask: Decimal,
    limit_price: Decimal,
    costs: PaperCostModel,
) -> Decimal:
    """Compute fill price from mid + explicit half-spread + slippage.

    BUY pays above mid; SELL receives below mid. The result is clipped to the
    limit so the fill never violates the limit order.
    """
    mid = _money((bid + ask) / Decimal("2"))
    adverse_bps = costs.half_spread_bps + costs.slippage_bps
    adverse_frac = adverse_bps / BPS_DENOM
    if side is OrderSide.BUY:
        raw = mid * (Decimal("1") + adverse_frac)
        # Never pay above the limit on a limit buy.
        return _money(min(raw, limit_price))
    raw = mid * (Decimal("1") - adverse_frac)
    # Never sell below the limit on a limit sell.
    return _money(max(raw, limit_price))


def _fee_for(*, notional: Decimal, costs: PaperCostModel) -> Decimal:
    return _money(notional * costs.fee_bps / BPS_DENOM)


def _fingerprint(proposal: OrderProposal) -> _SubmitFingerprint:
    return _SubmitFingerprint(
        proposal_id=proposal.proposal_id,
        order_hash=proposal.order_hash,
        side=proposal.side,
        quantity=_money(proposal.quantity),
        limit_price=_money(proposal.limit_price),
        instrument_id=proposal.instrument_id,
    )


def _broker_order_id(client_order_id: str) -> str:
    return f"paper_{client_order_id}"


def _fill_id(*, broker_order_id: str, event_id: str, seq: int) -> str:
    return f"paper_fill_{broker_order_id}_{event_id}_{seq}"


class PaperBroker:
    """In-memory paper account implementing broker read + write ports.

    Construction requires an explicit :class:`PaperCostModel` and an injected
    :class:`PaperClock`. An optional :class:`~random.Random` may be supplied for
    stochastic partial-fill sizing; when omitted, fills consume liquidity in
    FIFO order up to remaining quantity (fully deterministic).
    """

    def __init__(
        self,
        *,
        cost_model: PaperCostModel,
        clock: PaperClock,
        initial_cash: Decimal | str | int,
        rng: Random | None = None,
        snapshot_id_prefix: str = "paper_snap",
    ) -> None:
        self._costs = cost_model
        self._clock = clock
        self._rng = rng
        self._cash = _money(Decimal(str(initial_cash)))
        if self._cash < ZERO:
            raise ValueError("initial_cash must be >= 0")
        self._snapshot_id_prefix = snapshot_id_prefix
        self._snapshot_seq = 0
        self._ledger_as_of = _require_utc(clock())
        self._positions: dict[str, _PositionBook] = {}
        self._orders: dict[str, _WorkingOrder] = {}  # broker_order_id
        self._by_client: dict[str, str] = {}  # client_order_id -> broker_order_id
        self._submit_fingerprints: dict[str, _SubmitFingerprint] = {}
        self._submit_results: dict[str, BrokerSubmitResult] = {}
        self._cancel_results: dict[str, CancelResult] = {}
        self._quotes: dict[str, MarketQuote] = {}
        self._all_fills: list[BrokerFill] = []
        self._event_seq: dict[str, int] = {}  # broker_order_id -> fill sequence
        assert_no_replace_operation(self)

    # ------------------------------------------------------------------
    # Read port
    # ------------------------------------------------------------------

    def get_account(self, account_scope: AccountScope) -> PortfolioSnapshot:
        self._require_paper_scope(account_scope)
        return self._build_snapshot()

    def get_positions(self, account_scope: AccountScope) -> tuple[PositionSnapshot, ...]:
        return self.get_account(account_scope).positions

    def get_quotes(self, instrument_ids: tuple[str, ...]) -> tuple[MarketQuote, ...]:
        if not instrument_ids:
            return tuple(self._quotes[k] for k in sorted(self._quotes))
        out: list[MarketQuote] = []
        for instrument_id in instrument_ids:
            quote = self._quotes.get(instrument_id)
            if quote is not None:
                out.append(quote)
        return tuple(out)

    def get_orders(
        self,
        account_scope: AccountScope,
        *,
        broker_order_ids: tuple[str, ...] | None = None,
        client_order_ids: tuple[str, ...] | None = None,
    ) -> tuple[BrokerOrder, ...]:
        self._require_paper_scope(account_scope)
        orders = [wo.broker_order for wo in self._orders.values()]
        if broker_order_ids is not None:
            wanted = set(broker_order_ids)
            orders = [o for o in orders if o.broker_order_id in wanted]
        if client_order_ids is not None:
            wanted_c = set(client_order_ids)
            orders = [o for o in orders if o.client_order_id in wanted_c]
        orders.sort(key=lambda o: (o.submitted_at, o.broker_order_id))
        return tuple(orders)

    def get_fills(
        self,
        account_scope: AccountScope,
        *,
        broker_order_ids: tuple[str, ...] | None = None,
    ) -> tuple[BrokerFill, ...]:
        self._require_paper_scope(account_scope)
        fills = list(self._all_fills)
        if broker_order_ids is not None:
            wanted = set(broker_order_ids)
            fills = [f for f in fills if f.broker_order_id in wanted]
        fills.sort(key=lambda f: (f.filled_at, f.fill_id))
        return tuple(fills)

    # ------------------------------------------------------------------
    # Write port
    # ------------------------------------------------------------------

    def submit(self, request: BrokerSubmitRequest) -> BrokerSubmitResult:
        proposal = request.proposal
        client_order_id = request.client_order_id
        now = self._now()

        if proposal.account_scope is not AccountScope.PAPER:
            return BrokerSubmitResult(
                outcome=BrokerSubmitOutcome.REJECTED,
                client_order_id=client_order_id,
                observed_at=now,
                reason_code=PaperRejectReason.ACCOUNT_SCOPE_NOT_PAPER.value,
            )

        fp = _fingerprint(proposal)
        prior = self._submit_results.get(client_order_id)
        if prior is not None:
            existing_fp = self._submit_fingerprints[client_order_id]
            if existing_fp != fp:
                raise BrokerInvalidOrderError(
                    "idempotency key reused with different order fields",
                    reason_code=PaperRejectReason.IDEMPOTENCY_CONFLICT.value,
                    details={"client_order_id": client_order_id},
                )
            # Identical submit: return the prior result (no double-charge).
            return prior

        if now >= _require_utc(proposal.expires_at):
            result = BrokerSubmitResult(
                outcome=BrokerSubmitOutcome.REJECTED,
                client_order_id=client_order_id,
                observed_at=now,
                reason_code=PaperRejectReason.ORDER_EXPIRED.value,
            )
            self._remember_submit(client_order_id, fp, result)
            return result

        qty = _money(proposal.quantity)
        limit = _money(proposal.limit_price)
        max_fee = _fee_for(notional=qty * limit, costs=self._costs)

        if proposal.side is OrderSide.BUY:
            need = canonicalize_decimal(qty * limit + max_fee)
            available = self._available_cash()
            if need > available:
                result = BrokerSubmitResult(
                    outcome=BrokerSubmitOutcome.REJECTED,
                    client_order_id=client_order_id,
                    observed_at=now,
                    reason_code=PaperRejectReason.INSUFFICIENT_CASH.value,
                )
                self._remember_submit(client_order_id, fp, result)
                return result
            reserved_cash = need
            reserved_qty = ZERO
        else:
            held = self._position_qty(proposal.instrument_id)
            reserved_sells = self._reserved_sell_qty(proposal.instrument_id)
            if qty > held - reserved_sells:
                result = BrokerSubmitResult(
                    outcome=BrokerSubmitOutcome.REJECTED,
                    client_order_id=client_order_id,
                    observed_at=now,
                    reason_code=PaperRejectReason.INSUFFICIENT_POSITION.value,
                )
                self._remember_submit(client_order_id, fp, result)
                return result
            reserved_cash = ZERO
            reserved_qty = qty

        broker_order_id = _broker_order_id(client_order_id)
        broker_order = BrokerOrder(
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            proposal_id=proposal.proposal_id,
            order_hash=proposal.order_hash,
            account_scope=AccountScope.PAPER,
            side=proposal.side,
            status=BrokerOrderStatus.ACCEPTED,
            submitted_at=now,
            updated_at=now,
        )
        working = _WorkingOrder(
            broker_order=broker_order,
            proposal=proposal,
            remaining=qty,
            reserved_cash=reserved_cash,
            reserved_qty=reserved_qty,
        )
        self._orders[broker_order_id] = working
        self._by_client[client_order_id] = broker_order_id
        result = BrokerSubmitResult(
            outcome=BrokerSubmitOutcome.ACCEPTED,
            client_order_id=client_order_id,
            observed_at=now,
            broker_order=broker_order,
        )
        self._remember_submit(client_order_id, fp, result)
        return result

    def cancel(self, command: CancelCommand) -> CancelResult:
        now = self._now()
        prior = self._cancel_results.get(command.idempotency_key)
        if prior is not None:
            if (
                prior.cancel_id != command.cancel_id
                or prior.broker_order_id != command.broker_order_id
            ):
                raise BrokerInvalidOrderError(
                    "cancel idempotency key reused with different cancel fields",
                    reason_code=PaperRejectReason.IDEMPOTENCY_CONFLICT.value,
                    details={"idempotency_key": command.idempotency_key},
                )
            return prior

        if command.account_scope is not AccountScope.PAPER:
            result = CancelResult(
                cancel_id=command.cancel_id,
                broker_order_id=command.broker_order_id,
                status=CancelStatus.REJECTED,
                reason_code=PaperRejectReason.ACCOUNT_SCOPE_NOT_PAPER.value,
                observed_at=now,
            )
            self._cancel_results[command.idempotency_key] = result
            return result

        working = self._orders.get(command.broker_order_id)
        if working is None:
            result = CancelResult(
                cancel_id=command.cancel_id,
                broker_order_id=command.broker_order_id,
                status=CancelStatus.REJECTED,
                reason_code=PaperRejectReason.ORDER_NOT_FOUND.value,
                observed_at=now,
            )
            self._cancel_results[command.idempotency_key] = result
            return result

        status = working.broker_order.status
        if status not in {BrokerOrderStatus.ACCEPTED, BrokerOrderStatus.PARTIALLY_FILLED}:
            result = CancelResult(
                cancel_id=command.cancel_id,
                broker_order_id=command.broker_order_id,
                status=CancelStatus.REJECTED,
                reason_code=PaperRejectReason.ORDER_NOT_CANCELABLE.value,
                observed_at=now,
            )
            self._cancel_results[command.idempotency_key] = result
            return result

        if (
            command.order_hash != working.proposal.order_hash
            or command.proposal_id != working.proposal.proposal_id
        ):
            result = CancelResult(
                cancel_id=command.cancel_id,
                broker_order_id=command.broker_order_id,
                status=CancelStatus.REJECTED,
                reason_code=PaperRejectReason.IDEMPOTENCY_CONFLICT.value,
                observed_at=now,
            )
            self._cancel_results[command.idempotency_key] = result
            return result

        self._release_reserves(working)
        working.reserved_cash = ZERO
        working.reserved_qty = ZERO
        working.remaining = ZERO
        working.broker_order = working.broker_order.model_copy(
            update={"status": BrokerOrderStatus.CANCELLED, "updated_at": now}
        )
        result = CancelResult(
            cancel_id=command.cancel_id,
            broker_order_id=command.broker_order_id,
            status=CancelStatus.CONFIRMED,
            reason_code=command.reason_code,
            observed_at=now,
        )
        self._cancel_results[command.idempotency_key] = result
        return result

    # ------------------------------------------------------------------
    # Market-event injection (fill path)
    # ------------------------------------------------------------------

    def inject_market_event(self, event: PaperMarketEvent) -> tuple[BrokerFill, ...]:
        """Apply one injected market event; return fills produced (may be empty).

        Fills are produced only here — never on submit. Crossed/invalid quotes
        fail closed via :class:`BrokerInvalidOrderError`.
        """
        try:
            # Re-validate invariants even if constructed via model_validate.
            if event.bid > event.ask:
                raise ValueError("crossed")
            if event.bid <= ZERO or event.ask <= ZERO:
                raise ValueError("non-positive")
        except ValueError as exc:
            raise BrokerInvalidOrderError(
                "invalid paper market event",
                reason_code=PaperRejectReason.INVALID_MARKET_EVENT.value,
                details={"event_id": event.event_id, "error": str(exc)},
            ) from exc

        observed = self._touch(event.observed_at)
        instrument = self._instrument_for_event(event)
        self._quotes[event.instrument_id] = MarketQuote(
            instrument=instrument,
            last_price=event.last,
            bid=event.bid,
            ask=event.ask,
            currency=instrument.currency,
            provenance=Provenance(
                source=_PAPER_SOURCE,
                observed_at=observed,
                received_at=observed,
                timezone="UTC",
                is_delayed=False,
                quality_flags=(),
            ),
        )

        liquidity = _money(event.liquidity)
        produced: list[BrokerFill] = []
        # FIFO by submitted_at then broker_order_id.
        candidates = sorted(
            (
                wo
                for wo in self._orders.values()
                if wo.proposal.instrument_id == event.instrument_id
                and wo.broker_order.status
                in {BrokerOrderStatus.ACCEPTED, BrokerOrderStatus.PARTIALLY_FILLED}
                and wo.remaining > ZERO
            ),
            key=lambda w: (w.broker_order.submitted_at, w.broker_order.broker_order_id),
        )

        for working in candidates:
            if liquidity <= ZERO:
                break
            if not self._is_marketable(working, event):
                continue
            fill_qty = self._choose_fill_qty(working.remaining, liquidity)
            if fill_qty <= ZERO:
                continue
            fill_price = _adverse_fill_price(
                side=working.proposal.side,
                bid=_money(event.bid),
                ask=_money(event.ask),
                limit_price=_money(working.proposal.limit_price),
                costs=self._costs,
            )
            fill = self._apply_fill(
                working,
                quantity=fill_qty,
                price=fill_price,
                event_id=event.event_id,
                filled_at=observed,
                instrument=instrument,
            )
            produced.append(fill)
            liquidity = canonicalize_decimal(liquidity - fill_qty)

        return tuple(produced)

    def inject_market_events(self, events: Sequence[PaperMarketEvent]) -> tuple[BrokerFill, ...]:
        """Apply events in order; concatenate fills."""
        fills: list[BrokerFill] = []
        for event in events:
            fills.extend(self.inject_market_event(event))
        return tuple(fills)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _touch(self, moment: datetime) -> datetime:
        observed = _require_utc(moment)
        if observed > self._ledger_as_of:
            self._ledger_as_of = observed
        return self._ledger_as_of

    def _now(self) -> datetime:
        return self._touch(self._clock())

    def _remember_submit(
        self,
        client_order_id: str,
        fingerprint: _SubmitFingerprint,
        result: BrokerSubmitResult,
    ) -> None:
        self._submit_fingerprints[client_order_id] = fingerprint
        self._submit_results[client_order_id] = result

    def _require_paper_scope(self, account_scope: AccountScope) -> None:
        if account_scope is not AccountScope.PAPER:
            raise BrokerRejectedError(
                "paper broker only serves account_scope=paper",
                reason_code=PaperRejectReason.ACCOUNT_SCOPE_NOT_PAPER.value,
            )

    def _available_cash(self) -> Decimal:
        reserved = sum((wo.reserved_cash for wo in self._orders.values()), ZERO)
        return canonicalize_decimal(self._cash - reserved)

    def _position_qty(self, instrument_id: str) -> Decimal:
        book = self._positions.get(instrument_id)
        return book.quantity if book is not None else ZERO

    def _reserved_sell_qty(self, instrument_id: str) -> Decimal:
        total = ZERO
        for wo in self._orders.values():
            if (
                wo.proposal.instrument_id == instrument_id
                and wo.proposal.side is OrderSide.SELL
                and wo.broker_order.status
                in {BrokerOrderStatus.ACCEPTED, BrokerOrderStatus.PARTIALLY_FILLED}
            ):
                total += wo.reserved_qty
        return canonicalize_decimal(total)

    def _release_reserves(self, working: _WorkingOrder) -> None:
        # Reserves are virtual; releasing is clearing working-order fields.
        # Cash itself is only mutated on fills (BUY debit / SELL credit).
        del working  # reserves cleared by caller assignment

    def _is_marketable(self, working: _WorkingOrder, event: PaperMarketEvent) -> bool:
        limit = _money(working.proposal.limit_price)
        if working.proposal.side is OrderSide.BUY:
            return _money(event.ask) <= limit
        return _money(event.bid) >= limit

    def _choose_fill_qty(self, remaining: Decimal, liquidity: Decimal) -> Decimal:
        cap = min(remaining, liquidity)
        if self._rng is None:
            return canonicalize_decimal(cap)
        # Deterministic given the injected RNG state: uniform integer shares
        # in [1, cap] when cap is a whole number; otherwise take full cap.
        if cap == cap.to_integral_value() and cap >= 1:
            shares = self._rng.randint(1, int(cap))
            return canonicalize_decimal(Decimal(shares))
        return canonicalize_decimal(cap)

    def _apply_fill(
        self,
        working: _WorkingOrder,
        *,
        quantity: Decimal,
        price: Decimal,
        event_id: str,
        filled_at: datetime,
        instrument: InstrumentIdentity,
    ) -> BrokerFill:
        quantity = canonicalize_decimal(quantity)
        price = canonicalize_decimal(price)
        notional = canonicalize_decimal(quantity * price)
        fee = _fee_for(notional=notional, costs=self._costs)
        side = working.proposal.side

        if side is OrderSide.BUY:
            debit = canonicalize_decimal(notional + fee)
            # Reserved cash was sized at limit+max_fee; fill_price <= limit so
            # debit must fit. Fail closed if bookkeeping ever drifts.
            if debit > self._cash:
                raise BrokerRejectedError(
                    "paper fill would overdraft cash",
                    reason_code=PaperRejectReason.INSUFFICIENT_CASH.value,
                )
            self._cash = canonicalize_decimal(self._cash - debit)
            working.reserved_cash = canonicalize_decimal(max(ZERO, working.reserved_cash - debit))
            self._credit_position(instrument, quantity=quantity, price=price)
        else:
            proceeds = canonicalize_decimal(notional - fee)
            if proceeds < ZERO:
                raise BrokerRejectedError(
                    "paper fill fee exceeds sell proceeds",
                    reason_code=PaperRejectReason.INSUFFICIENT_CASH.value,
                )
            held = self._position_qty(instrument.instrument_id)
            if quantity > held:
                raise BrokerRejectedError(
                    "paper fill would oversell position",
                    reason_code=PaperRejectReason.INSUFFICIENT_POSITION.value,
                )
            self._debit_position(instrument.instrument_id, quantity=quantity)
            self._cash = canonicalize_decimal(self._cash + proceeds)
            working.reserved_qty = canonicalize_decimal(max(ZERO, working.reserved_qty - quantity))

        working.remaining = canonicalize_decimal(working.remaining - quantity)
        working.filled_qty = canonicalize_decimal(working.filled_qty + quantity)

        seq = self._event_seq.get(working.broker_order.broker_order_id, 0) + 1
        self._event_seq[working.broker_order.broker_order_id] = seq
        fill = BrokerFill(
            fill_id=_fill_id(
                broker_order_id=working.broker_order.broker_order_id,
                event_id=event_id,
                seq=seq,
            ),
            broker_order_id=working.broker_order.broker_order_id,
            quantity=quantity,
            price=price,
            filled_at=filled_at,
        )
        working.fills.append(fill)
        self._all_fills.append(fill)

        if working.remaining == ZERO:
            # Release any leftover buy reserve (limit was worse than fill).
            working.reserved_cash = ZERO
            working.reserved_qty = ZERO
            new_status = BrokerOrderStatus.FILLED
        else:
            new_status = BrokerOrderStatus.PARTIALLY_FILLED

        working.broker_order = working.broker_order.model_copy(
            update={"status": new_status, "updated_at": filled_at}
        )
        # Keep submit-result snapshot in sync for idempotent re-reads.
        client_id = working.broker_order.client_order_id
        prior = self._submit_results.get(client_id)
        if prior is not None and prior.broker_order is not None:
            self._submit_results[client_id] = prior.model_copy(
                update={"broker_order": working.broker_order, "observed_at": filled_at}
            )
        return fill

    def _credit_position(
        self,
        instrument: InstrumentIdentity,
        *,
        quantity: Decimal,
        price: Decimal,
    ) -> None:
        book = self._positions.get(instrument.instrument_id)
        if book is None:
            self._positions[instrument.instrument_id] = _PositionBook(
                instrument=instrument,
                quantity=quantity,
                average_cost=price,
            )
            return
        new_qty = canonicalize_decimal(book.quantity + quantity)
        if new_qty == ZERO:
            self._positions.pop(instrument.instrument_id, None)
            return
        new_cost = _money((book.average_cost * book.quantity + price * quantity) / new_qty)
        book.quantity = new_qty
        book.average_cost = new_cost
        book.instrument = instrument

    def _debit_position(self, instrument_id: str, *, quantity: Decimal) -> None:
        book = self._positions[instrument_id]
        new_qty = canonicalize_decimal(book.quantity - quantity)
        if new_qty < ZERO:
            raise BrokerRejectedError(
                "paper position went negative",
                reason_code=PaperRejectReason.INSUFFICIENT_POSITION.value,
            )
        if new_qty == ZERO:
            self._positions.pop(instrument_id, None)
        else:
            book.quantity = new_qty

    def _instrument_for_event(self, event: PaperMarketEvent) -> InstrumentIdentity:
        # Prefer identity from a working order; else from an existing position;
        # else synthesize a minimal equity identity for quote storage.
        for wo in self._orders.values():
            if wo.proposal.instrument_id == event.instrument_id:
                p = wo.proposal
                return InstrumentIdentity(
                    instrument_id=p.instrument_id,
                    symbol=p.symbol,
                    exchange=p.exchange,
                    currency=p.currency,
                    asset_type=p.asset_type,
                    identity_as_of=_require_utc(event.observed_at),
                    provider=None,
                )
        book = self._positions.get(event.instrument_id)
        if book is not None:
            return book.instrument.model_copy(
                update={"identity_as_of": _require_utc(event.observed_at)}
            )
        return InstrumentIdentity(
            instrument_id=event.instrument_id,
            symbol="UNKN",
            exchange="XNAS",
            currency="USD",
            asset_type=AssetType.EQUITY,
            identity_as_of=_require_utc(event.observed_at),
        )

    def _mark_prices(self) -> Mapping[str, Decimal]:
        prices: dict[str, Decimal] = {}
        for instrument_id, quote in self._quotes.items():
            prices[instrument_id] = _money(quote.last_price)
        for instrument_id, book in self._positions.items():
            prices.setdefault(instrument_id, book.average_cost)
        return prices

    def _build_snapshot(self) -> PortfolioSnapshot:
        now = self._now()
        self._snapshot_seq += 1
        marks = self._mark_prices()
        positions: list[PositionSnapshot] = []
        gross = ZERO
        for instrument_id, book in sorted(self._positions.items()):
            mark = marks.get(instrument_id, book.average_cost)
            mv = _money(book.quantity * mark)
            gross = canonicalize_decimal(gross + mv)
            instrument = book.instrument
            if instrument.identity_as_of > now:
                instrument = instrument.model_copy(update={"identity_as_of": now})
            positions.append(
                PositionSnapshot(
                    instrument=instrument,
                    quantity=book.quantity,
                    market_value=mv,
                    portfolio_weight=ZERO,  # filled below once equity known
                    average_cost=book.average_cost,
                    unrealized_pnl=_money((mark - book.average_cost) * book.quantity),
                    currency=book.instrument.currency,
                )
            )

        # Buying power = cash minus open buy reserves (sells do not free BP until fill).
        reserved_buys = sum(
            (
                wo.reserved_cash
                for wo in self._orders.values()
                if wo.broker_order.status
                in {BrokerOrderStatus.ACCEPTED, BrokerOrderStatus.PARTIALLY_FILLED}
            ),
            ZERO,
        )
        cash = self._cash
        equity = canonicalize_decimal(cash + gross)
        buying_power = canonicalize_decimal(max(ZERO, cash - reserved_buys))

        weighted: list[PositionSnapshot] = []
        for pos in positions:
            weight = ZERO if equity == ZERO else _weight(pos.market_value / equity)
            weighted.append(pos.model_copy(update={"portfolio_weight": weight}))

        largest = ZERO
        if weighted:
            largest = max(p.portfolio_weight for p in weighted)

        open_orders: list[OpenOrderSnapshot] = []
        for wo in sorted(
            self._orders.values(),
            key=lambda w: (w.broker_order.submitted_at, w.broker_order.broker_order_id),
        ):
            if wo.broker_order.status not in {
                BrokerOrderStatus.ACCEPTED,
                BrokerOrderStatus.PARTIALLY_FILLED,
            }:
                continue
            if wo.remaining <= ZERO:
                continue
            p = wo.proposal
            open_orders.append(
                OpenOrderSnapshot(
                    order_id=wo.broker_order.broker_order_id,
                    instrument=InstrumentIdentity(
                        instrument_id=p.instrument_id,
                        symbol=p.symbol,
                        exchange=p.exchange,
                        currency=p.currency,
                        asset_type=p.asset_type,
                        identity_as_of=wo.broker_order.submitted_at,
                    ),
                    side=p.side,
                    quantity=wo.remaining,
                    submitted_at=wo.broker_order.submitted_at,
                    limit_price=p.limit_price,
                    symbol=p.symbol,
                )
            )

        return PortfolioSnapshot(
            snapshot_id=f"{self._snapshot_id_prefix}_{self._snapshot_seq:08d}",
            account_scope=AccountScope.PAPER,
            as_of=now,
            currency="USD",
            cash=cash,
            buying_power=buying_power,
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
                source=_PAPER_SOURCE,
                observed_at=now,
                received_at=now,
                timezone="UTC",
                is_delayed=False,
                quality_flags=(),
            ),
        )


def as_read_port(broker: PaperBroker) -> BrokerReadPort:
    """Return a read-only view that does not expose submit/cancel."""

    class _ReadOnly:
        def get_account(self, account_scope: AccountScope) -> PortfolioSnapshot:
            return broker.get_account(account_scope)

        def get_positions(self, account_scope: AccountScope) -> tuple[PositionSnapshot, ...]:
            return broker.get_positions(account_scope)

        def get_quotes(self, instrument_ids: tuple[str, ...]) -> tuple[MarketQuote, ...]:
            return broker.get_quotes(instrument_ids)

        def get_orders(
            self,
            account_scope: AccountScope,
            *,
            broker_order_ids: tuple[str, ...] | None = None,
            client_order_ids: tuple[str, ...] | None = None,
        ) -> tuple[BrokerOrder, ...]:
            return broker.get_orders(
                account_scope,
                broker_order_ids=broker_order_ids,
                client_order_ids=client_order_ids,
            )

        def get_fills(
            self,
            account_scope: AccountScope,
            *,
            broker_order_ids: tuple[str, ...] | None = None,
        ) -> tuple[BrokerFill, ...]:
            return broker.get_fills(account_scope, broker_order_ids=broker_order_ids)

    port = _ReadOnly()
    assert_read_port_has_no_write_methods(port)
    return port


def as_write_port(broker: PaperBroker) -> BrokerWritePort:
    """Return a write-capability view (submit/cancel only)."""

    class _WriteOnly:
        def submit(self, request: BrokerSubmitRequest) -> BrokerSubmitResult:
            return broker.submit(request)

        def cancel(self, command: CancelCommand) -> CancelResult:
            return broker.cancel(command)

    port = _WriteOnly()
    assert_no_replace_operation(port)
    return port


__all__ = [
    "PaperBroker",
    "PaperClock",
    "PaperCostModel",
    "PaperMarketEvent",
    "PaperRejectReason",
    "as_read_port",
    "as_write_port",
]
