"""Unit tests for the display-only Robinhood service."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from ainvest.execution.robinhood.display import (
    DISPLAY_QUOTE_MAX_AGE_SECONDS,
    AdjustmentType,
    DisplayCommand,
    OrderView,
    RobinhoodDisplayService,
)
from ainvest.execution.robinhood.pins import EXPECTED_MANIFEST_DIGEST, PINNED_MANIFEST_VERSION
from ainvest.execution.robinhood.prose import discard_provider_prose
from ainvest.execution.robinhood.read_client import GatewayReadResult, RobinhoodReadClient
from ainvest.execution.robinhood.read_models import (
    FundamentalBounds,
    HistoricalBounds,
    HistoricalInterval,
    QuoteIneligibility,
    ReportingPeriod,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "rh_mcp" / "v0.2.0"
OBSERVED_AT = "2026-08-08T15:00:02Z"
RECEIVED_AT = datetime(2026, 8, 8, 15, 0, 3, tzinfo=UTC)
DIGEST = f"sha256:{'d' * 64}"


def _result(capability: str) -> GatewayReadResult:
    part = (
        "p06-t1-part1"
        if capability
        in {
            "get_accounts",
            "get_portfolio",
            "get_equity_positions",
            "get_equity_quotes",
        }
        else "p06-t1-part2"
    )
    raw = json.loads((FIXTURES / part / f"{capability}.json").read_text(encoding="utf-8"))
    payload = discard_provider_prose(raw)
    assert isinstance(payload, dict)
    return GatewayReadResult(
        capability=capability,
        manifest_version=PINNED_MANIFEST_VERSION,
        manifest_digest=EXPECTED_MANIFEST_DIGEST,
        schema_digest=DIGEST,
        result_digest=DIGEST,
        observed_at=OBSERVED_AT,
        payload=payload,
        warnings=(),
    )


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []

    async def _read(
        self, capability: str, arguments: Mapping[str, Any] | None
    ) -> GatewayReadResult:
        self.calls.append((capability, arguments))
        return _result(capability)

    async def read_accounts(self, arguments: Mapping[str, Any] | None = None) -> GatewayReadResult:
        return await self._read("get_accounts", arguments)

    async def read_portfolio(self, arguments: Mapping[str, Any] | None = None) -> GatewayReadResult:
        return await self._read("get_portfolio", arguments)

    async def read_equity_positions(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        return await self._read("get_equity_positions", arguments)

    async def read_equity_orders(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        return await self._read("get_equity_orders", arguments)

    async def read_equity_quotes(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        return await self._read("get_equity_quotes", arguments)

    async def read_equity_price_book(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        return await self._read("get_equity_price_book", arguments)

    async def read_equity_tradability(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        return await self._read("get_equity_tradability", arguments)

    async def read_equity_historicals(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        return await self._read("get_equity_historicals", arguments)

    async def read_equity_fundamentals(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        return await self._read("get_equity_fundamentals", arguments)

    async def read_financials(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        return await self._read("get_financials", arguments)


def _service(client: _FakeClient, *, clock: datetime = RECEIVED_AT) -> RobinhoodDisplayService:
    return RobinhoodDisplayService(cast(RobinhoodReadClient, client), clock=lambda: clock)


@pytest.mark.unit
def test_status_is_local_and_has_the_exact_safety_posture() -> None:
    client = _FakeClient()
    document = _service(client).status().model_dump(mode="json")

    assert document == {
        "schema_version": "1.0",
        "command": "status",
        "ready": True,
        "posture": {"read_only": True, "mode": "display_only", "execution": "disabled"},
        "limitations": {
            "usable_for_trading": False,
            "identity": "not_applicable",
            "account_binding": "not_applicable",
            "session_evidence": "not_applicable",
        },
        "data": {"ready": True},
    }
    assert client.calls == []


@pytest.mark.unit
def test_all_named_display_reads_normalize_without_a_generic_dispatch() -> None:
    client = _FakeClient()
    service = _service(client)

    async def exercise() -> list[Any]:
        return [
            await service.accounts(),
            await service.portfolio("opaque-account"),
            await service.positions("opaque-account"),
            await service.orders(
                "opaque-account",
                view=OrderView.OPEN,
                filters={"symbol": "AAPL", "state": "new"},
            ),
            await service.orders(
                "opaque-account",
                view=OrderView.CLOSED,
                filters={"symbol": "AAPL"},
            ),
            await service.quotes(("AAPL", "MSFT")),
            await service.price_book(("AAPL", "MSFT")),
            await service.tradability("opaque-account", ("AAPL", "MSFT")),
            await service.historicals(
                ("AAPL", "MSFT"),
                start_time="2026-08-01T00:00:00Z",
                end_time="2026-08-08T00:00:00Z",
                interval=HistoricalInterval.MINUTE_5,
                bounds=HistoricalBounds.REGULAR,
                adjustment_type=AdjustmentType.SPLIT,
            ),
            await service.fundamentals(("AAPL", "MSFT"), bounds=FundamentalBounds.REGULAR),
            await service.financials(("AAPL", "MSFT"), period=ReportingPeriod.QUARTERLY, limit=4),
        ]

    documents = asyncio.run(exercise())

    assert [document.command for document in documents] == [
        DisplayCommand.ACCOUNTS,
        DisplayCommand.PORTFOLIO,
        DisplayCommand.POSITIONS,
        DisplayCommand.ORDERS,
        DisplayCommand.ORDERS,
        DisplayCommand.QUOTES,
        DisplayCommand.PRICE_BOOK,
        DisplayCommand.TRADABILITY,
        DisplayCommand.HISTORICALS,
        DisplayCommand.FUNDAMENTALS,
        DisplayCommand.FINANCIALS,
    ]
    assert all(document.limitations.usable_for_trading is False for document in documents)
    assert client.calls == [
        ("get_accounts", None),
        ("get_portfolio", {"account_number": "opaque-account"}),
        ("get_equity_positions", {"account_number": "opaque-account"}),
        (
            "get_equity_orders",
            {"account_number": "opaque-account", "symbol": "AAPL", "state": "new"},
        ),
        ("get_equity_orders", {"account_number": "opaque-account", "symbol": "AAPL"}),
        ("get_equity_quotes", {"symbols": ["AAPL", "MSFT"]}),
        ("get_equity_price_book", {"symbols": ["AAPL", "MSFT"]}),
        (
            "get_equity_tradability",
            {"account_number": "opaque-account", "symbols": ["AAPL", "MSFT"]},
        ),
        (
            "get_equity_historicals",
            {
                "symbols": ["AAPL", "MSFT"],
                "start_time": "2026-08-01T00:00:00Z",
                "end_time": "2026-08-08T00:00:00Z",
                "interval": "5minute",
                "bounds": "regular",
                "adjustment_type": "split",
            },
        ),
        ("get_equity_fundamentals", {"symbols": ["AAPL", "MSFT"], "bounds": "regular"}),
        (
            "get_financials",
            {"symbols": ["AAPL", "MSFT"], "period": "quarterly", "limit": 4},
        ),
    ]


@pytest.mark.unit
def test_quote_age_is_fixed_at_fifteen_seconds_and_never_trading_eligible() -> None:
    assert DISPLAY_QUOTE_MAX_AGE_SECONDS == 15
    fresh = asyncio.run(_service(_FakeClient()).quotes(("AAPL", "MSFT")))
    stale = asyncio.run(
        _service(_FakeClient(), clock=datetime(2026, 8, 8, 15, 1, 0, tzinfo=UTC)).quotes(
            ("AAPL", "MSFT")
        )
    )

    fresh_data = cast(Any, fresh.data)
    stale_data = cast(Any, stale.data)
    assert all(quote.live_eligible is False for quote in fresh_data.quotes)
    assert all(
        QuoteIneligibility.SESSION_UNVERIFIED in quote.ineligibility for quote in fresh_data.quotes
    )
    assert all(QuoteIneligibility.STALE in quote.ineligibility for quote in stale_data.quotes)


@pytest.mark.unit
def test_display_clock_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(_service(_FakeClient(), clock=datetime(2026, 8, 8, 15, 0, 3)).accounts())
