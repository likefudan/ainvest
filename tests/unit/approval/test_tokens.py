"""Security tests for opaque approval nonce generation and hashing."""

from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import asdict, dataclass

import pytest
from pydantic_core import to_jsonable_python

from ainvest.approval.tokens import (
    APPROVAL_TOKEN_BYTES,
    APPROVAL_TOKEN_HASH_DOMAIN,
    OpaqueApprovalToken,
    generate_approval_token,
    hash_approval_token,
)

FIXED_TOKEN = "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg"


@pytest.mark.unit
def test_generate_token_uses_256_bits_and_redacts_string_forms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[int] = []

    def fake_token_bytes(size: int) -> bytes:
        requested.append(size)
        return b"x" * size

    monkeypatch.setattr("ainvest.approval.tokens.secrets.token_bytes", fake_token_bytes)

    token = generate_approval_token()

    assert requested == [APPROVAL_TOKEN_BYTES]
    assert token.reveal() == FIXED_TOKEN
    assert FIXED_TOKEN not in repr(token)
    assert FIXED_TOKEN not in str(token)


@pytest.mark.unit
def test_token_hash_is_domain_separated_and_deterministic() -> None:
    token = OpaqueApprovalToken(FIXED_TOKEN)
    expected = hashlib.sha256(APPROVAL_TOKEN_HASH_DOMAIN + (b"x" * 32)).hexdigest()

    assert hash_approval_token(token) == expected
    assert hash_approval_token(token.reveal()) == expected
    assert token.reveal() not in expected


@pytest.mark.unit
def test_token_is_redacted_or_rejected_by_structural_serializers() -> None:
    token = OpaqueApprovalToken(FIXED_TOKEN)

    @dataclass
    class Envelope:
        approval_token: OpaqueApprovalToken

    structural = asdict(Envelope(token))
    assert FIXED_TOKEN not in repr(structural)
    with pytest.raises(TypeError):
        vars(token)
    with pytest.raises(TypeError):
        json.dumps(token)
    with pytest.raises(TypeError, match="cannot be structurally serialized"):
        pickle.dumps(token)

    json_fallback = json.dumps(
        {"approval_token": token},
        default=str,
    )
    pydantic_fallback = to_jsonable_python(
        {"approval_token": token},
        fallback=str,
    )
    structlog_style = json.dumps(
        {"event": "approval-issued", "approval_token": token},
        default=repr,
    )
    assert FIXED_TOKEN not in json_fallback
    assert FIXED_TOKEN not in json.dumps(pydantic_fallback)
    assert FIXED_TOKEN not in structlog_style
    assert "<redacted>" in json_fallback


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "too-short",
        "x" * 42,
        "x" * 44,
        "!" * 43,
        FIXED_TOKEN[:-1] + "h",
    ],
)
def test_invalid_token_encodings_fail_closed(invalid: str) -> None:
    with pytest.raises(ValueError, match="invalid approval token"):
        OpaqueApprovalToken(invalid)
    with pytest.raises(ValueError, match="invalid approval token"):
        hash_approval_token(invalid)
