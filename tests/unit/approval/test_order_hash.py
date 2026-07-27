"""Unit tests and fixed vectors for canonical order hashing (P02-T4)."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from decimal_cases import (
    EXTREME_HUGE,
    EXTREME_ZERO_ENCODINGS,
    PADDED_ONE,
    SCIENTIFIC_NOTATION_STRINGS,
)

from ainvest.approval.order_hash import (
    ORDER_HASH_FIELDS,
    attach_order_hash,
    compute_cancel_hash,
    compute_order_hash,
    parse_order_proposal,
)
from ainvest.schemas.broker import CancelCommand
from ainvest.schemas.orders import order_proposal_example

VECTORS = Path(__file__).parent / "fixtures" / "order_hash_vectors.json"


def _base_order() -> dict[str, Any]:
    payload = order_proposal_example()
    payload.pop("order_hash", None)
    return payload


@pytest.mark.unit
def test_fixed_order_hash_vectors() -> None:
    """Cross-language consumers must match these digests exactly."""
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    for case in vectors["order_cases"]:
        digest = compute_order_hash(case["input"])
        assert digest == case["expected_hash"], case["name"]
        # Semantically identical alternate encodings must not drift.
        if "equivalent_input" in case:
            assert compute_order_hash(case["equivalent_input"]) == case["expected_hash"]

    for case in vectors["cancel_cases"]:
        digest = compute_cancel_hash(case["input"])
        assert digest == case["expected_hash"], case["name"]

    for case in vectors.get("error_cases", []):
        with pytest.raises(ValueError, match="null"):
            compute_order_hash(case["input"])


@pytest.mark.unit
def test_protected_field_changes_change_hash() -> None:
    base = _base_order()
    baseline = compute_order_hash(base)
    mutations: dict[str, Any] = {
        "instrument_id": "rh_inst_msft_xnas",
        "symbol": "MSFT",
        "exchange": "XNYS",
        "currency": "EUR",
        "asset_type": "ETF",
        "side": "SELL",
        "quantity": "3",
        "limit_price": "215.00",
        "maximum_notional": "645.00",
        "expires_at": "2026-07-24T18:40:12Z",
        "strategy": "rsi_reversion",
        "strategy_version": "2.0.0",
        "account_scope": "paper",
    }
    # order_type/time_in_force are first-release singletons (LIMIT/DAY) and cannot
    # legally vary; every other protected field must change the digest.
    for field, value in mutations.items():
        assert field in ORDER_HASH_FIELDS
        mutated = deepcopy(base)
        mutated[field] = value
        assert compute_order_hash(mutated) != baseline, field
    assert set(ORDER_HASH_FIELDS) - set(mutations) == {"order_type", "time_in_force"}


@pytest.mark.unit
def test_unprotected_fields_do_not_change_hash() -> None:
    base = _base_order()
    baseline = compute_order_hash(base)
    mutated = deepcopy(base)
    mutated["proposal_id"] = "ordp_01OTHER00000001"
    mutated["signal_id"] = "sig_01OTHER00000001"
    mutated["candidate_id"] = "cand_01OTHER00000001"
    mutated["risk_decision_id"] = "risk_01OTHER00000001"
    mutated["created_at"] = "2026-07-24T18:00:00Z"
    mutated["quantity_increment"] = "1"
    mutated["price_increment"] = "0.01"
    assert compute_order_hash(mutated) == baseline


@pytest.mark.unit
def test_attach_order_hash_round_trips_through_proposal() -> None:
    attached = attach_order_hash(_base_order())
    proposal = parse_order_proposal(attached)
    assert proposal.order_hash == compute_order_hash(attached)


@pytest.mark.unit
def test_subsecond_expiry_changes_change_hash() -> None:
    base = _base_order()
    early = deepcopy(base)
    early["expires_at"] = "2026-07-24T18:32:12.100000Z"
    late = deepcopy(base)
    late["expires_at"] = "2026-07-24T18:32:12.900000Z"
    assert compute_order_hash(early) != compute_order_hash(late)


@pytest.mark.unit
def test_null_protected_field_is_rejected() -> None:
    base = _base_order()
    base["symbol"] = None
    with pytest.raises(ValueError, match="null"):
        compute_order_hash(base)


@pytest.mark.unit
def test_cancel_hash_is_independent_of_order_hash_domain() -> None:
    order = attach_order_hash(_base_order())
    cancel = {
        "cancel_id": "cncl_01HZYEXAMPLE0001",
        "proposal_id": order["proposal_id"],
        "broker_order_id": "brk_ord_1",
        "order_hash": order["order_hash"],
        "account_scope": "paper",
        "reason_code": "USER_REQUESTED",
        "idempotency_key": "cancel-key-0001",
        "requested_at": "2026-07-24T18:31:00Z",
    }
    cancel_digest = compute_cancel_hash(cancel)
    assert cancel_digest.startswith("sha256:")
    assert cancel_digest != order["order_hash"]
    # Valid cancel command schema accepts the same mapping.
    CancelCommand.model_validate(cancel)

    tweaked = deepcopy(cancel)
    tweaked["idempotency_key"] = "cancel-key-0002"
    assert compute_cancel_hash(tweaked) != cancel_digest


@pytest.mark.unit
def test_high_precision_quantities_do_not_collide_under_default_context() -> None:
    """>28 significant digits must not round into the same digest."""
    base = _base_order()
    base["maximum_notional"] = "999999999999999999999999999999999"
    early = deepcopy(base)
    early["quantity"] = "10000000000000000000000000000.1"
    late = deepcopy(base)
    late["quantity"] = "10000000000000000000000000000.9"
    assert compute_order_hash(early) != compute_order_hash(late)


@pytest.mark.unit
def test_extreme_decimal_exponents_are_rejected_before_allocation() -> None:
    """Hash domain fail-closes on extreme exponents (ValueError, not schema)."""
    base = _base_order()
    with pytest.raises(ValueError, match="decimal"):
        compute_order_hash({**base, "quantity": SCIENTIFIC_NOTATION_STRINGS[-1]})
    with pytest.raises(ValueError, match="exponent"):
        compute_order_hash({**base, "quantity": EXTREME_HUGE})


@pytest.mark.unit
def test_hash_accepts_trailing_zero_equivalent_quantities() -> None:
    """Hash domain must treat trailing-zero quantity encodings as identical."""
    base = _base_order()
    plain = deepcopy(base)
    plain["quantity"] = "1"
    plain["maximum_notional"] = "214.50"
    padded = deepcopy(plain)
    padded["quantity"] = PADDED_ONE
    assert compute_order_hash(plain) == compute_order_hash(padded)


@pytest.mark.unit
def test_hash_collapses_extreme_exponent_zero_notionals() -> None:
    """Hash domain must collapse extreme-exponent zero before digesting."""
    base = _base_order()
    plain = deepcopy(base)
    plain["maximum_notional"] = "0"
    extreme = deepcopy(plain)
    extreme["maximum_notional"] = EXTREME_ZERO_ENCODINGS[0]
    assert compute_order_hash(plain) == compute_order_hash(extreme)
