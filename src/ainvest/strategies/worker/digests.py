"""Canonical digests for strategy worker audit metadata."""

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


__all__ = ["digest_json", "sha256_digest"]
