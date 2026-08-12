"""Public outbound Telegram contract tests (coordinator-authorized P05-T4 scope)."""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from ainvest.approval import TelegramNotificationOutcome
from ainvest.approval.telegram import (
    TelegramDeliveryCode,
    TelegramEnvironment,
    TelegramTransport,
)


@pytest.mark.contract
def test_delivery_outcome_wire_is_stable_and_sanitized() -> None:
    outcome = TelegramNotificationOutcome(
        code=TelegramDeliveryCode.SENT,
        retryable=False,
        environment=TelegramEnvironment.STAGING,
        intent_correlation_id="notify_01HZYEXAMPLE0001",
        telegram_message_id=77,
    )

    assert outcome.model_dump(mode="json") == {
        "code": "sent",
        "retryable": False,
        "environment": "staging",
        "intent_correlation_id": "notify_01HZYEXAMPLE0001",
        "telegram_message_id": 77,
    }
    assert set(TelegramDeliveryCode) == {
        TelegramDeliveryCode.SENT,
        TelegramDeliveryCode.CONFIG_INVALID,
        TelegramDeliveryCode.BOT_IDENTITY_MISMATCH,
        TelegramDeliveryCode.RECIPIENT_NOT_ALLOWED,
        TelegramDeliveryCode.CHAT_NOT_PRIVATE,
        TelegramDeliveryCode.MESSAGE_INVALID,
        TelegramDeliveryCode.VALIDATION_TIMEOUT,
        TelegramDeliveryCode.DELIVERY_UNKNOWN,
        TelegramDeliveryCode.DELIVERY_FAILED,
    }


@pytest.mark.contract
def test_retryable_and_message_id_invariants_are_not_caller_selectable() -> None:
    with pytest.raises(ValidationError):
        TelegramNotificationOutcome(
            code=TelegramDeliveryCode.DELIVERY_UNKNOWN,
            retryable=True,
            environment=TelegramEnvironment.PRODUCTION,
            intent_correlation_id="notify_01HZYEXAMPLE0001",
        )
    with pytest.raises(ValidationError):
        TelegramNotificationOutcome(
            code=TelegramDeliveryCode.DELIVERY_FAILED,
            retryable=False,
            environment=TelegramEnvironment.PRODUCTION,
            intent_correlation_id="notify_01HZYEXAMPLE0001",
            telegram_message_id=77,
        )


@pytest.mark.contract
def test_transport_surface_is_outbound_only() -> None:
    methods = {
        name
        for name, value in inspect.getmembers(TelegramTransport)
        if inspect.isfunction(value) and not name.startswith("_")
    }

    assert methods == {"get_me", "get_chat", "send_message"}
    assert methods.isdisjoint(
        {
            "get_updates",
            "poll",
            "receive_update",
            "decide_approval",
            "invoke_broker",
            "query_robinhood",
        }
    )
