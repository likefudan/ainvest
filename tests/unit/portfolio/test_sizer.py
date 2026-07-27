"""Unit and property tests for the single-strategy Position Sizer (P03-T6)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from ainvest.portfolio import (
    SizerReasonCode,
    SizingConfig,
    ceil_to_increment,
    floor_to_increment,
    normalize_limit_price,
    size_position,
)
from ainvest.schemas.common import OrderSide, parse_decimal
from ainvest.schemas.examples import (
    market_quote_example,
    portfolio_snapshot_example,
)
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.strategy import TradeSignal, trade_signal_example

AS_OF = datetime(2026, 7, 24, 18, 30, 10, tzinfo=UTC)
CANDIDATE_ID = "cand_01HZYSIZER000001"


def _signal(**overrides: Any) -> TradeSignal:
    payload = trade_signal_example()
    payload.update(overrides)
    return TradeSignal.model_validate(payload)


def _quote(**overrides: Any) -> MarketQuote:
    payload = market_quote_example()
    payload.update(overrides)
    return MarketQuote.model_validate(payload)


def _portfolio(**overrides: Any) -> PortfolioSnapshot:
    payload = portfolio_snapshot_example()
    payload.update(overrides)
    return PortfolioSnapshot.model_validate(payload)


def _config(**overrides: Any) -> SizingConfig:
    base = {
        "quantity_increment": "1",
        "price_increment": "0.01",
        "min_notional": "1.00",
        "max_notional": "5000.00",
        "cash_reserve": "100.00",
        "candidate_ttl_seconds": 120,
    }
    base.update(overrides)
    return SizingConfig.model_validate(base)


def _empty_portfolio() -> PortfolioSnapshot:
    """Cash-only portfolio so target-weight buys have a clear delta."""
    payload = portfolio_snapshot_example()
    payload["cash"] = "5000.00"
    payload["buying_power"] = "5000.00"
    payload["equity"] = "5000.00"
    payload["positions"] = []
    payload["exposure"] = {
        "cash": "5000.00",
        "equity": "5000.00",
        "gross_market_value": "0",
        "net_market_value": "0",
        "largest_position_weight": "0",
        "position_count": 0,
    }
    return PortfolioSnapshot.model_validate(payload)


@pytest.mark.unit
def test_buy_sizes_whole_shares_within_limits() -> None:
    result = size_position(
        signal=_signal(intent="BUY", target_weight="0.10"),
        quote=_quote(last_price="214.50", ask="214.52", bid="214.48"),
        portfolio=_empty_portfolio(),
        config=_config(),
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    assert result.reason_code == SizerReasonCode.SIZED_TO_TARGET_WEIGHT
    assert result.candidate is not None
    candidate = result.candidate
    assert candidate.side is OrderSide.BUY
    assert candidate.quantity == Decimal("2")
    assert candidate.limit_price == Decimal("214.52")
    assert candidate.quantity % candidate.quantity_increment == 0
    assert candidate.limit_price % candidate.price_increment == 0
    assert candidate.quantity * candidate.limit_price <= candidate.maximum_notional
    assert candidate.quantity * candidate.limit_price <= Decimal("5000.00")


@pytest.mark.unit
def test_hold_returns_stable_reason_without_candidate() -> None:
    result = size_position(
        signal=_signal(intent="HOLD", target_weight=None, reason_codes=["NO_CROSS"]),
        quote=_quote(),
        portfolio=_empty_portfolio(),
        config=_config(),
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    assert result.candidate is None
    assert result.reason_code == SizerReasonCode.HOLD_SIGNAL


@pytest.mark.unit
def test_expired_signal_rejected() -> None:
    result = size_position(
        signal=_signal(),
        quote=_quote(),
        portfolio=_empty_portfolio(),
        config=_config(),
        as_of=datetime(2026, 7, 24, 19, 0, 10, tzinfo=UTC),
        candidate_id=CANDIDATE_ID,
    )
    assert result.candidate is None
    assert result.reason_code == SizerReasonCode.SIGNAL_EXPIRED


@pytest.mark.unit
def test_non_positive_buying_power_rejected() -> None:
    """BUY must fail closed when buying power is zero; SELL is tested separately."""
    payload = portfolio_snapshot_example()
    payload["cash"] = "100.00"
    payload["buying_power"] = "0"
    payload["equity"] = "100.00"
    payload["positions"] = []
    payload["exposure"] = {
        "cash": "100.00",
        "equity": "100.00",
        "gross_market_value": "0",
        "net_market_value": "0",
        "largest_position_weight": "0",
        "position_count": 0,
    }
    result = size_position(
        signal=_signal(intent="BUY", target_weight="0.50"),
        quote=_quote(),
        portfolio=PortfolioSnapshot.model_validate(payload),
        config=_config(cash_reserve="0"),
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    assert result.candidate is None
    assert result.reason_code == SizerReasonCode.NON_POSITIVE_BUYING_POWER


@pytest.mark.unit
def test_cash_reserve_can_block_buy() -> None:
    portfolio = _empty_portfolio()
    result = size_position(
        signal=_signal(intent="BUY", target_weight="0.10"),
        quote=_quote(last_price="214.50"),
        portfolio=portfolio,
        config=_config(cash_reserve="5000.00", min_notional="1.00"),
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    assert result.candidate is None
    assert result.reason_code == SizerReasonCode.CASH_RESERVE_BLOCKS_BUY


@pytest.mark.unit
def test_sell_floors_quantity_and_ceils_price() -> None:
    # Target weight below current (~0.418) with SELL intent.
    result = size_position(
        signal=_signal(intent="SELL", target_weight="0.20", strength="-0.50"),
        quote=_quote(last_price="214.505", bid="214.501", ask="214.51"),
        portfolio=_portfolio(),
        config=_config(min_notional="1.00"),
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    assert result.reason_code == SizerReasonCode.SIZED_TO_TARGET_WEIGHT
    assert result.candidate is not None
    assert result.candidate.side is OrderSide.SELL
    # Bid 214.501 ceils to 214.51 (safe: never below reference).
    assert result.candidate.limit_price == Decimal("214.51")
    assert result.candidate.quantity == floor_to_increment(result.candidate.quantity, Decimal("1"))
    assert result.candidate.quantity >= 1


@pytest.mark.unit
def test_symbol_mismatch_rejected() -> None:
    quote_payload = market_quote_example()
    quote_payload["instrument"]["symbol"] = "MSFT"
    quote_payload["instrument"]["instrument_id"] = "rh_inst_msft_xnas"
    result = size_position(
        signal=_signal(),
        quote=MarketQuote.model_validate(quote_payload),
        portfolio=_empty_portfolio(),
        config=_config(),
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    assert result.reason_code == SizerReasonCode.SYMBOL_MISMATCH
    assert result.candidate is None


@pytest.mark.unit
def test_below_min_notional_rejected() -> None:
    # One whole share is affordable, but below the configured minimum notional.
    result = size_position(
        signal=_signal(intent="BUY", target_weight="0.05"),
        quote=_quote(last_price="214.50"),
        portfolio=_empty_portfolio(),
        config=_config(min_notional="1000.00", cash_reserve="0"),
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    assert result.candidate is None
    assert result.reason_code == SizerReasonCode.BELOW_MIN_NOTIONAL


@pytest.mark.unit
def test_sizing_is_deterministic() -> None:
    signal = _signal(intent="BUY", target_weight="0.10")
    quote = _quote(last_price="214.50", ask="214.52", bid="214.48")
    portfolio = _empty_portfolio()
    config = _config()
    first = size_position(
        signal=signal,
        quote=quote,
        portfolio=portfolio,
        config=config,
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    second = size_position(
        signal=signal,
        quote=quote,
        portfolio=portfolio,
        config=config,
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    assert first.model_dump() == second.model_dump()


@pytest.mark.unit
def test_sell_allowed_when_buying_power_is_zero() -> None:
    """Fully invested accounts must still be able to reduce a position."""
    payload = portfolio_snapshot_example()
    market_value = parse_decimal(payload["positions"][0]["market_value"])
    payload["cash"] = "0"
    payload["buying_power"] = "0"
    payload["equity"] = str(market_value)
    payload["positions"][0]["portfolio_weight"] = "1"
    payload["exposure"] = {
        "cash": "0",
        "equity": str(market_value),
        "gross_market_value": str(market_value),
        "net_market_value": str(market_value),
        "largest_position_weight": "1",
        "position_count": 1,
    }
    result = size_position(
        signal=_signal(intent="SELL", target_weight="0.20", strength="-0.50"),
        quote=_quote(),
        portfolio=PortfolioSnapshot.model_validate(payload),
        config=_config(cash_reserve="0", min_notional="1.00"),
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    assert result.reason_code == SizerReasonCode.SIZED_TO_TARGET_WEIGHT
    assert result.candidate is not None
    assert result.candidate.side is OrderSide.SELL
    assert result.candidate.quantity >= 1


@pytest.mark.unit
def test_open_sell_orders_reduce_sellable_quantity() -> None:
    payload = portfolio_snapshot_example()  # 10 AAPL
    payload["open_orders"] = [
        {
            "order_id": "ord_pending_sell_aapl",
            "instrument": payload["positions"][0]["instrument"],
            "side": "SELL",
            "quantity": "8",
            "submitted_at": "2026-07-24T18:29:00Z",
            "limit_price": "214.50",
            "symbol": "AAPL",
        }
    ]
    result = size_position(
        signal=_signal(intent="SELL", target_weight="0", strength="-1"),
        quote=_quote(),
        portfolio=PortfolioSnapshot.model_validate(payload),
        config=_config(cash_reserve="0", min_notional="1.00"),
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    assert result.reason_code == SizerReasonCode.SIZED_TO_TARGET_WEIGHT
    assert result.candidate is not None
    # 10 filled - 8 open sell => at most 2 more shares may be sold.
    assert result.candidate.quantity == Decimal("2")


@pytest.mark.unit
def test_open_buy_orders_reduce_spendable_buying_power() -> None:
    """Open BUY notionals reserve cash even for a different symbol."""
    portfolio = _empty_portfolio()
    payload = portfolio.model_dump(mode="python")
    payload["open_orders"] = [
        {
            "order_id": "ord_pending_buy_msft",
            "instrument": {
                "instrument_id": "rh_inst_msft_xnas",
                "symbol": "MSFT",
                "exchange": "XNAS",
                "currency": "USD",
                "asset_type": "EQUITY",
                "identity_as_of": "2026-07-24T18:30:00Z",
            },
            "side": "BUY",
            "quantity": "20",
            "submitted_at": "2026-07-24T18:29:00Z",
            "limit_price": "214.50",
            "symbol": "MSFT",
        }
    ]
    result = size_position(
        signal=_signal(intent="BUY", target_weight="0.50"),
        quote=_quote(last_price="214.50", ask="214.50", bid="214.40"),
        portfolio=PortfolioSnapshot.model_validate(payload),
        config=_config(cash_reserve="0", max_notional="5000.00", min_notional="1.00"),
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    assert result.reason_code == SizerReasonCode.SIZED_TO_TARGET_WEIGHT
    assert result.candidate is not None
    # 20*214.50=4290 reserved from 5000 => <=710 spendable => at most 3 shares.
    assert result.candidate.quantity <= Decimal("3")
    assert result.candidate.quantity * result.candidate.limit_price <= Decimal("710.00")


@pytest.mark.unit
def test_open_buy_without_limit_fails_closed() -> None:
    portfolio = _empty_portfolio()
    payload = portfolio.model_dump(mode="python")
    payload["open_orders"] = [
        {
            "order_id": "ord_pending_buy_no_limit",
            "instrument": {
                "instrument_id": "rh_inst_msft_xnas",
                "symbol": "MSFT",
                "exchange": "XNAS",
                "currency": "USD",
                "asset_type": "EQUITY",
                "identity_as_of": "2026-07-24T18:30:00Z",
            },
            "side": "BUY",
            "quantity": "5",
            "submitted_at": "2026-07-24T18:29:00Z",
            "symbol": "MSFT",
        }
    ]
    result = size_position(
        signal=_signal(intent="BUY", target_weight="0.10"),
        quote=_quote(),
        portfolio=PortfolioSnapshot.model_validate(payload),
        config=_config(cash_reserve="0"),
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    assert result.candidate is None
    assert result.reason_code == SizerReasonCode.OPEN_BUY_MISSING_LIMIT


@pytest.mark.unit
def test_config_rejects_inverted_notional_bounds() -> None:
    with pytest.raises(ValidationError, match="min_notional"):
        _config(min_notional="100.00", max_notional="10.00")


@pytest.mark.unit
def test_buy_limit_never_rounds_up() -> None:
    price = normalize_limit_price(Decimal("214.559"), Decimal("0.01"), side=OrderSide.BUY)
    assert price == Decimal("214.55")
    assert price <= Decimal("214.559")


@pytest.mark.unit
def test_sell_limit_never_rounds_down() -> None:
    price = normalize_limit_price(Decimal("214.551"), Decimal("0.01"), side=OrderSide.SELL)
    assert price == Decimal("214.56")
    assert price >= Decimal("214.551")


@given(
    value=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("100000"),
        places=4,
        allow_nan=False,
        allow_infinity=False,
    ),
    increment=st.sampled_from(
        [Decimal("0.01"), Decimal("0.05"), Decimal("0.10"), Decimal("1"), Decimal("0.001")]
    ),
)
@settings(max_examples=80)
def test_property_floor_never_exceeds_and_on_increment(value: Decimal, increment: Decimal) -> None:
    floored = floor_to_increment(parse_decimal(value), parse_decimal(increment))
    assert floored <= parse_decimal(value)
    assert floored >= Decimal("0")
    # Exact multiple: ratio must be integral under integer scaling.
    if floored > 0:
        ratio = floored / parse_decimal(increment)
        assert ratio == ratio.to_integral_value()


@given(
    value=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("100000"),
        places=4,
        allow_nan=False,
        allow_infinity=False,
    ),
    increment=st.sampled_from(
        [Decimal("0.01"), Decimal("0.05"), Decimal("0.10"), Decimal("1"), Decimal("0.001")]
    ),
)
@settings(max_examples=80)
def test_property_ceil_never_below_and_on_increment(value: Decimal, increment: Decimal) -> None:
    ceiled = ceil_to_increment(parse_decimal(value), parse_decimal(increment))
    assert ceiled >= parse_decimal(value)
    ratio = ceiled / parse_decimal(increment)
    assert ratio == ratio.to_integral_value()


@given(
    target_weight=st.sampled_from(
        [Decimal("0.05"), Decimal("0.10"), Decimal("0.15"), Decimal("0.25")]
    ),
    last_price=st.sampled_from(
        [Decimal("50.00"), Decimal("99.99"), Decimal("214.50"), Decimal("500.25")]
    ),
    equity=st.sampled_from([Decimal("2000.00"), Decimal("5000.00"), Decimal("10000.00")]),
)
@settings(max_examples=40)
def test_property_buy_never_exceeds_max_notional_or_buying_power(
    target_weight: Decimal,
    last_price: Decimal,
    equity: Decimal,
) -> None:
    payload = portfolio_snapshot_example()
    payload["cash"] = str(equity)
    payload["buying_power"] = str(equity)
    payload["equity"] = str(equity)
    payload["positions"] = []
    payload["exposure"] = {
        "cash": str(equity),
        "equity": str(equity),
        "gross_market_value": "0",
        "net_market_value": "0",
        "largest_position_weight": "0",
        "position_count": 0,
    }
    portfolio = PortfolioSnapshot.model_validate(payload)
    config = _config(cash_reserve="0", max_notional="2500.00", min_notional="1.00")
    quote_payload = market_quote_example()
    quote_payload["last_price"] = str(last_price)
    quote_payload["ask"] = str(last_price)
    quote_payload["bid"] = str(last_price - Decimal("0.01"))
    result = size_position(
        signal=_signal(intent="BUY", target_weight=str(target_weight)),
        quote=MarketQuote.model_validate(quote_payload),
        portfolio=portfolio,
        config=config,
        as_of=AS_OF,
        candidate_id=CANDIDATE_ID,
    )
    if result.candidate is None:
        assert result.reason_code != SizerReasonCode.SIZED_TO_TARGET_WEIGHT
        return
    candidate = result.candidate
    notional = candidate.quantity * candidate.limit_price
    assert notional <= parse_decimal(config.max_notional)
    assert notional <= parse_decimal(portfolio.buying_power)
    # BUY must never round limit price above the ask/last reference.
    assert candidate.limit_price <= last_price


@given(
    price=st.decimals(
        min_value=Decimal("1.00"),
        max_value=Decimal("999.99"),
        places=3,
        allow_nan=False,
        allow_infinity=False,
    ),
    tick=st.sampled_from([Decimal("0.01"), Decimal("0.05"), Decimal("0.10")]),
)
@settings(max_examples=60)
def test_property_safe_price_rounding_directions(price: Decimal, tick: Decimal) -> None:
    buy = normalize_limit_price(parse_decimal(price), parse_decimal(tick), side=OrderSide.BUY)
    sell = normalize_limit_price(parse_decimal(price), parse_decimal(tick), side=OrderSide.SELL)
    assert buy <= parse_decimal(price)
    assert sell >= parse_decimal(price)
