"""Unit tests for the display-only Robinhood service."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
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
from ainvest.execution.robinhood.mappers import MappingErrorCode, RobinhoodMappingError
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

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "rh_mcp" / "v0.4.2"
OBSERVED_AT = "2026-08-08T15:00:02Z"
RECEIVED_AT = datetime(2026, 8, 8, 15, 0, 3, tzinfo=UTC)
DIGEST = f"sha256:{'d' * 64}"


def _result(capability: str, *, fixture_part: str | None = None) -> GatewayReadResult:
    part = fixture_part or (
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
    def __init__(
        self,
        overrides: Mapping[str, GatewayReadResult | list[GatewayReadResult]] | None = None,
    ) -> None:
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []
        self.overrides = {
            key: list(value) if isinstance(value, list) else [value]
            for key, value in (overrides or {}).items()
        }

    async def _read(
        self, capability: str, arguments: Mapping[str, Any] | None
    ) -> GatewayReadResult:
        self.calls.append((capability, arguments))
        queued = self.overrides.get(capability)
        return queued.pop(0) if queued else _result(capability)

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
    client = _FakeClient(
        {
            "get_equity_orders": [
                _result("get_equity_orders", fixture_part="p06-t1-part1"),
                _result("get_equity_orders", fixture_part="p06-t1-part2"),
            ]
        }
    )
    service = _service(client)

    async def exercise() -> list[Any]:
        return [
            await service.accounts(),
            await service.portfolio("opaque-account"),
            await service.positions("opaque-account"),
            await service.orders(
                "opaque-account",
                view=OrderView.OPEN,
                filters={"symbol": "AAPL", "state": "queued"},
            ),
            await service.orders(
                "opaque-account",
                view=OrderView.CLOSED,
                filters={"symbol": "AAPL"},
            ),
            await service.quotes(("AAPL",)),
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
            {"account_number": "opaque-account", "symbol": "AAPL", "state": "queued"},
        ),
        ("get_equity_orders", {"account_number": "opaque-account", "symbol": "AAPL"}),
        ("get_equity_quotes", {"symbols": ["AAPL"]}),
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
    fresh = asyncio.run(_service(_FakeClient()).quotes(("AAPL",)))
    stale = asyncio.run(
        _service(_FakeClient(), clock=datetime(2026, 8, 8, 15, 1, 0, tzinfo=UTC)).quotes(("AAPL",))
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


def _mutated_result(
    capability: str, mutate: Any, *, fixture_part: str | None = None
) -> GatewayReadResult:
    original = _result(capability, fixture_part=fixture_part)
    payload = deepcopy(dict(original.payload))
    mutate(payload)
    return replace(original, payload=payload)


@pytest.mark.unit
@pytest.mark.parametrize("mutation", ["unexpected", "missing"])
def test_quotes_require_every_requested_symbol_and_no_extra_symbol(mutation: str) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        results = payload["data"]["results"]
        if mutation == "missing":
            return
        results[0]["quote"]["symbol"] = "TSLA"
        results[0]["close"]["symbol"] = "TSLA"

    client = _FakeClient({"get_equity_quotes": _mutated_result("get_equity_quotes", mutate)})

    with pytest.raises(RobinhoodMappingError) as caught:
        requested = ("AAPL", "MSFT") if mutation == "missing" else ("AAPL",)
        asyncio.run(_service(client).quotes(requested))
    assert caught.value.code is MappingErrorCode.INCONSISTENT_DATA


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "unexpected"),
    [
        ("symbol", "MSFT"),
        ("order_id", "different-order-id"),
        ("state", "confirmed"),
        ("placed_agent", "drip"),
        ("created_at_gte", "2026-08-08T14:51:00Z"),
    ],
)
def test_open_orders_bind_each_forwarded_filter_to_displayed_rows(
    field: str, unexpected: str
) -> None:
    filters = {
        "symbol": "AAPL",
        "order_id": "order-open-123",
        "state": "queued",
        "placed_agent": "user",
        "created_at_gte": "2026-08-08T14:49:00Z",
    }
    filters[field] = unexpected
    client = _FakeClient(
        {"get_equity_orders": _result("get_equity_orders", fixture_part="p06-t1-part1")}
    )

    with pytest.raises(RobinhoodMappingError) as caught:
        asyncio.run(_service(client).orders("opaque-account", view=OrderView.OPEN, filters=filters))
    assert caught.value.code is MappingErrorCode.INCONSISTENT_DATA


@pytest.mark.unit
def test_open_orders_accept_matching_filters_including_date_only_cutoff() -> None:
    client = _FakeClient(
        {"get_equity_orders": _result("get_equity_orders", fixture_part="p06-t1-part1")}
    )
    matched = asyncio.run(
        _service(client).orders(
            "opaque-account",
            view=OrderView.OPEN,
            filters={
                "symbol": "AAPL",
                "order_id": "order-open-123",
                "state": "queued",
                "placed_agent": "user",
                "created_at_gte": "2026-08-08",
            },
        )
    )

    matched_data = cast(Any, matched.data)
    assert [order.order_id for order in matched_data.open_orders] == ["order-open-123"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "created_at_gte",
    ["20260808", "2026-W32-5", "2026-08-08T14:49:00", "2026-13-01"],
)
def test_display_service_rejects_noncanonical_order_time_filters(created_at_gte: str) -> None:
    client = _FakeClient(
        {"get_equity_orders": _result("get_equity_orders", fixture_part="p06-t1-part1")}
    )

    with pytest.raises(RobinhoodMappingError) as caught:
        asyncio.run(
            _service(client).orders(
                "opaque-account",
                view=OrderView.OPEN,
                filters={"created_at_gte": created_at_gte},
            )
        )
    assert caught.value.code is MappingErrorCode.INVALID_VALUE


@pytest.mark.unit
def test_explicit_fundamental_bounds_are_bound_to_every_result() -> None:
    with pytest.raises(RobinhoodMappingError) as caught:
        asyncio.run(
            _service(_FakeClient()).fundamentals(
                ("AAPL", "MSFT"), bounds=FundamentalBounds.EXTENDED
            )
        )
    assert caught.value.code is MappingErrorCode.INCONSISTENT_DATA


@pytest.mark.unit
def test_financial_period_count_is_bounded_by_requested_limit() -> None:
    service = _service(_FakeClient())
    with pytest.raises(RobinhoodMappingError) as caught:
        asyncio.run(service.financials(("AAPL", "MSFT"), period=ReportingPeriod.QUARTERLY, limit=1))
    assert caught.value.code is MappingErrorCode.INCONSISTENT_DATA

    accepted = asyncio.run(
        service.financials(("AAPL", "MSFT"), period=ReportingPeriod.QUARTERLY, limit=2)
    )
    accepted_data = cast(Any, accepted.data)
    assert len(accepted_data.series[0].financials) == 2


@pytest.mark.unit
@pytest.mark.parametrize("limit", [0, 41])
def test_display_service_rejects_out_of_contract_financial_limits(limit: int) -> None:
    with pytest.raises(RobinhoodMappingError) as caught:
        asyncio.run(
            _service(_FakeClient()).financials(
                ("AAPL", "MSFT"), period=ReportingPeriod.QUARTERLY, limit=limit
            )
        )
    assert caught.value.code is MappingErrorCode.INVALID_VALUE
