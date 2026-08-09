"""Mapping tests for the first functional Robinhood normalized reads."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from ainvest.execution.robinhood.mappers import (
    MappingErrorCode,
    RobinhoodMappingError,
    map_accounts,
    map_equity_positions,
    map_equity_quotes,
    map_open_equity_orders,
    map_portfolio,
)
from ainvest.execution.robinhood.pins import EXPECTED_MANIFEST_DIGEST, PINNED_MANIFEST_VERSION
from ainvest.execution.robinhood.prose import discard_provider_prose
from ainvest.execution.robinhood.read_client import GatewayReadResult
from ainvest.execution.robinhood.read_models import QuoteIneligibility

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "rh_mcp" / "v0.2.0" / "p06-t1-part1"
OBSERVED_AT = "2026-08-08T15:00:02Z"
RECEIVED_AT = "2026-08-08T15:00:03Z"
DIGEST_B = f"sha256:{'b' * 64}"
DIGEST_C = f"sha256:{'c' * 64}"


def _provider_result(capability: str) -> dict[str, Any]:
    value = json.loads((FIXTURES / f"{capability}.json").read_text(encoding="utf-8"))
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
