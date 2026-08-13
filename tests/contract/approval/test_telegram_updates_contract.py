"""P05-T5 public boundary and forbidden-coupling contract tests."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

import ainvest.approval.telegram_updates as telegram_updates
import ainvest.db.repositories as repositories
from ainvest.approval import (
    TelegramAuthorizedUpdateHandler,
    TelegramPollingControl,
    TelegramUpdateTransport,
)
from ainvest.approval.telegram import TelegramEnvironment
from ainvest.approval.telegram_updates import AuthorizedCallbackUpdate, AuthorizedTextUpdate


def test_authorized_input_is_frozen_and_redacted() -> None:
    update = AuthorizedTextUpdate(
        environment=TelegramEnvironment.STAGING,
        update_id=1,
        sender_user_id=2,
        chat_id=3,
        message_id=4,
        text=SecretStr("private command"),
    )
    assert "private command" not in repr(update)
    with pytest.raises(ValidationError):
        update.update_id = 2
    assert update.model_dump(mode="json")["text"] == "**********"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("environment", "unknown"),
        ("update_id", -1),
        ("update_id", 2**63 - 1),
        ("sender_user_id", 0),
        ("chat_id", -(2**63)),
        ("message_id", 2**63),
        ("text", SecretStr("")),
        ("text", SecretStr("x" * 4097)),
    ],
)
def test_public_authorized_text_rejects_invalid_direct_construction(
    field: str, value: object
) -> None:
    values: dict[str, object] = {
        "environment": TelegramEnvironment.STAGING,
        "update_id": 1,
        "sender_user_id": 2,
        "chat_id": 3,
        "message_id": 4,
        "text": SecretStr("status"),
    }
    values[field] = value
    with pytest.raises(ValidationError) as captured:
        AuthorizedTextUpdate.model_validate(values)
    assert "status" not in str(captured.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("update_id", -1),
        ("sender_user_id", 0),
        ("chat_id", 2**63),
        ("message_id", -1),
        ("callback_query_id", SecretStr("")),
        ("callback_query_id", SecretStr("contains space")),
        ("callback_query_id", SecretStr("x" * 129)),
        ("callback_data", SecretStr("")),
        ("callback_data", SecretStr("x" * 65)),
        ("callback_data", SecretStr("\ud800")),
    ],
)
def test_public_authorized_callback_rejects_invalid_direct_construction(
    field: str, value: object
) -> None:
    values: dict[str, object] = {
        "environment": TelegramEnvironment.PRODUCTION,
        "update_id": 1,
        "sender_user_id": 2,
        "chat_id": 3,
        "message_id": 4,
        "callback_query_id": SecretStr("callback-id"),
        "callback_data": SecretStr("opaque"),
    }
    values[field] = value
    with pytest.raises(ValidationError) as captured:
        AuthorizedCallbackUpdate.model_validate(values)
    assert "opaque" not in str(captured.value)


def test_public_authorized_callback_json_serialization_redacts_secrets() -> None:
    update = AuthorizedCallbackUpdate(
        environment=TelegramEnvironment.STAGING,
        update_id=1,
        sender_user_id=2,
        chat_id=3,
        message_id=4,
        callback_query_id=SecretStr("callback-id"),
        callback_data=SecretStr("opaque"),
    )
    assert update.model_dump(mode="json") == {
        "environment": "staging",
        "update_id": 1,
        "sender_user_id": 2,
        "chat_id": 3,
        "message_id": 4,
        "callback_query_id": "**********",
        "callback_data": "**********",
    }


def test_polling_boundary_does_not_import_business_handlers_or_raw_orm() -> None:
    source = Path(inspect.getfile(telegram_updates)).read_text(encoding="utf-8")
    assert "approval.callback" not in source
    assert "telegram_queries" not in source
    assert "ainvest.db.models" not in source
    assert "broker" not in source.lower()


def test_public_ports_are_exported_and_retention_has_no_delete_api() -> None:
    assert TelegramAuthorizedUpdateHandler is telegram_updates.TelegramAuthorizedUpdateHandler
    assert TelegramPollingControl is telegram_updates.TelegramPollingControl
    assert TelegramUpdateTransport is telegram_updates.TelegramUpdateTransport
    repository_methods = {
        name for name, _ in inspect.getmembers(repositories.TelegramUpdateRepository)
    }
    assert not {"delete", "delete_processed", "prune", "cleanup"} & repository_methods
