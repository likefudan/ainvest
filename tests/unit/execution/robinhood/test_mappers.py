"""Mapping tests for the first functional Robinhood normalized reads."""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from ainvest.execution.robinhood.mappers import (
    MappingErrorCode,
    RobinhoodMappingError,
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
from ainvest.execution.robinhood.pins import EXPECTED_MANIFEST_DIGEST, PINNED_MANIFEST_VERSION
from ainvest.execution.robinhood.prose import discard_provider_prose
from ainvest.execution.robinhood.read_client import GatewayReadResult
from ainvest.execution.robinhood.read_models import (
    UNAVAILABLE_UNTRUSTED_TEXT,
    AccountBinding,
    BrokerageTradingType,
    HistoricalBounds,
    HistoricalInterval,
    NormalizedUnit,
    QuoteIneligibility,
    ReportingPeriod,
    RobinhoodAccountScope,
    SessionEvidence,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "rh_mcp" / "v0.3.3" / "p06-t1-part1"
PART2_FIXTURES = FIXTURES.parent / "p06-t1-part2"
OBSERVED_AT = "2026-08-08T15:00:02Z"
RECEIVED_AT = "2026-08-08T15:00:03Z"
DIGEST_B = f"sha256:{'b' * 64}"
DIGEST_C = f"sha256:{'c' * 64}"


def _provider_result(capability: str) -> dict[str, Any]:
    path = FIXTURES / f"{capability}.json"
    if not path.exists():
        path = PART2_FIXTURES / f"{capability}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _result(capability: str, provider_result: dict[str, Any] | None = None) -> GatewayReadResult:
    raw = _provider_result(capability) if provider_result is None else provider_result
    payload = discard_provider_prose(raw)
    assert isinstance(payload, dict)
    return GatewayReadResult(
        capability=capability,
        manifest_version=PINNED_MANIFEST_VERSION,
        manifest_digest=EXPECTED_MANIFEST_DIGEST,
        schema_digest=DIGEST_B,
        result_digest=DIGEST_C,
        observed_at=OBSERVED_AT,
        payload=payload,
        warnings=(),
    )


def _part2_provider_result(capability: str) -> dict[str, Any]:
    value = json.loads((PART2_FIXTURES / f"{capability}.json").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _part2_result(
    capability: str, provider_result: dict[str, Any] | None = None
) -> GatewayReadResult:
    raw = _part2_provider_result(capability) if provider_result is None else provider_result
    return _result(capability, raw)


@pytest.mark.unit
def test_all_five_valid_reads_map_and_preserve_evidence() -> None:
    accounts = map_accounts(_result("get_accounts"), received_at=RECEIVED_AT)
    portfolio = map_portfolio(
        _result("get_portfolio"),
        received_at=RECEIVED_AT,
    )
    positions = map_equity_positions(
        _result("get_equity_positions"),
        received_at=RECEIVED_AT,
    )
    quotes = map_equity_quotes(
        _result("get_equity_quotes"),
        received_at=RECEIVED_AT,
        max_quote_age_seconds=15,
    )
    orders = map_open_equity_orders(
        _result("get_equity_orders"),
        received_at=RECEIVED_AT,
        expected_symbol="AAPL",
    )

    assert accounts.accounts[0].tradable is True
    assert accounts.accounts[0].trading_type is BrokerageTradingType.LIMITED_MARGIN
    assert portfolio.options_value > 0 and portfolio.crypto_value > 0
    assert positions.positions[0].symbol == "AAPL"
    assert quotes.quotes[0].live_eligible is False
    assert QuoteIneligibility.SESSION_UNVERIFIED in quotes.quotes[0].ineligibility
    assert len(orders.open_orders) == 1
    assert orders.open_orders[0].placed_agent == "user"
    assert orders.records_seen == 2
    for unbound in (portfolio, positions, orders):
        assert "account_scope" not in unbound.model_dump()
    for normalized in (accounts, portfolio, positions, quotes, orders):
        assert normalized.schema_version == "1.0"
        assert normalized.evidence.result_digest == DIGEST_C
        assert normalized.evidence.provenance.source == "robinhood_mcp"


@pytest.mark.unit
def test_account_numbers_and_provider_prose_do_not_cross_boundary() -> None:
    normalized = map_accounts(_result("get_accounts"), received_at=RECEIVED_AT)
    rendered = normalized.model_dump_json()

    assert "SENSITIVE-1234" not in rendered
    assert "99887766" not in rendered
    assert "Provider-authored" not in rendered
    assert "account_number" not in rendered
    assert "guide" not in rendered


@pytest.mark.unit
def test_account_trading_types_remain_three_distinct_provider_labels() -> None:
    """Limited margin is neither cash nor full margin and implies no policy."""
    mapped: list[BrokerageTradingType] = []
    eligibility: list[tuple[RobinhoodAccountScope, bool]] = []
    for raw_type in ("cash", "limited_margin", "margin"):
        payload = _provider_result("get_accounts")
        payload["data"]["accounts"][0]["type"] = raw_type
        account = map_accounts(
            _result("get_accounts", payload),
            received_at=RECEIVED_AT,
        ).accounts[0]
        mapped.append(account.trading_type)
        eligibility.append((account.scope, account.tradable))

    assert mapped == [
        BrokerageTradingType.CASH,
        BrokerageTradingType.LIMITED_MARGIN,
        BrokerageTradingType.MARGIN,
    ]
    assert len(set(mapped)) == 3
    assert len(set(eligibility)) == 1


@pytest.mark.unit
def test_unknown_account_type_and_duplicate_default_fail_closed() -> None:
    unknown = _provider_result("get_accounts")
    unknown["data"]["accounts"][0]["type"] = "mystery"
    with pytest.raises(RobinhoodMappingError) as caught:
        map_accounts(_result("get_accounts", unknown), received_at=RECEIVED_AT)
    assert caught.value.code is MappingErrorCode.INVALID_VALUE

    ambiguous = _provider_result("get_accounts")
    second = deepcopy(ambiguous["data"]["accounts"][0])
    second["is_default"] = False
    second["account_number"] = "SENSITIVE-5678"
    second["rhs_account_number"] = "11223344"
    ambiguous["data"]["accounts"].append(second)
    with pytest.raises(RobinhoodMappingError) as caught:
        map_accounts(_result("get_accounts", ambiguous), received_at=RECEIVED_AT)
    assert caught.value.code is MappingErrorCode.INVALID_VALUE

    duplicate = _provider_result("get_accounts")
    duplicate["data"]["accounts"].append(deepcopy(duplicate["data"]["accounts"][0]))
    with pytest.raises(RobinhoodMappingError) as caught:
        map_accounts(_result("get_accounts", duplicate), received_at=RECEIVED_AT)
    assert caught.value.code is MappingErrorCode.INVALID_VALUE


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["", " 1.0", "01.0", "1e2", 1.0, None])
def test_noncanonical_portfolio_decimals_fail_closed(bad: object) -> None:
    payload = _provider_result("get_portfolio")
    payload["data"]["cash"] = bad
    with pytest.raises(RobinhoodMappingError) as caught:
        map_portfolio(
            _result("get_portfolio", payload),
            received_at=RECEIVED_AT,
        )
    assert caught.value.code is MappingErrorCode.INVALID_VALUE


@pytest.mark.unit
def test_portfolio_preserves_mixed_assets_but_rejects_currency_mismatch() -> None:
    normalized = map_portfolio(
        _result("get_portfolio"),
        received_at=RECEIVED_AT,
    )
    assert normalized.options_value == 200
    assert normalized.crypto_value == 100

    payload = _provider_result("get_portfolio")
    payload["data"]["buying_power"]["display_currency"] = "EUR"
    with pytest.raises(RobinhoodMappingError) as caught:
        map_portfolio(
            _result("get_portfolio", payload),
            received_at=RECEIVED_AT,
        )
    assert caught.value.code is MappingErrorCode.INVALID_VALUE

    mismatch = _provider_result("get_portfolio")
    mismatch["data"]["total_value"] = "9999.00"
    with pytest.raises(RobinhoodMappingError) as caught:
        map_portfolio(_result("get_portfolio", mismatch), received_at=RECEIVED_AT)
    assert caught.value.code is MappingErrorCode.INVALID_VALUE


@pytest.mark.unit
@pytest.mark.parametrize("position_type", ["short", "boxed", "empty", "mystery"])
def test_non_long_positions_fail_closed(position_type: str) -> None:
    payload = _provider_result("get_equity_positions")
    payload["data"]["positions"][0]["type"] = position_type
    with pytest.raises(RobinhoodMappingError) as caught:
        map_equity_positions(
            _result("get_equity_positions", payload),
            received_at=RECEIVED_AT,
        )
    assert caught.value.code is MappingErrorCode.UNSUPPORTED_POSITION


@pytest.mark.unit
def test_quote_missing_or_stale_market_fields_is_not_live_eligible() -> None:
    missing = _provider_result("get_equity_quotes")
    quote = missing["data"]["results"][0]["quote"]
    quote["bid_price"] = "0"
    quote["venue_ask_time"] = ""
    normalized = map_equity_quotes(
        _result("get_equity_quotes", missing),
        received_at=RECEIVED_AT,
        max_quote_age_seconds=15,
    ).quotes[0]
    assert normalized.live_eligible is False
    assert QuoteIneligibility.MISSING_BID in normalized.ineligibility
    assert QuoteIneligibility.MISSING_ASK_TIME in normalized.ineligibility

    stale = map_equity_quotes(
        _result("get_equity_quotes"),
        received_at="2026-08-08T15:01:00Z",
        max_quote_age_seconds=15,
    ).quotes[0]
    assert stale.live_eligible is False
    assert QuoteIneligibility.STALE in stale.ineligibility


@pytest.mark.unit
def test_quote_symbol_mismatch_and_bad_time_fail_closed() -> None:
    mismatch = _provider_result("get_equity_quotes")
    mismatch["data"]["results"][0]["close"]["symbol"] = "MSFT"
    with pytest.raises(RobinhoodMappingError) as caught:
        map_equity_quotes(
            _result("get_equity_quotes", mismatch),
            received_at=RECEIVED_AT,
            max_quote_age_seconds=15,
        )
    assert caught.value.code is MappingErrorCode.INCONSISTENT_DATA

    bad_time = _provider_result("get_equity_quotes")
    bad_time["data"]["results"][0]["quote"]["venue_last_trade_time"] = "not-a-time"
    with pytest.raises(RobinhoodMappingError) as caught:
        map_equity_quotes(
            _result("get_equity_quotes", bad_time),
            received_at=RECEIVED_AT,
            max_quote_age_seconds=15,
        )
    assert caught.value.code is MappingErrorCode.INVALID_VALUE


@pytest.mark.unit
def test_order_view_rejects_unknown_state_symbol_mismatch_and_amount_mismatch() -> None:
    unknown = _provider_result("get_equity_orders")
    unknown["data"]["orders"][0]["state"] = "surprise"
    with pytest.raises(RobinhoodMappingError) as caught:
        map_open_equity_orders(
            _result("get_equity_orders", unknown),
            received_at=RECEIVED_AT,
        )
    assert caught.value.code is MappingErrorCode.INVALID_VALUE

    unsafe_agent = _provider_result("get_equity_orders")
    unsafe_agent["data"]["orders"][0]["placed_agent"] = "user\nprovider-prose"
    with pytest.raises(RobinhoodMappingError) as caught:
        map_open_equity_orders(
            _result("get_equity_orders", unsafe_agent),
            received_at=RECEIVED_AT,
        )
    assert caught.value.code is MappingErrorCode.INVALID_VALUE

    with pytest.raises(RobinhoodMappingError) as caught:
        map_open_equity_orders(
            _result("get_equity_orders"),
            received_at=RECEIVED_AT,
            expected_symbol="MSFT",
        )
    assert caught.value.code is MappingErrorCode.INCONSISTENT_DATA

    amount = _provider_result("get_equity_orders")
    amount["data"]["orders"][0]["quantity"] = None
    amount["data"]["orders"][0]["dollar_based_amount"] = {
        "amount": "25.00",
        "currency_code": "US",
    }
    with pytest.raises(RobinhoodMappingError) as caught:
        map_open_equity_orders(
            _result("get_equity_orders", amount),
            received_at=RECEIVED_AT,
        )
    assert caught.value.code is MappingErrorCode.INVALID_VALUE


@pytest.mark.unit
def test_wrong_capability_and_invalid_errors_are_sanitized() -> None:
    result = _result("get_accounts")
    with pytest.raises(RobinhoodMappingError) as caught:
        map_portfolio(
            result,
            received_at=RECEIVED_AT,
        )
    assert caught.value.code is MappingErrorCode.WRONG_CAPABILITY

    raw = _provider_result("get_accounts")
    raw["data"]["accounts"][0]["type"] = "SECRET-PROVIDER-VALUE"
    with pytest.raises(RobinhoodMappingError) as caught:
        map_accounts(_result("get_accounts", raw), received_at=RECEIVED_AT)
    rendered = f"{caught.value!s} {caught.value!r}"
    assert "SECRET-PROVIDER-VALUE" not in rendered
    assert "SENSITIVE-1234" not in rendered


@pytest.mark.unit
def test_part2_valid_reads_map_with_explicit_limitations_and_evidence() -> None:
    books = map_equity_price_books(
        _result("get_equity_price_book"),
        received_at=RECEIVED_AT,
        expected_symbols=("AAPL", "MSFT"),
    )
    tradability = map_equity_tradability(
        _result("get_equity_tradability"),
        received_at=RECEIVED_AT,
        expected_symbols=("AAPL", "MSFT"),
    )
    historicals = map_equity_historicals(
        _result("get_equity_historicals"),
        received_at=RECEIVED_AT,
        expected_symbols=("AAPL", "MSFT"),
        expected_interval=HistoricalInterval.MINUTE_5,
        expected_bounds=HistoricalBounds.REGULAR,
    )
    fundamentals = map_equity_fundamentals(
        _result("get_equity_fundamentals"),
        received_at=RECEIVED_AT,
        expected_symbols=("AAPL", "MSFT"),
    )
    financials = map_financials(
        _result("get_financials"),
        received_at=RECEIVED_AT,
        expected_symbols=("AAPL", "MSFT"),
        expected_period=ReportingPeriod.QUARTERLY,
    )
    orders = map_closed_equity_orders(
        _part2_result("get_equity_orders"),
        received_at=RECEIVED_AT,
        expected_symbol="AAPL",
    )

    assert books.books[0].bids[0].price == Decimal("210.1")
    assert books.errors[0].error.value == "Book unavailable"
    assert tradability.account_binding is AccountBinding.UNVERIFIED
    assert tradability.session_evidence is SessionEvidence.UNVERIFIED
    assert tradability.tradabilities[0].instrument.identity_verified is False
    assert historicals.session_evidence is SessionEvidence.UNVERIFIED
    assert historicals.series[0].bars[1].interpolated is True
    units = {fact.key: fact.unit for fact in fundamentals.fundamentals[0].snapshot.facts}
    assert units["volume"] == NormalizedUnit.SHARES
    assert units["market_cap"] == NormalizedUnit.USD
    assert units["open"] == NormalizedUnit.UNSPECIFIED
    financial_units = {
        fact.key: (fact.unit, fact.comparable)
        for fact in financials.series[0].financials[0].metrics
    }
    assert financial_units["revenue"] == (NormalizedUnit.UNSPECIFIED, False)
    assert financial_units["net_margin"] == (NormalizedUnit.PERCENT, True)
    assert financials.unavailable_symbols == ("MSFT",)
    assert len(orders.closed_orders) == 2 and orders.has_more is True
    assert len(orders.closed_orders[0].executions) == 2
    assert orders.closed_orders[0].instrument.identity_verified is False
    assert orders.account_binding is AccountBinding.UNVERIFIED
    for normalized in (books, tradability, historicals, fundamentals, financials, orders):
        assert normalized.evidence.result_digest == DIGEST_C
        assert "guide" not in normalized.model_dump_json()


@pytest.mark.unit
def test_untrusted_text_is_bounded_or_replaced_with_a_visible_marker() -> None:
    raw = _provider_result("get_equity_fundamentals")
    raw["data"]["results"][0]["description"] = "do this\nsecret"
    raw["data"]["results"][0]["ceo"] = "x" * 513

    mapped = map_equity_fundamentals(
        _result("get_equity_fundamentals", raw),
        received_at=RECEIVED_AT,
        expected_symbols=("AAPL", "MSFT"),
    )

    display = {item.field: item.text.value for item in mapped.fundamentals[0].display_text}
    assert display["description"] == UNAVAILABLE_UNTRUSTED_TEXT
    assert display["ceo"] == UNAVAILABLE_UNTRUSTED_TEXT
    assert mapped.omitted_untrusted_fields == (
        "results[0].description",
        "results[0].ceo",
    )
    rendered = mapped.model_dump_json()
    assert "do this" not in rendered and "x" * 513 not in rendered


def _duplicate_financial_identity(value: dict[str, Any]) -> None:
    prior = value["data"]["results"][0]["financials"][1]
    prior.update(fiscal_year=2026, fiscal_quarter=2)


def _make_partially_filled_order_labeled_filled(value: dict[str, Any]) -> None:
    order = value["data"]["orders"][0]
    order["cumulative_quantity"] = "1"
    order["executions"] = [order["executions"][0]]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("capability", "mutate", "mapper", "kwargs"),
    [
        (
            "get_equity_price_book",
            lambda value: value["data"]["books"][0]["bids"].reverse(),
            map_equity_price_books,
            {"expected_symbols": ("AAPL", "MSFT")},
        ),
        (
            "get_equity_price_book",
            lambda value: value["data"]["books"][0]["bids"][0].update(price="220.00"),
            map_equity_price_books,
            {"expected_symbols": ("AAPL", "MSFT")},
        ),
        (
            "get_equity_price_book",
            lambda value: value["data"]["books"][0]["asks"][0].update(quantity=0),
            map_equity_price_books,
            {"expected_symbols": ("AAPL", "MSFT")},
        ),
        (
            "get_equity_tradability",
            lambda value: value["data"]["results"][0].update(fractional_tradability="mystery"),
            map_equity_tradability,
            {"expected_symbols": ("AAPL", "MSFT")},
        ),
        (
            "get_equity_historicals",
            lambda value: value["data"]["results"][0].update(interval="mystery"),
            map_equity_historicals,
            {"expected_symbols": ("AAPL", "MSFT")},
        ),
        (
            "get_equity_historicals",
            lambda value: value["data"]["results"][0]["bars"][0].update(high_price="209.00"),
            map_equity_historicals,
            {"expected_symbols": ("AAPL", "MSFT")},
        ),
        (
            "get_equity_fundamentals",
            lambda value: value["data"]["results"][0].update(volume="-1"),
            map_equity_fundamentals,
            {"expected_symbols": ("AAPL", "MSFT")},
        ),
        (
            "get_equity_fundamentals",
            lambda value: value["data"]["results"][0].update(bounds="24_7"),
            map_equity_fundamentals,
            {"expected_symbols": ("AAPL", "MSFT")},
        ),
        (
            "get_financials",
            lambda value: value["data"]["results"][0]["financials"].reverse(),
            map_financials,
            {"expected_symbols": ("AAPL", "MSFT")},
        ),
        (
            "get_financials",
            lambda value: value["data"]["results"][0]["financials"][0].update(fiscal_quarter=None),
            map_financials,
            {"expected_symbols": ("AAPL", "MSFT")},
        ),
        (
            "get_financials",
            _duplicate_financial_identity,
            map_financials,
            {"expected_symbols": ("AAPL", "MSFT")},
        ),
        (
            "get_financials",
            lambda value: value["data"]["results"][0]["financials"][0].update(fiscal_year=2020),
            map_financials,
            {"expected_symbols": ("AAPL", "MSFT")},
        ),
        (
            "get_equity_orders",
            lambda value: value["data"]["orders"][0]["executions"][0].update(
                timestamp="2026-08-07T14:59:59Z"
            ),
            map_closed_equity_orders,
            {"expected_symbol": "AAPL"},
        ),
        (
            "get_equity_orders",
            lambda value: value["data"]["orders"][0]["executions"][1].update(id="execution-1"),
            map_closed_equity_orders,
            {"expected_symbol": "AAPL"},
        ),
        (
            "get_equity_orders",
            lambda value: value["data"]["orders"][0].update(cumulative_quantity="1"),
            map_closed_equity_orders,
            {"expected_symbol": "AAPL"},
        ),
        (
            "get_equity_orders",
            lambda value: value["data"]["orders"][0].update(executions=None),
            map_closed_equity_orders,
            {"expected_symbol": "AAPL"},
        ),
        (
            "get_equity_orders",
            _make_partially_filled_order_labeled_filled,
            map_closed_equity_orders,
            {"expected_symbol": "AAPL"},
        ),
    ],
)
def test_material_part2_inconsistencies_fail_closed(
    capability: str,
    mutate: Any,
    mapper: Any,
    kwargs: dict[str, Any],
) -> None:
    raw = (
        _part2_provider_result(capability)
        if capability == "get_equity_orders"
        else _provider_result(capability)
    )
    mutate(raw)
    with pytest.raises(RobinhoodMappingError):
        mapper(_result(capability, raw), received_at=RECEIVED_AT, **kwargs)


@pytest.mark.unit
def test_part2_rejects_symbol_identity_and_request_mismatches() -> None:
    with pytest.raises(RobinhoodMappingError) as caught:
        map_equity_price_books(
            _result("get_equity_price_book"),
            received_at=RECEIVED_AT,
            expected_symbols=("AAPL",),
        )
    assert caught.value.code is MappingErrorCode.INCONSISTENT_DATA

    order = _part2_provider_result("get_equity_orders")
    order["data"]["orders"][1]["instrument_id"] = "instrument-other-999"
    with pytest.raises(RobinhoodMappingError) as caught:
        map_closed_equity_orders(
            _part2_result("get_equity_orders", order),
            received_at=RECEIVED_AT,
        )
    assert caught.value.code is MappingErrorCode.INCONSISTENT_DATA
