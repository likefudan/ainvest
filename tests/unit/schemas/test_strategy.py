"""Unit tests for portfolio and strategy schemas (P02-T2)."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from portfolio_fixtures import make_cash_portfolio, make_instrument, make_open_order
from pydantic import ValidationError

from ainvest.schemas.portfolio import (
    AccountScope,
    PortfolioSnapshot,
)
from ainvest.schemas.research import research_packet_example
from ainvest.schemas.strategy import (
    SignalIntent,
    StrategyContext,
    StrategyStateItem,
    TradeSignal,
    parse_strategy_context,
    parse_trade_signal,
    parse_trade_signal_for_context,
    trade_signal_example,
)

EVAL_AS_OF = "2026-07-24T18:45:00Z"


def _instrument(*, identity_as_of: str = "2026-07-24T18:30:00Z") -> dict[str, Any]:
    return {
        "instrument_id": "rh_inst_aapl_xnas",
        "symbol": "AAPL",
        "exchange": "XNAS",
        "currency": "USD",
        "asset_type": "EQUITY",
        "identity_as_of": identity_as_of,
    }


def _portfolio_example() -> dict[str, Any]:
    as_of = "2026-07-24T18:30:00Z"
    # 2154.20 / 5154.20 ≈ 0.417951...; stored weight must be within WEIGHT_TOLERANCE.
    return {
        "schema_version": "1.0",
        "snapshot_id": "port_01HZYEXAMPLE0001",
        "account_scope": "paper",
        "as_of": as_of,
        "currency": "USD",
        "cash": "3000.00",
        "buying_power": "3000.00",
        "equity": "5154.20",
        "positions": [
            {
                "instrument": _instrument(),
                "quantity": "10",
                "market_value": "2154.20",
                "portfolio_weight": "0.4180",
                "average_cost": "200.00",
                "unrealized_pnl": "154.20",
                "currency": "USD",
            }
        ],
        "open_orders": [],
        "exposure": {
            "cash": "3000.00",
            "equity": "5154.20",
            "gross_market_value": "2154.20",
            "net_market_value": "2154.20",
            "largest_position_weight": "0.4180",
            "position_count": 1,
        },
        "provenance": {
            "source": "robinhood.mcp.portfolio",
            "observed_at": "2026-07-24T18:29:58Z",
            "received_at": as_of,
            "timezone": "UTC",
            "is_delayed": False,
            "quality_flags": [],
        },
    }


def _context_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "as_of": "2026-07-24T18:30:00Z",
        "research": research_packet_example(),
        "portfolio": _portfolio_example(),
        "strategy_state": {
            "strategy": "sma_crossover",
            "strategy_version": "1.2.0",
            "updated_at": "2026-07-24T18:00:00Z",
            "entries": [
                {
                    "key": "last_cross",
                    "kind": "TEXT",
                    "text_value": "bullish",
                }
            ],
        },
    }


@pytest.mark.unit
def test_design_trade_signal_example_validates() -> None:
    signal = parse_trade_signal(trade_signal_example(), as_of=EVAL_AS_OF)
    assert signal.intent is SignalIntent.BUY
    assert signal.strength == Decimal("0.73")
    assert signal.target_weight == Decimal("0.10")
    assert signal.may_become_order()
    dumped = json.loads(signal.model_dump_json())
    assert dumped["strength"] == "0.73"
    assert dumped["generated_at"].endswith("Z")


@pytest.mark.unit
def test_strategy_context_is_immutable() -> None:
    context = parse_strategy_context(_context_example())
    assert context.symbol == "AAPL"
    assert context.portfolio.account_scope is AccountScope.PAPER
    with pytest.raises((ValidationError, TypeError)):
        context.as_of = datetime(2026, 7, 24, 19, 0, tzinfo=UTC)
    with pytest.raises((ValidationError, TypeError)):
        context.portfolio.cash = Decimal("1.00")


@pytest.mark.unit
def test_rejects_future_research_or_portfolio_in_context() -> None:
    payload = _context_example()
    research = payload["research"]
    assert isinstance(research, dict)
    research["as_of"] = "2026-07-24T19:00:00Z"
    market = research["market"]
    assert isinstance(market, dict)
    provenance = market["provenance"]
    assert isinstance(provenance, dict)
    provenance["observed_at"] = "2026-07-24T18:59:00Z"
    provenance["received_at"] = "2026-07-24T19:00:00Z"
    market["observed_at"] = "2026-07-24T18:59:00Z"
    with pytest.raises(ValidationError, match=r"research\.as_of"):
        parse_strategy_context(payload)

    payload = _context_example()
    portfolio = payload["portfolio"]
    assert isinstance(portfolio, dict)
    portfolio["as_of"] = "2026-07-24T19:00:00Z"
    port_prov = portfolio["provenance"]
    assert isinstance(port_prov, dict)
    port_prov["observed_at"] = "2026-07-24T18:59:00Z"
    port_prov["received_at"] = "2026-07-24T19:00:00Z"
    with pytest.raises(ValidationError, match=r"portfolio\.as_of"):
        parse_strategy_context(payload)


@pytest.mark.unit
def test_rejects_invalid_strength_and_missing_strategy_version() -> None:
    payload = trade_signal_example()
    payload["strength"] = "1.01"
    with pytest.raises(ValidationError):
        parse_trade_signal(payload, as_of=EVAL_AS_OF)

    payload = trade_signal_example()
    payload["strategy_version"] = ""
    with pytest.raises(ValidationError):
        parse_trade_signal(payload, as_of=EVAL_AS_OF)

    payload = trade_signal_example()
    del payload["strategy_version"]
    with pytest.raises(ValidationError):
        parse_trade_signal(payload, as_of=EVAL_AS_OF)


@pytest.mark.unit
def test_parse_trade_signal_rejects_future_and_expired_relative_to_as_of() -> None:
    payload = trade_signal_example()
    payload["expires_at"] = payload["generated_at"]
    with pytest.raises(ValidationError, match="expires_at"):
        TradeSignal.model_validate(payload)

    future = trade_signal_example()
    future["generated_at"] = "2099-01-01T00:00:00Z"
    future["expires_at"] = "2099-01-01T00:30:00Z"
    with pytest.raises(ValueError, match="future"):
        parse_trade_signal(future, as_of=EVAL_AS_OF)

    expired = trade_signal_example()
    with pytest.raises(ValueError, match="expired"):
        parse_trade_signal(expired, as_of="2026-07-24T19:00:10Z")

    active = parse_trade_signal(trade_signal_example(), as_of=EVAL_AS_OF)
    assert active.intent is SignalIntent.BUY

    context = parse_strategy_context(_context_example())
    # Context as_of equals generated_at in the example research clock window.
    # Use a signal generated at/before context.as_of.
    signal_payload = trade_signal_example()
    signal_payload["generated_at"] = "2026-07-24T18:30:00Z"
    signal_payload["expires_at"] = "2026-07-24T19:00:00Z"
    bound = parse_trade_signal_for_context(signal_payload, context)
    assert bound.research_id == context.research.research_id


@pytest.mark.unit
def test_parse_trade_signal_for_context_rejects_early_or_mismatched_strategy() -> None:
    context = parse_strategy_context(_context_example())

    early = trade_signal_example()
    early["generated_at"] = "2026-07-24T17:00:00Z"
    early["expires_at"] = "2026-07-24T19:00:00Z"
    with pytest.raises(ValueError, match="generated_at"):
        parse_trade_signal_for_context(early, context)

    mismatched = trade_signal_example()
    mismatched["generated_at"] = "2026-07-24T18:30:00Z"
    mismatched["expires_at"] = "2026-07-24T19:00:00Z"
    mismatched["strategy"] = "other_strategy"
    mismatched["strategy_version"] = "9.9.9"
    with pytest.raises(ValueError, match="strategy"):
        parse_trade_signal_for_context(mismatched, context)


@pytest.mark.unit
def test_hold_cannot_become_order_or_carry_target_weight() -> None:
    payload = trade_signal_example()
    payload["intent"] = "HOLD"
    payload["target_weight"] = "0.10"
    payload["reason_codes"] = ["NO_EDGE"]
    with pytest.raises(ValidationError, match="HOLD"):
        parse_trade_signal(payload, as_of=EVAL_AS_OF)

    payload = trade_signal_example()
    payload["intent"] = "HOLD"
    payload["target_weight"] = None
    payload["strength"] = "0"
    payload["reason_codes"] = ["NO_EDGE"]
    signal = parse_trade_signal(payload, as_of=EVAL_AS_OF)
    assert not signal.may_become_order()


@pytest.mark.unit
def test_portfolio_reconciles_derived_exposure_values() -> None:
    payload = _portfolio_example()
    exposure = payload["exposure"]
    assert isinstance(exposure, dict)
    exposure["cash"] = "1.00"
    with pytest.raises(ValidationError, match=r"exposure\.cash"):
        PortfolioSnapshot.model_validate(payload)

    payload = _portfolio_example()
    exposure = payload["exposure"]
    assert isinstance(exposure, dict)
    exposure["gross_market_value"] = "999999"
    with pytest.raises(ValidationError, match="gross_market_value"):
        PortfolioSnapshot.model_validate(payload)

    payload = _portfolio_example()
    exposure = payload["exposure"]
    assert isinstance(exposure, dict)
    exposure["net_market_value"] = "0"
    with pytest.raises(ValidationError, match="net_market_value"):
        PortfolioSnapshot.model_validate(payload)

    payload = _portfolio_example()
    exposure = payload["exposure"]
    assert isinstance(exposure, dict)
    exposure["largest_position_weight"] = "0.99"
    with pytest.raises(ValidationError, match="largest_position_weight"):
        PortfolioSnapshot.model_validate(payload)

    payload = _portfolio_example()
    positions = payload["positions"]
    assert isinstance(positions, list)
    position = positions[0]
    assert isinstance(position, dict)
    position["portfolio_weight"] = "0.0800"
    exposure = payload["exposure"]
    assert isinstance(exposure, dict)
    exposure["largest_position_weight"] = "0.0800"
    with pytest.raises(ValidationError, match="portfolio_weight"):
        PortfolioSnapshot.model_validate(payload)

    empty = make_cash_portfolio(cash="100.00").model_dump(mode="python")
    assert PortfolioSnapshot.model_validate(empty).exposure.position_count == 0


@pytest.mark.unit
def test_portfolio_rejects_future_instrument_identity_and_duplicate_orders() -> None:
    payload = _portfolio_example()
    positions = payload["positions"]
    assert isinstance(positions, list)
    position = positions[0]
    assert isinstance(position, dict)
    position["instrument"] = make_instrument(identity_as_of="2099-01-01T00:00:00Z")
    with pytest.raises(ValidationError, match="identity_as_of"):
        PortfolioSnapshot.model_validate(payload)

    payload = _portfolio_example()
    order = make_open_order(
        order_id="ord_open_1",
        side="BUY",
        quantity="1",
        limit_price="214.50",
        instrument=make_instrument(identity_as_of="2099-01-01T00:00:00Z"),
        submitted_at="2026-07-24T18:00:00Z",
    )
    payload["open_orders"] = [order]
    with pytest.raises(ValidationError, match="identity_as_of"):
        PortfolioSnapshot.model_validate(payload)

    payload = _portfolio_example()
    good_order = make_open_order(
        order_id="ord_open_1",
        side="BUY",
        quantity="1",
        limit_price="214.50",
        submitted_at="2026-07-24T18:00:00Z",
    )
    payload["open_orders"] = [good_order, {**good_order}]
    with pytest.raises(ValidationError, match="order_id"):
        PortfolioSnapshot.model_validate(payload)

    payload = _portfolio_example()
    payload["open_orders"] = [
        make_open_order(
            order_id="ord_open_1",
            side="BUY",
            quantity="1",
            limit_price="214.50",
            submitted_at="2026-07-24T19:00:00Z",
        )
    ]
    with pytest.raises(ValidationError, match="submitted_at"):
        PortfolioSnapshot.model_validate(payload)


@pytest.mark.unit
def test_paper_buying_power_cannot_exceed_equity() -> None:
    payload = _portfolio_example()
    payload["buying_power"] = "6000.00"
    with pytest.raises(ValidationError, match="buying_power"):
        PortfolioSnapshot.model_validate(payload)


@pytest.mark.unit
def test_strategy_state_rejects_cross_type_scalar_coercion() -> None:
    with pytest.raises(ValidationError):
        StrategyStateItem.model_validate(
            {
                "key": "flag_count",
                "kind": "INTEGER",
                "integer_value": True,
            }
        )
    with pytest.raises(ValidationError):
        StrategyStateItem.model_validate(
            {
                "key": "enabled",
                "kind": "BOOLEAN",
                "boolean_value": 1,
            }
        )
    with pytest.raises(ValidationError):
        StrategyStateItem.model_validate(
            {
                "key": "enabled",
                "kind": "BOOLEAN",
                "boolean_value": "true",
            }
        )
    item = StrategyStateItem.model_validate(
        {
            "key": "flag_count",
            "kind": "INTEGER",
            "integer_value": 2,
        }
    )
    assert item.integer_value == 2


@pytest.mark.unit
def test_strategy_context_rejects_future_strategy_state() -> None:
    payload = _context_example()
    state = payload["strategy_state"]
    assert isinstance(state, dict)
    state["updated_at"] = "2026-07-24T19:00:00Z"
    with pytest.raises(ValidationError, match=r"strategy_state\.updated_at"):
        parse_strategy_context(payload)


@pytest.mark.unit
def test_trade_signal_json_schema_documents_strength_bounds() -> None:
    schema = TradeSignal.model_json_schema()
    assert schema["title"] == "TradeSignal"
    strength = schema["properties"]["strength"]
    assert strength["type"] == "string"
    context_schema = StrategyContext.model_json_schema()
    assert "research" in context_schema["properties"]
    assert "portfolio" in context_schema["properties"]


@pytest.mark.unit
def test_context_and_signal_round_trip() -> None:
    context = parse_strategy_context(_context_example())
    raw = json.loads(context.model_dump_json())
    again = StrategyContext.model_validate(raw)
    assert again.research.research_id == context.research.research_id
    signal = parse_trade_signal(deepcopy(trade_signal_example()), as_of=EVAL_AS_OF)
    assert json.loads(signal.model_dump_json())["strategy_version"] == "1.2.0"
