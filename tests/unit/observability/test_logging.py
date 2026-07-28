"""Tests for structured logging, context propagation, and redaction."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from io import StringIO
from typing import Any

import pytest

from ainvest.audit import REDACTED, assert_no_plaintext_secrets
from ainvest.observability import (
    bind_log_context,
    clear_log_context,
    configure_logging,
    get_logger,
    log_context,
)


def _events(stream: StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


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
    assert event["level"] == "info"
    assert event["funds_safety"] is False
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
        },
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
    assert "RuntimeError" in event["exception"]["stack"]
    assert_no_plaintext_secrets(event, secrets)


@pytest.mark.unit
def test_funds_safety_events_bypass_level_filter_and_sampling() -> None:
    stream = StringIO()
    configure_logging(
        stream=stream,
        environment="test",
        level=logging.ERROR,
        sample_rate=0,
    )
    logger = get_logger()
    logger.info("ordinary_progress")
    logger.info("broker_submit_unknown", proposal_id="ordp_test_12345678")
    logger.debug("custom_safety_event", funds_safety=True)
    logger.error("ordinary_error")

    events = _events(stream)
    assert [event["event"] for event in events] == [
        "broker_submit_unknown",
        "custom_safety_event",
        "ordinary_error",
    ]
    assert events[0]["funds_safety"] is True
    assert events[0]["level"] == "critical"
    assert events[1]["funds_safety"] is True
    assert events[1]["level"] == "critical"
    assert events[2]["funds_safety"] is False
    assert events[2]["level"] == "error"


@pytest.mark.unit
@pytest.mark.parametrize("sample_rate", [-0.1, 1.1])
def test_invalid_sample_rate_is_rejected(sample_rate: float) -> None:
    with pytest.raises(ValueError, match="sample_rate"):
        configure_logging(sample_rate=sample_rate)
