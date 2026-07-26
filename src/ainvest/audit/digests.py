"""Payload digests for large or external audit objects."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_digest(data: bytes | str) -> str:
    """Return a ``sha256:<hex>`` digest string."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def digest_json(value: Any) -> str:
    """Canonical JSON digest (sorted keys, UTF-8, no NaN)."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )
    return sha256_digest(encoded)


def utf8_size(value: Any) -> int:
    """Return UTF-8 byte size of the canonical JSON rendering of ``value``."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )
    return len(encoded.encode("utf-8"))


__all__ = ["digest_json", "sha256_digest", "utf8_size"]
