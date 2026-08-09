"""Focused invariants for the P06-T1 part 1 normalized read models."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from ainvest.execution.robinhood.read_models import (
    EquityQuoteRead,
    OpenOrderRead,
    QuoteIneligibility,
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
