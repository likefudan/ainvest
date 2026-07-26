"""Recursive redaction of secrets from audit payloads.

Redacts tokens, cookies, authorization headers, account numbers, and raw
approval tokens. Redaction is fail-closed: unknown nested structures are
converted to safe placeholders rather than passed through.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

REDACTED: Final[str] = "***REDACTED***"

# Exact key names (case-insensitive) that always redact their values.
_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "bot_token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "cookies",
        "set-cookie",
        "set_cookie",
        "password",
        "secret",
        "client_secret",
        "private_key",
        "passkey",
        "webauthn_server_secret",
        "robinhood_oauth_token",
        "openai_api_key",
        "database_password",
        "webhook_secret",
        "approval_token",
        "raw_token",
        "raw_nonce",
        "nonce",
        "account_number",
        "account_no",
        "routing_number",
        "ssn",
        "session",
        "session_id",
        "cookie_header",
        "auth_header",
        "bearer",
        "x-api-key",
    }
)

# Substring matches for keys (case-insensitive).
_SENSITIVE_KEY_SUBSTRINGS: Final[tuple[str, ...]] = (
    "token",
    "secret",
    "password",
    "cookie",
    "authorization",
    "account_number",
    "account-number",
    "private_key",
    "api_key",
    "apikey",
    "nonce",
)

_BOT_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")
_BEARER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(authorization\s*[:=]\s*bearer\s+)([A-Za-z0-9._\-+=/]+)"
)
_COOKIE_ASSIGN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(cookie\s*[:=]\s*)([^\s;]+(?:;\s*[^\s;]+)*)"
)


def _key_is_sensitive(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if normalized in _SENSITIVE_KEYS:
        return True
    return any(part in normalized for part in _SENSITIVE_KEY_SUBSTRINGS)


def _redact_string(value: str, *, force: bool = False) -> str:
    if force:
        return REDACTED
    redacted = _BOT_TOKEN_RE.sub(REDACTED, value)
    redacted = _BEARER_RE.sub(rf"\1{REDACTED}", redacted)
    redacted = _COOKIE_ASSIGN_RE.sub(rf"\1{REDACTED}", redacted)
    return redacted


def redact(value: Any, *, _parent_sensitive: bool = False) -> Any:
    """Recursively redact sensitive values from mappings, sequences, and scalars."""
    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return _redact_string(value, force=_parent_sensitive)

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            sensitive = _parent_sensitive or _key_is_sensitive(key_str)
            if sensitive:
                if isinstance(item, Mapping):
                    result[key_str] = redact(item, _parent_sensitive=True)
                elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                    result[key_str] = [REDACTED for _ in item]
                else:
                    result[key_str] = REDACTED
            else:
                result[key_str] = redact(item, _parent_sensitive=False)
        return result

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(item, _parent_sensitive=_parent_sensitive) for item in value]

    if isinstance(value, (bytes, bytearray)):
        return REDACTED if _parent_sensitive else f"<bytes:{len(value)}>"

    # Unknown objects become a type placeholder (fail closed).
    return f"<{type(value).__name__}>"


def assert_no_plaintext_secrets(payload: Any, corpus: Sequence[str]) -> None:
    """Raise ``AssertionError`` if any corpus secret appears in ``payload`` text."""
    rendered = json_dumps_for_scan(payload)
    for secret in corpus:
        if not secret:
            continue
        if secret in rendered:
            raise AssertionError("plaintext secret found in audit payload")


def json_dumps_for_scan(payload: Any) -> str:
    """Render payload for secret-scanning without raising on non-JSON types."""
    try:
        return json.dumps(payload, default=str)
    except TypeError:
        return str(payload)


__all__ = [
    "REDACTED",
    "assert_no_plaintext_secrets",
    "json_dumps_for_scan",
    "redact",
]
