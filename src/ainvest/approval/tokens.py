"""Opaque one-time approval tokens and domain-separated hashing.

Raw tokens are returned to the caller exactly once and must never be persisted
or logged. Persistence stores only :func:`hash_approval_token` output.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Final

APPROVAL_TOKEN_BYTES: Final[int] = 32
"""256 bits of CSPRNG entropy, the minimum required by P05-T0."""

APPROVAL_TOKEN_HASH_DOMAIN: Final[bytes] = b"ainvest.approval.nonce.v1\x00"
_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{43}$")


@dataclass(frozen=True, slots=True, repr=False)
class OpaqueApprovalToken:
    """Sensitive callback token whose string representations stay redacted."""

    _value: str

    def __post_init__(self) -> None:
        _decode_token(self._value)

    def reveal(self) -> str:
        """Return the raw token for transport to the intended approval channel."""
        return self._value

    def __repr__(self) -> str:
        return "OpaqueApprovalToken(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


def generate_approval_token() -> OpaqueApprovalToken:
    """Generate a URL-safe token backed by exactly 256 random bits."""
    raw = secrets.token_bytes(APPROVAL_TOKEN_BYTES)
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return OpaqueApprovalToken(encoded)


def hash_approval_token(token: OpaqueApprovalToken | str) -> str:
    """Return the versioned-domain SHA-256 digest stored by the service."""
    value = token.reveal() if isinstance(token, OpaqueApprovalToken) else token
    raw = _decode_token(value)
    return hashlib.sha256(APPROVAL_TOKEN_HASH_DOMAIN + raw).hexdigest()


def _decode_token(value: str) -> bytes:
    if _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid approval token")
    try:
        raw = base64.urlsafe_b64decode(value + "=")
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid approval token") from exc
    if len(raw) != APPROVAL_TOKEN_BYTES:
        raise ValueError("invalid approval token")
    canonical = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if canonical != value:
        raise ValueError("invalid approval token")
    return raw


__all__ = [
    "APPROVAL_TOKEN_BYTES",
    "APPROVAL_TOKEN_HASH_DOMAIN",
    "OpaqueApprovalToken",
    "generate_approval_token",
    "hash_approval_token",
]
