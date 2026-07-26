"""Unit tests for audit redaction and digests."""

from __future__ import annotations

import pytest

from ainvest.audit.digests import digest_json, sha256_digest
from ainvest.audit.envelope import MAX_AUDIT_PAYLOAD_BYTES
from ainvest.audit.redact import REDACTED, assert_no_plaintext_secrets, redact


@pytest.mark.unit
def test_redact_nested_tokens_cookies_auth_and_account_numbers() -> None:
    payload = {
        "order": {"symbol": "AAPL", "quantity": "2"},
        "authorization": "Bearer super-secret-token-value-abcdefghijklmnopqrstuvwxyz",
        "headers": {
            "Cookie": "session=abc123; other=1",
            "X-Other": "ok",
        },
        "nested": {
            "approval_token": "raw-approval-token-should-vanish",
            "bot_token": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
            "account_number": "123456789012",
            "safe": "visible",
        },
        "note": "Authorization: Bearer anothersecrettokenvalue000111222333",
    }
    redacted = redact(payload)
    assert isinstance(redacted, dict)
    assert redacted["authorization"] == REDACTED
    assert redacted["nested"]["approval_token"] == REDACTED
    assert redacted["nested"]["bot_token"] == REDACTED
    assert redacted["nested"]["account_number"] == REDACTED
    assert redacted["nested"]["safe"] == "visible"
    assert redacted["order"]["symbol"] == "AAPL"
    assert REDACTED in str(redacted["note"])

    corpus = [
        "super-secret-token-value-abcdefghijklmnopqrstuvwxyz",
        "raw-approval-token-should-vanish",
        "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
        "session=abc123",
        "anothersecrettokenvalue000111222333",
    ]
    assert_no_plaintext_secrets(redacted, corpus)


@pytest.mark.unit
def test_secret_corpus_finds_leak() -> None:
    with pytest.raises(AssertionError, match="plaintext secret"):
        assert_no_plaintext_secrets({"token": "leaked"}, ["leaked"])


@pytest.mark.unit
def test_digest_is_stable() -> None:
    assert digest_json({"b": 1, "a": 2}) == digest_json({"a": 2, "b": 1})
    assert sha256_digest("abc").startswith("sha256:")
    assert MAX_AUDIT_PAYLOAD_BYTES == 16_384


@pytest.mark.unit
def test_redact_preserves_decimal_and_datetime() -> None:
    from datetime import UTC, datetime
    from decimal import Decimal

    payload = {
        "qty": Decimal("2.50"),
        "when": datetime(2026, 7, 24, 18, 30, tzinfo=UTC),
    }
    redacted = redact(payload)
    assert redacted["qty"] == "2.50"
    assert redacted["when"] == "2026-07-24T18:30:00+00:00"
