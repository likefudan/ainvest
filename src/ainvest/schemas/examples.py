"""Canonical example payloads for contract fixtures (P02-T5)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ainvest.schemas.orders import order_proposal_example
from ainvest.schemas.research import research_packet_example
from ainvest.schemas.strategy import trade_signal_example

# Digests for design.md §6.3 example fields (ainvest.order.v1). Kept inline so
# ``schemas`` never imports ``approval`` (architecture boundary).
_ORDER_PROPOSAL_HASH = "sha256:d1b50c5ba161bf21c22422d2f45b6dab5b01ce67583a66b089f7c3f1e3a77fe7"


def _instrument(*, identity_as_of: str = "2026-07-24T18:30:00Z") -> dict[str, Any]:
    return {
        "instrument_id": "rh_inst_aapl_xnas",
        "symbol": "AAPL",
        "exchange": "XNAS",
        "currency": "USD",
        "asset_type": "EQUITY",
        "identity_as_of": identity_as_of,
    }


def _provenance(
    *,
    source: str = "robinhood.mcp.quotes",
    observed_at: str = "2026-07-24T18:29:58Z",
    received_at: str = "2026-07-24T18:30:00Z",
    quality_flags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "observed_at": observed_at,
        "received_at": received_at,
        "timezone": "UTC",
        "is_delayed": False,
        "quality_flags": quality_flags or [],
    }


def portfolio_snapshot_example() -> dict[str, Any]:
    as_of = "2026-07-24T18:30:00Z"
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
        "provenance": _provenance(source="robinhood.mcp.portfolio"),
    }


def strategy_context_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "as_of": "2026-07-24T18:30:00Z",
        "research": research_packet_example(),
        "portfolio": portfolio_snapshot_example(),
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


def candidate_order_example() -> dict[str, Any]:
    payload = order_proposal_example()
    payload["candidate_id"] = "cand_01HZYEXAMPLE0001"
    payload.pop("proposal_id", None)
    payload.pop("risk_decision_id", None)
    payload.pop("order_hash", None)
    payload["reason_codes"] = ["SIZED_TO_TARGET_WEIGHT"]
    return payload


def order_proposal_valid() -> dict[str, Any]:
    payload = order_proposal_example()
    payload["order_hash"] = _ORDER_PROPOSAL_HASH
    return payload


def risk_decision_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "risk_decision_id": "risk_01HZYEXAMPLE0001",
        "candidate_id": "cand_01HZYEXAMPLE0001",
        "outcome": "APPROVED",
        "decided_at": "2026-07-24T18:30:11Z",
        "rule_set_version": "1.0.0",
        "violations": [],
        "reason_code": "ALL_RULES_PASSED",
        "reason": "all hard and review rules passed",
    }


def approval_challenge_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "challenge_id": "apch_01HZYEXAMPLE0001",
        "proposal_id": "ordp_01HZYEXAMPLE0001",
        "order_hash": _ORDER_PROPOSAL_HASH,
        "method": "telegram",
        "scope": "paper",
        "nonce_hash": "a" * 64,
        "created_at": "2026-07-24T18:30:12Z",
        "expires_at": "2026-07-24T18:32:12Z",
        "status": "PENDING",
    }


def approval_event_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "event_id": "apev_01HZYEXAMPLE0001",
        "challenge_id": "apch_01HZYEXAMPLE0001",
        "proposal_id": "ordp_01HZYEXAMPLE0001",
        "order_hash": _ORDER_PROPOSAL_HASH,
        "method": "telegram",
        "scope": "paper",
        "outcome": "APPROVED",
        "approved_at": "2026-07-24T18:31:00Z",
        "approver_identity": "tg_user_1",
    }


def broker_order_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "broker_order_id": "brk_ord_1",
        "client_order_id": "client_ord_1",
        "proposal_id": "ordp_01HZYEXAMPLE0001",
        "order_hash": _ORDER_PROPOSAL_HASH,
        "account_scope": "paper",
        "side": "BUY",
        "status": "ACCEPTED",
        "submitted_at": "2026-07-24T18:30:20Z",
        "updated_at": "2026-07-24T18:30:20Z",
    }


def broker_fill_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "fill_id": "fill_01HZYEXAMPLE",
        "broker_order_id": "brk_ord_1",
        "quantity": "2",
        "price": "214.48",
        "filled_at": "2026-07-24T18:31:10Z",
    }


def cancel_command_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "cancel_id": "cncl_01HZYEXAMPLE0001",
        "proposal_id": "ordp_01HZYEXAMPLE0001",
        "broker_order_id": "brk_ord_1",
        "order_hash": _ORDER_PROPOSAL_HASH,
        "account_scope": "paper",
        "reason_code": "USER_REQUESTED",
        "idempotency_key": "cancel-key-0001",
        "requested_at": "2026-07-24T18:31:00Z",
    }


def cancel_result_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "cancel_id": "cncl_01HZYEXAMPLE0001",
        "broker_order_id": "brk_ord_1",
        "status": "CONFIRMED",
        "reason_code": "USER_REQUESTED",
        "observed_at": "2026-07-24T18:31:21Z",
    }


def reconciliation_result_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "reconciliation_id": "recon_01HZYEXAMPLE01",
        "proposal_id": "ordp_01HZYEXAMPLE0001",
        "broker_order_id": "brk_ord_1",
        "outcome": "MATCHED",
        "reason_code": "MATCHED",
        "observed_at": "2026-07-24T18:40:00Z",
    }


def market_quote_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "instrument": _instrument(),
        "last_price": "215.42",
        "bid": "215.40",
        "ask": "215.44",
        "currency": "USD",
        "provenance": _provenance(),
    }


def ohlcv_bar_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "instrument": _instrument(),
        "interval": "1d",
        "bar_start": "2026-07-24T00:00:00Z",
        "open": "210.00",
        "high": "216.00",
        "low": "209.50",
        "close": "215.42",
        "volume": "1000000",
        "provenance": _provenance(source="robinhood.mcp.bars"),
    }


def technical_indicators_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "symbol": "AAPL",
        "sma_20": "211.30",
        "sma_50": "204.80",
        "rsi_14": "61.20",
        "atr_14": "4.70",
        "provenance": _provenance(source="ainvest.indicators.v1"),
    }


def fundamental_snapshot_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "symbol": "AAPL",
        "as_of": "2026-07-24T18:30:00Z",
        "facts": [
            {
                "key": "pe_ratio",
                "kind": "DECIMAL",
                "decimal_value": "28.50",
            }
        ],
        "provenance": _provenance(source="robinhood.mcp.fundamentals"),
    }


def market_event_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "event_id": "mevt_01HZYEXAMPLE",
        "symbol": "AAPL",
        "event_type": "EARNINGS",
        "headline": "Quarterly earnings released",
        "occurred_at": "2026-07-20T21:00:00Z",
        "provenance": _provenance(
            source="robinhood.mcp.events",
            observed_at="2026-07-20T21:05:00Z",
            received_at="2026-07-20T21:06:00Z",
        ),
    }


def evidence_citation_example() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "evidence_id": "evid_01HZYEXAMPLE0001",
        "kind": "QUOTE",
        "summary": "Last price observed near session high",
        "provenance": _provenance(),
        "locator": "quote:robinhood.mcp.quotes/AAPL#last",
    }


EXAMPLE_BUILDERS: dict[str, Any] = {
    "ResearchPacket": research_packet_example,
    "TradeSignal": trade_signal_example,
    "StrategyContext": strategy_context_example,
    "PortfolioSnapshot": portfolio_snapshot_example,
    "CandidateOrder": candidate_order_example,
    "OrderProposal": order_proposal_valid,
    "RiskDecision": risk_decision_example,
    "ApprovalChallenge": approval_challenge_example,
    "ApprovalEvent": approval_event_example,
    "BrokerOrder": broker_order_example,
    "BrokerFill": broker_fill_example,
    "CancelCommand": cancel_command_example,
    "CancelResult": cancel_result_example,
    "ReconciliationResult": reconciliation_result_example,
    "MarketQuote": market_quote_example,
    "OhlcvBar": ohlcv_bar_example,
    "TechnicalIndicators": technical_indicators_example,
    "FundamentalSnapshot": fundamental_snapshot_example,
    "MarketEvent": market_event_example,
    "EvidenceCitation": evidence_citation_example,
}


def example_payload(model_name: str) -> dict[str, Any]:
    """Return a deep copy of the canonical valid example for ``model_name``."""
    try:
        builder = EXAMPLE_BUILDERS[model_name]
    except KeyError as exc:
        raise KeyError(f"no example builder for {model_name}") from exc
    return deepcopy(builder())


__all__ = [
    "EXAMPLE_BUILDERS",
    "example_payload",
]
