"""Shared helpers for db unit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

ORDER_HASH = "sha256:" + ("ab" * 32)
TOKEN_HASH = "cd" * 32


def utc(moment: str = "2026-07-24T18:30:00Z") -> datetime:
    return datetime.fromisoformat(moment.replace("Z", "+00:00")).astimezone(UTC)


def later(base: datetime, seconds: int = 120) -> datetime:
    return base + timedelta(seconds=seconds)


def sample_proposal_kwargs(**overrides: object) -> dict[str, object]:
    created = utc()
    base: dict[str, object] = {
        "proposal_id": "ordp_01HZYTEST0000001",
        "signal_id": "sig_01HZYTEST0000001",
        "candidate_id": "cand_01HZYTEST0000001",
        "risk_decision_id": "risk_01HZYTEST0000001",
        "account_scope": "paper",
        "instrument_id": "rh_inst_aapl_xnas",
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": Decimal("2"),
        "limit_price": Decimal("214.50"),
        "currency": "USD",
        "strategy": "sma_crossover",
        "strategy_version": "1.2.0",
        "order_hash": ORDER_HASH,
        "status": "PENDING_APPROVAL",
        "proposal_created_at": created,
        "expires_at": later(created),
        "idempotency_key": "idem_proposal_001",
        "payload_json": {"schema_version": "1.0"},
        "schema_version": "1.0",
        "code_version": "0.1.0",
        "config_version": "cfg_v1",
        "version": 1,
    }
    base.update(overrides)
    return base


def sample_broker_order_kwargs(**overrides: object) -> dict[str, object]:
    created = utc()
    base: dict[str, object] = {
        "broker_order_id": "brk_order_001",
        "client_order_id": "client_ord_001",
        "proposal_id": "ordp_01HZYTEST0000001",
        "order_hash": ORDER_HASH,
        "account_scope": "paper",
        "side": "BUY",
        "status": "ACCEPTED",
        "submitted_at": created,
        "broker_updated_at": created,
        "idempotency_key": "idem_broker_001",
        "payload_json": {},
        "version": 1,
    }
    base.update(overrides)
    return base


def sample_fill_kwargs(**overrides: object) -> dict[str, object]:
    created = utc()
    base: dict[str, object] = {
        "fill_id": "fill_01HZYTEST0000001",
        "broker_order_id": "brk_order_001",
        "quantity": Decimal("1"),
        "price": Decimal("214.50"),
        "filled_at": created,
        "payload_json": {},
    }
    base.update(overrides)
    return base
