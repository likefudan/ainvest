"""Canonical order and cancel digests for approval binding (P02-T4).

Downstream Telegram/WebAuthn approval and execution must verify these digests.
Semantically identical protected fields always produce the same
``sha256:<hex>`` value. A replacement order always receives a new proposal and
digest; a prior approval cannot authorize it.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from ainvest.schemas.broker import CancelCommand
from ainvest.schemas.common import ensure_utc
from ainvest.schemas.orders import CandidateOrder, OrderProposal

ORDER_HASH_ALGORITHM = "sha256"
ORDER_HASH_PREFIX = f"{ORDER_HASH_ALGORITHM}:"

# Protected OrderProposal / CandidateOrder fields in canonical order.
ORDER_HASH_FIELDS: tuple[str, ...] = (
    "instrument_id",
    "symbol",
    "exchange",
    "currency",
    "asset_type",
    "side",
    "quantity",
    "order_type",
    "limit_price",
    "time_in_force",
    "maximum_notional",
    "expires_at",
    "strategy",
    "strategy_version",
    "account_scope",
)

# Protected CancelCommand fields in canonical order (separate digest domain).
CANCEL_HASH_FIELDS: tuple[str, ...] = (
    "cancel_id",
    "proposal_id",
    "broker_order_id",
    "order_hash",
    "account_scope",
    "reason_code",
    "idempotency_key",
    "requested_at",
)


DECIMAL_ORDER_FIELDS: frozenset[str] = frozenset({"quantity", "limit_price", "maximum_notional"})
DECIMAL_CANCEL_FIELDS: frozenset[str] = frozenset()


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _canonical_decimal(value: Decimal | str | int) -> str:
    if isinstance(value, bool):
        raise TypeError("boolean is not a valid decimal")
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    if not decimal_value.is_finite():
        raise ValueError("NaN and Infinity are not allowed in order hashes")
    normalized = decimal_value.normalize()
    # Avoid scientific notation; keep a stable finite decimal string.
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        text = "0"
    return text


def _canonical_timestamp(value: object) -> str:
    moment = ensure_utc(value)  # type: ignore[arg-type]
    # Drop sub-second noise for cross-language stability; seconds are required.
    return moment.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_scalar(value: object, *, as_decimal: bool = False) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("boolean values are not hashable order fields")
    if as_decimal or isinstance(value, (Decimal, int)):
        return _canonical_decimal(value)  # type: ignore[arg-type]
    if hasattr(value, "value") and isinstance(value.value, str):
        # StrEnum
        return _nfc(str(value.value))
    if hasattr(value, "year") and hasattr(value, "tzinfo"):
        return _canonical_timestamp(value)
    if isinstance(value, str):
        # ISO timestamps from JSON dumps
        if "T" in value and (value.endswith("Z") or "+" in value[10:]):
            try:
                return _canonical_timestamp(value)
            except ValueError:
                return _nfc(value)
        return _nfc(value)
    raise TypeError(f"unsupported hash field type: {type(value).__name__}")


def _extract_mapping(
    order: OrderProposal | CandidateOrder | Mapping[str, Any],
) -> Mapping[str, Any]:
    if isinstance(order, (OrderProposal, CandidateOrder)):
        return order.model_dump(mode="python")
    return order


def canonical_order_payload(
    order: OrderProposal | CandidateOrder | Mapping[str, Any],
) -> dict[str, str | None]:
    """Return the protected-field mapping used for order digests."""
    data = _extract_mapping(order)
    payload: dict[str, str | None] = {}
    for field in ORDER_HASH_FIELDS:
        if field not in data:
            raise KeyError(f"missing protected order hash field: {field}")
        payload[field] = _canonical_scalar(
            data[field],
            as_decimal=field in DECIMAL_ORDER_FIELDS,
        )
    return payload


def canonical_cancel_payload(
    command: CancelCommand | Mapping[str, Any],
) -> dict[str, str | None]:
    """Return the protected-field mapping used for cancel digests."""
    data = command.model_dump(mode="python") if isinstance(command, CancelCommand) else command
    payload: dict[str, str | None] = {}
    for field in CANCEL_HASH_FIELDS:
        if field not in data:
            raise KeyError(f"missing protected cancel hash field: {field}")
        payload[field] = _canonical_scalar(
            data[field],
            as_decimal=field in DECIMAL_CANCEL_FIELDS,
        )
    return payload


def _digest_payload(payload: Mapping[str, str | None], *, domain: str) -> str:
    document = {
        "domain": domain,
        "algorithm": ORDER_HASH_ALGORITHM,
        "fields": dict(payload),
    }
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{ORDER_HASH_PREFIX}{digest}"


def compute_order_hash(
    order: OrderProposal | CandidateOrder | Mapping[str, Any],
) -> str:
    """Compute the approval-binding digest for an order proposal/candidate."""
    return _digest_payload(canonical_order_payload(order), domain="ainvest.order.v1")


def compute_cancel_hash(command: CancelCommand | Mapping[str, Any]) -> str:
    """Compute the separate cancel-command digest."""
    return _digest_payload(canonical_cancel_payload(command), domain="ainvest.cancel.v1")


def attach_order_hash(proposal_data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of proposal mapping with ``order_hash`` filled in."""
    payload = dict(proposal_data)
    payload.pop("order_hash", None)
    digest = compute_order_hash(payload)
    payload["order_hash"] = digest
    return payload


__all__ = [
    "CANCEL_HASH_FIELDS",
    "ORDER_HASH_ALGORITHM",
    "ORDER_HASH_FIELDS",
    "ORDER_HASH_PREFIX",
    "attach_order_hash",
    "canonical_cancel_payload",
    "canonical_order_payload",
    "compute_cancel_hash",
    "compute_order_hash",
]
