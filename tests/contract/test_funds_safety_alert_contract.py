"""Versioned closed alert contracts cannot carry free-form sensitive content."""

from datetime import UTC, datetime

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ainvest.observability.alerts import AlertKind, AlertState, FundsSafetyEvent

pytestmark = pytest.mark.contract


def payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "environment": "staging",
        "kind": "submit_unknown",
        "subject_ref": "ref_" + "a" * 64,
        "sequence": 1,
        "observed_at": "2026-10-01T12:00:00Z",
        "state": "active",
    }


def test_round_trip_and_json_schema() -> None:
    parsed = FundsSafetyEvent.model_validate(payload())
    assert parsed.kind is AlertKind.SUBMIT_UNKNOWN
    assert parsed.state is AlertState.ACTIVE
    assert parsed.observed_at == datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert FundsSafetyEvent.model_validate_json(parsed.model_dump_json()) == parsed
    Draft202012Validator(FundsSafetyEvent.model_json_schema()).validate(
        parsed.model_dump(mode="json")
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "2.0"),
        ("environment", "other"),
        ("kind", "untrusted-provider-error"),
        ("state", "approved"),
        ("subject_ref", "synthetic-account-number"),
        ("subject_ref", "synthetic-token:do-not-export"),
        ("sequence", 0),
        ("sequence", True),
        ("sequence", "1"),
        ("sequence", 2**63),
        ("observed_at", "2026-10-01T12:00:00"),
        ("raw_payload", "synthetic-private-value"),
        ("account_number", "synthetic-account-value"),
        ("token", "synthetic-token-value"),
        ("state", "recovered"),
    ],
)
def test_invalid_or_sensitive_event_rejected(field: str, value: object) -> None:
    invalid = payload() | {field: value}
    with pytest.raises(ValidationError):
        FundsSafetyEvent.model_validate(invalid)
