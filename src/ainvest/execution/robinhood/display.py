"""Display-only service over normalized Robinhood reads (P06-T2 Part 1).

This module is the reusable boundary between user-facing read adapters and the
named :class:`RobinhoodReadClient` projection.  It returns only P06-T1 models;
it has no generic capability dispatch and no path into Paper or execution.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Literal, Self

from pydantic import model_validator

from ainvest.execution.robinhood.mappers import (
    map_accounts,
    map_closed_equity_orders,
    map_equity_fundamentals,
    map_equity_historicals,
    map_equity_positions,
    map_equity_price_books,
    map_equity_quotes,
    map_equity_tradability,
    map_financials,
    map_open_equity_orders,
    map_portfolio,
)
from ainvest.execution.robinhood.read_client import RobinhoodReadClient
from ainvest.execution.robinhood.read_models import (
    AccountsRead,
    ClosedOrdersRead,
    FinancialsRead,
    FundamentalBounds,
    FundamentalsRead,
    HistoricalBounds,
    HistoricalInterval,
    HistoricalsRead,
    OpenOrdersRead,
    PortfolioRead,
    PositionsRead,
    PriceBooksRead,
    QuotesRead,
    ReportingPeriod,
    TradabilitiesRead,
)
from ainvest.schemas.common import DomainModel, Symbol

DISPLAY_QUOTE_MAX_AGE_SECONDS: Final = 15


class DisplayCommand(StrEnum):
    STATUS = "status"
    ACCOUNTS = "accounts"
    PORTFOLIO = "portfolio"
    POSITIONS = "positions"
    ORDERS = "orders"
    QUOTES = "quotes"
    PRICE_BOOK = "price-book"
    TRADABILITY = "tradability"
    HISTORICALS = "historicals"
    FUNDAMENTALS = "fundamentals"
    FINANCIALS = "financials"


class OrderView(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class AdjustmentType(StrEnum):
    NONE = "none"
    SPLIT = "split"
    ALL = "all"


class DisplayPosture(DomainModel):
    read_only: Literal[True] = True
    mode: Literal["display_only"] = "display_only"
    execution: Literal["disabled"] = "disabled"


class DisplayLimitations(DomainModel):
    usable_for_trading: Literal[False] = False
    identity: Literal["not_applicable", "partial_or_unverified"]
    account_binding: Literal["not_applicable", "unverified"]
    session_evidence: Literal["not_applicable", "unverified"]


class DisplayStatusData(DomainModel):
    ready: Literal[True] = True


DisplayData = (
    DisplayStatusData
    | AccountsRead
    | PortfolioRead
    | PositionsRead
    | OpenOrdersRead
    | ClosedOrdersRead
    | QuotesRead
    | PriceBooksRead
    | TradabilitiesRead
    | HistoricalsRead
    | FundamentalsRead
    | FinancialsRead
)


_LIMITATIONS: Final[dict[DisplayCommand, DisplayLimitations]] = {
    DisplayCommand.STATUS: DisplayLimitations(
        identity="not_applicable",
        account_binding="not_applicable",
        session_evidence="not_applicable",
    ),
    DisplayCommand.ACCOUNTS: DisplayLimitations(
        identity="not_applicable",
        account_binding="unverified",
        session_evidence="not_applicable",
    ),
    DisplayCommand.PORTFOLIO: DisplayLimitations(
        identity="not_applicable",
        account_binding="unverified",
        session_evidence="not_applicable",
    ),
    DisplayCommand.POSITIONS: DisplayLimitations(
        identity="partial_or_unverified",
        account_binding="unverified",
        session_evidence="not_applicable",
    ),
    DisplayCommand.ORDERS: DisplayLimitations(
        identity="partial_or_unverified",
        account_binding="unverified",
        session_evidence="not_applicable",
    ),
    DisplayCommand.QUOTES: DisplayLimitations(
        identity="partial_or_unverified",
        account_binding="not_applicable",
        session_evidence="unverified",
    ),
    DisplayCommand.PRICE_BOOK: DisplayLimitations(
        identity="partial_or_unverified",
        account_binding="not_applicable",
        session_evidence="unverified",
    ),
    DisplayCommand.TRADABILITY: DisplayLimitations(
        identity="partial_or_unverified",
        account_binding="unverified",
        session_evidence="unverified",
    ),
    DisplayCommand.HISTORICALS: DisplayLimitations(
        identity="partial_or_unverified",
        account_binding="not_applicable",
        session_evidence="unverified",
    ),
    DisplayCommand.FUNDAMENTALS: DisplayLimitations(
        identity="partial_or_unverified",
        account_binding="not_applicable",
        session_evidence="not_applicable",
    ),
    DisplayCommand.FINANCIALS: DisplayLimitations(
        identity="partial_or_unverified",
        account_binding="not_applicable",
        session_evidence="not_applicable",
    ),
}


_DATA_TYPES: Final[dict[DisplayCommand, type[DomainModel] | tuple[type[DomainModel], ...]]] = {
    DisplayCommand.STATUS: DisplayStatusData,
    DisplayCommand.ACCOUNTS: AccountsRead,
    DisplayCommand.PORTFOLIO: PortfolioRead,
    DisplayCommand.POSITIONS: PositionsRead,
    DisplayCommand.ORDERS: (OpenOrdersRead, ClosedOrdersRead),
    DisplayCommand.QUOTES: QuotesRead,
    DisplayCommand.PRICE_BOOK: PriceBooksRead,
    DisplayCommand.TRADABILITY: TradabilitiesRead,
    DisplayCommand.HISTORICALS: HistoricalsRead,
    DisplayCommand.FUNDAMENTALS: FundamentalsRead,
    DisplayCommand.FINANCIALS: FinancialsRead,
}


class DisplaySuccess(DomainModel):
    """Exact successful display wire envelope."""

    schema_version: Literal["1.0"] = "1.0"
    command: DisplayCommand
    ready: Literal[True] = True
    posture: DisplayPosture = DisplayPosture()
    limitations: DisplayLimitations
    data: DisplayData

    @model_validator(mode="after")
    def _command_contract_is_exact(self) -> Self:
        if self.limitations != _LIMITATIONS[self.command]:
            raise ValueError("limitations must match the display command")
        if not isinstance(self.data, _DATA_TYPES[self.command]):
            raise ValueError("data type must match the display command")
        return self


Clock = Callable[[], datetime]


class RobinhoodDisplayService:
    """Normalized read service that is structurally unable to invoke a mutation."""

    __slots__ = ("_client", "_clock")

    def __init__(
        self,
        client: RobinhoodReadClient,
        *,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._clock = clock

    def status(self) -> DisplaySuccess:
        return self._success(DisplayCommand.STATUS, DisplayStatusData())

    async def accounts(self) -> DisplaySuccess:
        result = await self._client.read_accounts()
        return self._success(
            DisplayCommand.ACCOUNTS,
            map_accounts(result, received_at=self._received_at()),
        )

    async def portfolio(self, account_number: str) -> DisplaySuccess:
        result = await self._client.read_portfolio({"account_number": account_number})
        return self._success(
            DisplayCommand.PORTFOLIO,
            map_portfolio(result, received_at=self._received_at()),
        )

    async def positions(self, account_number: str) -> DisplaySuccess:
        result = await self._client.read_equity_positions({"account_number": account_number})
        return self._success(
            DisplayCommand.POSITIONS,
            map_equity_positions(result, received_at=self._received_at()),
        )

    async def orders(
        self,
        account_number: str,
        *,
        view: OrderView,
        filters: Mapping[str, str],
    ) -> DisplaySuccess:
        arguments = {"account_number": account_number, **filters}
        result = await self._client.read_equity_orders(arguments)
        received_at = self._received_at()
        expected_symbol = filters.get("symbol")
        if view is OrderView.OPEN:
            data: OpenOrdersRead | ClosedOrdersRead = map_open_equity_orders(
                result,
                received_at=received_at,
                expected_symbol=expected_symbol,
            )
        else:
            data = map_closed_equity_orders(
                result,
                received_at=received_at,
                expected_symbol=expected_symbol,
                expected_order_id=filters.get("order_id"),
            )
        return self._success(DisplayCommand.ORDERS, data)

    async def quotes(self, symbols: Sequence[Symbol]) -> DisplaySuccess:
        result = await self._client.read_equity_quotes({"symbols": list(symbols)})
        return self._success(
            DisplayCommand.QUOTES,
            map_equity_quotes(
                result,
                received_at=self._received_at(),
                max_quote_age_seconds=DISPLAY_QUOTE_MAX_AGE_SECONDS,
            ),
        )

    async def price_book(self, symbols: Sequence[Symbol]) -> DisplaySuccess:
        result = await self._client.read_equity_price_book({"symbols": list(symbols)})
        return self._success(
            DisplayCommand.PRICE_BOOK,
            map_equity_price_books(
                result,
                received_at=self._received_at(),
                expected_symbols=symbols,
            ),
        )

    async def tradability(self, account_number: str, symbols: Sequence[Symbol]) -> DisplaySuccess:
        result = await self._client.read_equity_tradability(
            {"account_number": account_number, "symbols": list(symbols)}
        )
        return self._success(
            DisplayCommand.TRADABILITY,
            map_equity_tradability(
                result,
                received_at=self._received_at(),
                expected_symbols=symbols,
            ),
        )

    async def historicals(
        self,
        symbols: Sequence[Symbol],
        *,
        start_time: str,
        end_time: str | None = None,
        interval: HistoricalInterval | None = None,
        bounds: HistoricalBounds | None = None,
        adjustment_type: AdjustmentType | None = None,
    ) -> DisplaySuccess:
        arguments: dict[str, Any] = {"symbols": list(symbols), "start_time": start_time}
        optional = {
            "end_time": end_time,
            "interval": interval.value if interval is not None else None,
            "bounds": bounds.value if bounds is not None else None,
            "adjustment_type": adjustment_type.value if adjustment_type is not None else None,
        }
        arguments.update({key: value for key, value in optional.items() if value is not None})
        result = await self._client.read_equity_historicals(arguments)
        return self._success(
            DisplayCommand.HISTORICALS,
            map_equity_historicals(
                result,
                received_at=self._received_at(),
                expected_symbols=symbols,
                expected_interval=interval,
                expected_bounds=bounds,
            ),
        )

    async def fundamentals(
        self,
        symbols: Sequence[Symbol],
        *,
        bounds: FundamentalBounds | None = None,
    ) -> DisplaySuccess:
        arguments: dict[str, Any] = {"symbols": list(symbols)}
        if bounds is not None:
            arguments["bounds"] = bounds.value
        result = await self._client.read_equity_fundamentals(arguments)
        return self._success(
            DisplayCommand.FUNDAMENTALS,
            map_equity_fundamentals(
                result,
                received_at=self._received_at(),
                expected_symbols=symbols,
            ),
        )

    async def financials(
        self,
        symbols: Sequence[Symbol],
        *,
        period: ReportingPeriod,
        limit: int,
    ) -> DisplaySuccess:
        result = await self._client.read_financials(
            {"symbols": list(symbols), "period": period.value, "limit": limit}
        )
        return self._success(
            DisplayCommand.FINANCIALS,
            map_financials(
                result,
                received_at=self._received_at(),
                expected_symbols=symbols,
                expected_period=period,
            ),
        )

    def _received_at(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("display clock must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _success(command: DisplayCommand, data: DisplayData) -> DisplaySuccess:
        return DisplaySuccess(command=command, limitations=_LIMITATIONS[command], data=data)


__all__ = [
    "DISPLAY_QUOTE_MAX_AGE_SECONDS",
    "AdjustmentType",
    "DisplayCommand",
    "DisplayLimitations",
    "DisplayPosture",
    "DisplayStatusData",
    "DisplaySuccess",
    "OrderView",
    "RobinhoodDisplayService",
]
