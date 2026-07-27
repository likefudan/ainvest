"""Portfolio snapshot assembly helpers for the paper broker."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from ainvest.execution.paper.ledger import _open_working_orders
from ainvest.execution.paper.types import (
    _PAPER_SOURCE,
    ZERO,
    _money,
    _PositionBook,
    _weight,
    _WorkingOrder,
)
from ainvest.schemas.broker import BrokerOrderStatus
from ainvest.schemas.common import InstrumentIdentity, Provenance, canonicalize_decimal
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.portfolio import (
    AccountScope,
    ExposureSnapshot,
    OpenOrderSnapshot,
    PortfolioSnapshot,
    PositionSnapshot,
)


def _mark_prices(
    quotes: Mapping[str, MarketQuote],
    positions: Mapping[str, _PositionBook],
) -> Mapping[str, Decimal]:
    prices: dict[str, Decimal] = {}
    for instrument_id, quote in quotes.items():
        prices[instrument_id] = _money(quote.last_price)
    for instrument_id, book in positions.items():
        prices.setdefault(instrument_id, book.average_cost)
    return prices


def _build_snapshot(
    *,
    now: datetime,
    snapshot_id_prefix: str,
    snapshot_seq: int,
    cash: Decimal,
    positions: Mapping[str, _PositionBook],
    orders: Mapping[str, _WorkingOrder],
    quotes: Mapping[str, MarketQuote],
) -> PortfolioSnapshot:
    marks = _mark_prices(quotes, positions)
    position_snaps: list[PositionSnapshot] = []
    gross = ZERO
    for instrument_id, book in sorted(positions.items()):
        mark = marks.get(instrument_id, book.average_cost)
        mv = _money(book.quantity * mark)
        gross = canonicalize_decimal(gross + mv)
        instrument = book.instrument
        if instrument.identity_as_of > now:
            instrument = instrument.model_copy(update={"identity_as_of": now})
        position_snaps.append(
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
    reserved_buys = sum((wo.reserved_cash for wo in _open_working_orders(orders)), ZERO)
    equity = canonicalize_decimal(cash + gross)
    buying_power = canonicalize_decimal(max(ZERO, cash - reserved_buys))

    weighted: list[PositionSnapshot] = []
    for pos in position_snaps:
        weight = ZERO if equity == ZERO else _weight(pos.market_value / equity)
        weighted.append(pos.model_copy(update={"portfolio_weight": weight}))

    largest = ZERO
    if weighted:
        largest = max(p.portfolio_weight for p in weighted)

    open_orders: list[OpenOrderSnapshot] = []
    for wo in sorted(
        orders.values(),
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
        snapshot_id=f"{snapshot_id_prefix}_{snapshot_seq:08d}",
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
