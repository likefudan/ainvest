"""Focused invariants for the P06-T1 part 1 normalized read models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ainvest.execution.robinhood.read_models import (
    ClosedOrderRead,
    EquityQuoteRead,
    FinancialMetric,
    FinancialPeriodRead,
    FinancialSeriesRead,
    HistoricalBarRead,
    OpenOrderRead,
    PartialInstrumentReference,
    QuoteIneligibility,
    ReportingPeriod,
    UntrustedDisplayText,
)


@pytest.mark.unit
def test_quote_requires_live_eligibility_to_agree_with_reasons() -> None:
    base = {
        "symbol": "AAPL",
        "last_price": "210.10",
        "last_at": "2026-08-08T15:00:00Z",
        "bid": "210.05",
        "bid_at": "2026-08-08T15:00:01Z",
        "ask": "210.15",
        "ask_at": "2026-08-08T15:00:01Z",
        "has_traded": True,
        "listing_state": "active",
    }

    quote = EquityQuoteRead.model_validate({**base, "live_eligible": True})
    assert quote.last_price == Decimal("210.1")
    with pytest.raises(ValidationError, match="live eligibility"):
        EquityQuoteRead.model_validate({**base, "live_eligible": False, "ineligibility": ()})
    with pytest.raises(ValidationError, match="crossed quotes"):
        EquityQuoteRead.model_validate(
            {
                **base,
                "bid": "211",
                "ask": "210",
                "live_eligible": False,
                "ineligibility": (QuoteIneligibility.STALE,),
            }
        )


@pytest.mark.unit
def test_external_open_order_has_no_ainvest_proposal_or_hash_fields() -> None:
    order = OpenOrderRead.model_validate(
        {
            "order_id": "order-123",
            "instrument_id": "instrument-123",
            "symbol": "AAPL",
            "side": "BUY",
            "order_type": "limit",
            "state": "queued",
            "quantity": "2",
            "filled_quantity": "0",
            "limit_price": "205",
            "fees": "0",
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
            "placed_agent": "user",
            "created_at": "2026-08-08T14:50:00Z",
        }
    )

    payload = order.model_dump(mode="json")
    assert payload["quantity"] == "2"
    assert payload["created_at"].endswith("Z")
    assert {"proposal_id", "order_hash", "client_order_id"}.isdisjoint(payload)


@pytest.mark.unit
def test_open_order_rejects_closed_state_and_incoherent_amounts() -> None:
    base = {
        "order_id": "order-123",
        "instrument_id": "instrument-123",
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "market",
        "state": "queued",
        "filled_quantity": "0",
        "fees": "0",
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "placed_agent": "user",
        "created_at": "2026-08-08T14:50:00Z",
    }
    with pytest.raises(ValidationError, match="either share quantity or dollar amount"):
        OpenOrderRead.model_validate(base)
    with pytest.raises(ValidationError, match="only open states"):
        OpenOrderRead.model_validate({**base, "state": "filled", "quantity": "1"})
    with pytest.raises(ValidationError, match="appear together"):
        OpenOrderRead.model_validate({**base, "dollar_amount": "25"})


@pytest.mark.unit
def test_untrusted_display_text_rejects_controls_and_oversize_values() -> None:
    assert UntrustedDisplayText(value="safe display text").value == "safe display text"
    with pytest.raises(ValidationError, match="control"):
        UntrustedDisplayText(value="unsafe\ntext")
    with pytest.raises(ValidationError):
        UntrustedDisplayText(value="x" * 513)


@pytest.mark.unit
def test_historical_bar_and_financial_unit_invariants_fail_closed() -> None:
    with pytest.raises(ValidationError, match="inconsistent"):
        HistoricalBarRead.model_validate(
            {
                "begins_at": "2026-08-08T14:30:00Z",
                "open": "12",
                "high": "11",
                "low": "10",
                "close": "10.5",
                "volume": "1",
            }
        )
    with pytest.raises(ValidationError, match="unit policy"):
        FinancialMetric.model_validate(
            {"key": "revenue", "value": "10", "unit": "USD", "comparable": True}
        )


def _closed_order(**overrides: object) -> ClosedOrderRead:
    values: dict[str, object] = {
        "order_id": "order-123",
        "instrument": {"instrument_id": "instrument-123", "symbol": "AAPL"},
        "side": "BUY",
        "order_type": "market",
        "state": "filled",
        "quantity": "2",
        "filled_quantity": "2",
        "fees": "0",
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "placed_agent": "user",
        "created_at": "2026-08-08T14:30:00Z",
        "last_transaction_at": "2026-08-08T14:31:00Z",
        "executions": [
            {
                "execution_id": "execution-123",
                "price": "210",
                "quantity": "2",
                "timestamp": "2026-08-08T14:30:30Z",
                "fees": "0",
            }
        ],
    }
    values.update(overrides)
    return ClosedOrderRead.model_validate(values)


@pytest.mark.unit
def test_closed_filled_order_requires_complete_execution_detail() -> None:
    with pytest.raises(ValidationError, match="execution quantity"):
        _closed_order(executions=[])
    with pytest.raises(ValidationError, match="requested quantity"):
        _closed_order(
            filled_quantity="1",
            executions=[
                {
                    "execution_id": "execution-123",
                    "price": "210",
                    "quantity": "1",
                    "timestamp": "2026-08-08T14:30:30Z",
                    "fees": "0",
                }
            ],
        )


@pytest.mark.unit
def test_financial_series_rejects_duplicate_identity_and_impossible_fiscal_year() -> None:
    def series(*periods: FinancialPeriodRead) -> FinancialSeriesRead:
        return FinancialSeriesRead(
            instrument=PartialInstrumentReference(symbol="AAPL"),
            period=ReportingPeriod.QUARTERLY,
            financials=periods,
        )

    q2 = FinancialPeriodRead(
        fiscal_year=2026,
        fiscal_quarter=2,
        period_end_date=date(2026, 6, 30),
        metrics=(),
    )
    duplicate_q2 = FinancialPeriodRead(
        fiscal_year=2026,
        fiscal_quarter=2,
        period_end_date=date(2026, 3, 31),
        metrics=(),
    )
    impossible = FinancialPeriodRead(
        fiscal_year=2020,
        fiscal_quarter=1,
        period_end_date=date(2026, 3, 31),
        metrics=(),
    )
    adjacent = FinancialPeriodRead(
        fiscal_year=2025,
        fiscal_quarter=1,
        period_end_date=date(2026, 3, 31),
        metrics=(),
    )

    with pytest.raises(ValidationError, match="identities"):
        series(q2, duplicate_q2)
    with pytest.raises(ValidationError, match="fiscal year"):
        series(impossible)
    assert series(adjacent).financials == (adjacent,)
