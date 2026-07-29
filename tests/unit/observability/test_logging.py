"""Tests for structured logging, context propagation, and redaction."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping, Sequence
from decimal import Decimal
from io import StringIO
from typing import Any, Never, overload

import pytest

import ainvest.observability.logging as logging_module
from ainvest.audit import REDACTED, assert_no_plaintext_secrets
from ainvest.observability import (
    bind_log_context,
    clear_log_context,
    configure_logging,
    get_logger,
    log_context,
)


def _reject_json_constant(value: str) -> Never:
    raise ValueError(f"non-standard JSON constant: {value}")


def _events(stream: StringIO) -> list[dict[str, Any]]:
    return [
        json.loads(line, parse_constant=_reject_json_constant)
        for line in stream.getvalue().splitlines()
    ]


@pytest.fixture(autouse=True)
def _isolated_log_context() -> Iterator[None]:
    clear_log_context()
    yield
    clear_log_context()


@pytest.mark.unit
def test_json_logging_has_stable_identity_and_bound_workflow_context() -> None:
    stream = StringIO()
    configure_logging(
        service="approval-worker",
        environment="test",
        version="1.2.3",
        stream=stream,
    )

    with log_context(
        correlation_id="corr_test_12345678",
        causation_id="cmd_test_12345678",
        proposal_id="ordp_test_12345678",
        strategy_run_id="srun_test_12345678",
    ):
        get_logger("approval").info("approval_checked", outcome="APPROVED")

    event = _events(stream)[0]
    assert event["event"] == "approval_checked"
    assert event["service"] == "approval-worker"
    assert event["environment"] == "test"
    assert event["version"] == "1.2.3"
    assert event["component"] == "approval"
    assert event["correlation_id"] == "corr_test_12345678"
    assert event["causation_id"] == "cmd_test_12345678"
    assert event["proposal_id"] == "ordp_test_12345678"
    assert event["strategy_run_id"] == "srun_test_12345678"
    assert event["client_order_id"] is None
    assert event["broker_order_id"] is None
    assert event["level"] == "info"
    assert event["funds_safety"] is False
    assert event["sampling_exempt"] is False
    assert event["timestamp"].endswith("Z")


@pytest.mark.unit
def test_context_manager_restores_prior_context() -> None:
    stream = StringIO()
    configure_logging(stream=stream, environment="test")
    bind_log_context(
        correlation_id="corr_outer_12345678",
        strategy_run_id="srun_outer_12345678",
    )

    with log_context(
        correlation_id="corr_inner_12345678",
        proposal_id="ordp_inner_12345678",
    ):
        get_logger().info("inside")
    get_logger().info("outside")

    inside, outside = _events(stream)
    assert inside["correlation_id"] == "corr_inner_12345678"
    assert inside["strategy_run_id"] == "srun_outer_12345678"
    assert inside["proposal_id"] == "ordp_inner_12345678"
    assert outside["correlation_id"] == "corr_outer_12345678"
    assert outside["causation_id"] is None
    assert outside["proposal_id"] is None
    assert outside["strategy_run_id"] == "srun_outer_12345678"


@pytest.mark.unit
def test_recursive_policy_redacts_headers_prompts_links_and_money_payloads() -> None:
    stream = StringIO()
    configure_logging(stream=stream, environment="test")
    corpus = [
        "sk-" + "proj-" + "synthetic-key-marker",
        "paper-callback-secret",
        "bearer-secret-value",
        "session-cookie-secret",
        "acct-123456789",
        "123.45",
    ]

    get_logger().info(
        "provider_failed",
        nested={
            "model_prompt": f"summarize with {corpus[0]}",
            "approval_link": f"https://approval.example/approve?token={corpus[1]}",
            "headers": {
                "Authorization": f"Bearer {corpus[2]}",
                "Cookie": f"session={corpus[3]}",
                "Content-Type": "application/json",
                "X-Request-ID": "request-safe",
                "X-Provider-Private": "private-header",
            },
            "account_number": corpus[4],
            "broker_credential": "nested-broker-credential",
            "order_type": "LIMIT",
            "quantity": "2",
            "symbol": "AAPL",
        },
        brokerCredential="top-level-broker-credential",
        credential="generic-credential",
        instrument_id="rh-instrument-private",
        limit_price=corpus[5],
        positions=[{"symbol": "AAPL", "quantity": "5"}],
        order_proposal={
            "quantity": "2",
            "limit_price": corpus[5],
            "side": "BUY",
        },
    )

    event = _events(stream)[0]
    nested = event["nested"]
    assert nested["model_prompt"] == REDACTED
    assert nested["approval_link"] == REDACTED
    assert nested["headers"]["Authorization"] == REDACTED
    assert nested["headers"]["Cookie"] == REDACTED
    assert nested["headers"]["Content-Type"] == "application/json"
    assert nested["headers"]["X-Request-ID"] == "request-safe"
    assert nested["headers"]["X-Provider-Private"] == REDACTED
    assert nested["account_number"] == REDACTED
    assert nested["broker_credential"] == REDACTED
    assert nested["order_type"] == REDACTED
    assert nested["quantity"] == REDACTED
    assert nested["symbol"] == REDACTED
    assert event["brokerCredential"] == REDACTED
    assert event["credential"] == REDACTED
    assert event["instrument_id"] == REDACTED
    assert event["limit_price"] == REDACTED
    assert event["positions"] == REDACTED
    assert event["order_proposal"]["content"] == REDACTED
    assert event["order_proposal"]["digest"].startswith("sha256:")
    assert_no_plaintext_secrets(event, corpus)


@pytest.mark.unit
def test_exception_type_and_stack_survive_with_inline_secrets_redacted() -> None:
    stream = StringIO()
    configure_logging(stream=stream, environment="test")
    secrets = [
        "sk-" + "synthetic-exception-key",
        "inline-token-value",
        "approval-callback-value",
    ]

    try:
        raise RuntimeError(
            f"api_key={secrets[0]} access_token={secrets[1]} "
            f"https://approval.example/challenge?token={secrets[2]}"
        )
    except RuntimeError:
        get_logger().exception("external_call_failed")

    event = _events(stream)[0]
    assert event["exception"]["type"] == "RuntimeError"
    assert REDACTED in event["exception"]["message"]
    assert event["exception"]["stack"]
    assert_no_plaintext_secrets(event, secrets)


@pytest.mark.unit
def test_exception_args_notes_cause_and_context_are_structurally_redacted() -> None:
    stream = StringIO()
    configure_logging(stream=stream, environment="test")
    secret_key = "sk-" + "mapping-key-secret"
    account = "account-99887766"
    token = "approval-token-secret"

    context = KeyError({"account_number": account, secret_key: token})
    cause = ValueError({"token": token, "shape": ["visible", {"secret": account}]})
    cause.__context__ = context
    cause.add_note(f"api_key={secret_key}")
    outer = RuntimeError({"approval_token": token, "kind": "provider"})
    outer.add_note(f"account_number={account}")
    try:
        raise outer from cause
    except RuntimeError:
        get_logger().exception("exception_tree_failed")

    event = _events(stream)[0]
    exception = event["exception"]
    assert exception["type"] == "RuntimeError"
    assert exception["args"][0]["approval_token"] == REDACTED
    assert exception["cause"]["type"] == "ValueError"
    assert exception["cause"]["context"]["type"] == "KeyError"
    assert exception["notes"]
    assert exception["cause"]["notes"]
    assert_no_plaintext_secrets(event, [secret_key, account, token])


class _HostileObject:
    def __str__(self) -> str:
        raise RuntimeError("hostile __str__")

    def __repr__(self) -> str:
        raise RuntimeError("hostile __repr__")


class _HostileMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        raise RuntimeError(key)

    def __iter__(self) -> Iterator[str]:
        raise RuntimeError("hostile mapping iterator")

    def __len__(self) -> int:
        raise RuntimeError("hostile mapping length")

    def items(self) -> Never:
        raise RuntimeError("hostile mapping items")


class _HostileSequence(Sequence[object]):
    @overload
    def __getitem__(self, index: int) -> object: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[object]: ...

    def __getitem__(self, index: int | slice) -> object | Sequence[object]:
        raise RuntimeError(index)

    def __len__(self) -> int:
        raise RuntimeError("hostile sequence length")

    def __iter__(self) -> Iterator[object]:
        raise RuntimeError("hostile sequence iterator")


@pytest.mark.unit
def test_cycles_depth_and_hostile_objects_emit_once_without_raising() -> None:
    stream = StringIO()
    configure_logging(stream=stream, environment="test")
    cyclic_dict: dict[str, object] = {}
    cyclic_dict["self"] = cyclic_dict
    cyclic_list: list[object] = []
    cyclic_list.append(cyclic_list)
    deep: dict[str, object] = {}
    cursor = deep
    for _index in range(20):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child

    get_logger().info(
        "adversarial_payload",
        cyclic_dict=cyclic_dict,
        cyclic_list=cyclic_list,
        deep=deep,
        hostile_object=_HostileObject(),
        hostile_mapping=_HostileMapping(),
        hostile_sequence=_HostileSequence(),
    )

    events = _events(stream)
    assert len(events) == 1
    rendered = json.dumps(events[0], sort_keys=True)
    assert "<cycle>" in rendered
    assert "<truncated>" in rendered
    assert "<_HostileObject>" in rendered
    assert "<unavailable>" in rendered or REDACTED in rendered


@pytest.mark.unit
def test_secret_looking_mapping_keys_are_redacted() -> None:
    stream = StringIO()
    configure_logging(stream=stream, environment="test")
    secret_key = "sk-" + "secret-as-a-mapping-key"
    token = "token-value-that-must-vanish"

    get_logger().info(
        "mapping_key_test",
        nested={
            secret_key: "visible",
            "account_number": "account-11223344",
            "token": token,
        },
    )

    event = _events(stream)[0]
    assert secret_key not in json.dumps(event, sort_keys=True)
    assert_no_plaintext_secrets(event, [secret_key, "account-11223344", token])


@pytest.mark.unit
def test_recursive_field_contract_preserves_only_safe_workflow_identifiers() -> None:
    stream = StringIO()
    configure_logging(stream=stream, environment="test")
    with log_context(
        correlation_id="corr_safe_12345678",
        proposal_id="ordp_safe_12345678",
        client_order_id="client_safe_12345678",
        broker_order_id="broker_safe_12345678",
    ):
        get_logger().info(
            "field_contract",
            nested={
                "client_order_id": "client_nested_12345678",
                "account_id": "account-private",
                "filled_quantity": "3",
                "instrument_identity": {"symbol": "AAPL", "exchange": "XNAS"},
                "stopPrice": "98.50",
            },
        )

    event = _events(stream)[0]
    assert event["correlation_id"] == "corr_safe_12345678"
    assert event["proposal_id"] == "ordp_safe_12345678"
    assert event["client_order_id"] == "client_safe_12345678"
    assert event["broker_order_id"] == "broker_safe_12345678"
    assert event["nested"]["client_order_id"] == "client_nested_12345678"
    assert event["nested"]["account_id"] == REDACTED
    assert event["nested"]["filled_quantity"] == REDACTED
    assert event["nested"]["instrument_identity"] == REDACTED
    assert event["nested"]["stopPrice"] == REDACTED


@pytest.mark.unit
def test_overlong_keys_are_replaced_and_values_fail_closed_recursively() -> None:
    stream = StringIO()
    configure_logging(stream=stream, environment="test")
    at_limit = "k" * 256
    over_limit = "x" * 257
    hidden_credential = ("c" * 256) + "_broker_credential"
    hidden_financial = ("f" * 256) + "_limit_price"

    get_logger().info(
        "overlong_keys",
        **{
            at_limit: "safe-boundary-value",
            over_limit: "over-limit-value",
            hidden_credential: "credential-value",
            "nested": {hidden_financial: "123.45"},
        },
    )

    rendered = stream.getvalue()
    event = _events(stream)[0]
    assert event[at_limit] == "safe-boundary-value"
    assert over_limit not in rendered
    assert hidden_credential not in rendered
    assert hidden_financial not in rendered
    assert "over-limit-value" not in rendered
    assert "credential-value" not in rendered
    assert "123.45" not in rendered
    top_level_placeholders = [key for key in event if key.startswith("<redacted-key:")]
    nested_placeholders = [key for key in event["nested"] if key.startswith("<redacted-key:")]
    assert len(top_level_placeholders) == 2
    assert len(nested_placeholders) == 1
    assert all(event[key] == REDACTED for key in top_level_placeholders)
    assert event["nested"][nested_placeholders[0]] == REDACTED


@pytest.mark.unit
def test_numeric_extremes_render_as_strict_bounded_json() -> None:
    stream = StringIO()
    configure_logging(stream=stream, environment="test")

    get_logger().info(
        "numeric_extremes",
        huge_integer=1 << 100_000,
        huge_decimal=Decimal("1e1000000"),
        tiny_decimal=Decimal("1e-1000000"),
        decimal_nan=Decimal("NaN"),
        positive_infinity=float("inf"),
        negative_infinity=float("-inf"),
        nan=float("nan"),
    )

    event = _events(stream)[0]
    assert event["huge_integer"] == "<number-out-of-range>"
    assert event["huge_decimal"] == "<number-out-of-range>"
    assert event["tiny_decimal"] == "<number-out-of-range>"
    assert event["decimal_nan"] == "<non-finite-number>"
    assert event["positive_infinity"] == "<non-finite-number>"
    assert event["negative_infinity"] == "<non-finite-number>"
    assert event["nan"] == "<non-finite-number>"


@pytest.mark.unit
def test_renderer_failure_emits_static_strict_json_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = StringIO()
    configure_logging(stream=stream, environment="test")

    def fail_rendering(_value: object) -> Never:
        raise RuntimeError("renderer failure")

    monkeypatch.setattr(logging_module, "_strict_json_dumps", fail_rendering)
    get_logger().info("renderer_failure", credential="must-not-escape")

    event = _events(stream)[0]
    assert event == {
        "event": "logging_render_failed",
        "funds_safety": False,
        "level": "error",
        "logging_error_code": "LOG_RENDER_FAILED",
        "sampling_exempt": True,
    }


@pytest.mark.unit
def test_sanitizer_failure_emits_minimal_funds_safety_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = StringIO()
    configure_logging(stream=stream, environment="test")

    def fail_sanitization(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("processor failure")

    monkeypatch.setattr(logging_module, "_sanitize", fail_sanitization)
    get_logger().info(
        "execute_order",
        correlation_id="corr_fallback_12345678",
        money_payload={"limit_price": "123.45"},
    )

    event = _events(stream)[0]
    assert event["event"] == "execute_order"
    assert event["funds_safety"] is True
    assert event["sampling_exempt"] is True
    assert event["level"] == "error"
    assert event["logging_error_code"] == "LOG_SANITIZATION_FAILED"
    assert event["correlation_id"] == "corr_fallback_12345678"
    assert "money_payload" not in event


@pytest.mark.unit
def test_money_lifecycle_bypasses_sampling_without_forcing_severity() -> None:
    stream = StringIO()
    configure_logging(
        stream=stream,
        environment="test",
        level=logging.ERROR,
        sample_rate=0,
    )
    logger = get_logger()
    logger.info("ordinary_progress")
    logger.info(
        "execute_order",
        proposal_id="ordp_test_12345678",
        outcome="SUBMITTED",
    )
    logger.info("custom_safety_event", funds_safety=True)
    logger.info(
        "execute_order",
        proposal_id="ordp_test_12345678",
        outcome="SUBMIT_UNKNOWN",
    )
    logger.error("ordinary_error")

    events = _events(stream)
    assert [event["event"] for event in events] == [
        "execute_order",
        "custom_safety_event",
        "execute_order",
        "ordinary_error",
    ]
    assert events[0]["funds_safety"] is True
    assert events[0]["sampling_exempt"] is True
    assert events[0]["level"] == "info"
    assert events[1]["funds_safety"] is True
    assert events[1]["sampling_exempt"] is True
    assert events[1]["level"] == "info"
    assert events[2]["funds_safety"] is True
    assert events[2]["sampling_exempt"] is True
    assert events[2]["level"] == "critical"
    assert events[3]["funds_safety"] is False
    assert events[3]["sampling_exempt"] is False
    assert events[3]["level"] == "error"


@pytest.mark.unit
def test_incident_rules_match_reachable_stable_event_schema() -> None:
    assert logging_module._INCIDENT_SEVERITY_RULES == {
        ("execute_order", "SUBMIT_UNKNOWN"): "critical",
        ("reconcile_after_unknown", "MANUAL_REVIEW"): "critical",
        ("reconcile", "DIVERGED"): "critical",
        ("reconcile", "UNKNOWN"): "critical",
        ("reconcile", "MANUAL_REVIEW"): "critical",
        ("blind_retry_blocked", None): "warning",
    }
    stream = StringIO()
    configure_logging(
        stream=stream,
        environment="test",
        level=logging.ERROR,
        sample_rate=0,
    )

    logger = get_logger()
    logger.info("execute_order", outcome="SUBMIT_UNKNOWN")
    logger.info("reconcile_after_unknown", outcome="MANUAL_REVIEW")
    logger.info("reconcile", outcome="DIVERGED")
    logger.info("reconcile", outcome="UNKNOWN")
    logger.info("reconcile", outcome="MANUAL_REVIEW")
    logger.info("blind_retry_blocked")

    events = _events(stream)
    assert [event["level"] for event in events] == [
        "critical",
        "critical",
        "critical",
        "critical",
        "critical",
        "warning",
    ]
    assert all(event["funds_safety"] is True for event in events)
    assert all(event["sampling_exempt"] is True for event in events)


@pytest.mark.unit
@pytest.mark.parametrize("sample_rate", [-0.1, 1.1])
def test_invalid_sample_rate_is_rejected(sample_rate: float) -> None:
    with pytest.raises(ValueError, match="sample_rate"):
        configure_logging(sample_rate=sample_rate)
