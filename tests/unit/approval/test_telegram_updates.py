"""Pure Telegram update normalization and authorization tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

import ainvest.approval.telegram as telegram_module
from ainvest.approval.telegram import TelegramEnvironment, TelegramHttpsTransport
from ainvest.approval.telegram_updates import (
    AuthorizedCallbackUpdate,
    AuthorizedTextUpdate,
    IgnoredTelegramUpdate,
    TelegramHttpsUpdateTransport,
    TelegramIgnoredReason,
    TelegramPollingFatal,
    TelegramProviderRateLimited,
    TelegramProviderTransient,
    TelegramProviderUpdate,
    TelegramProviderUpdateKind,
    _normalize_provider_update,
    _ordered_unique_batch,
    classify_update,
)

PAIR = frozenset({(101, 201), (111, 211)})


def _message(**changes: object) -> TelegramProviderUpdate:
    values: dict[str, object] = {
        "update_id": 7,
        "kind": TelegramProviderUpdateKind.MESSAGE,
        "sender_user_id": 101,
        "chat_id": 201,
        "message_id": 301,
        "chat_type": "private",
        "text": SecretStr("approve"),
    }
    values.update(changes)
    return TelegramProviderUpdate.model_validate(values)


def test_classifies_only_exact_bound_private_pairs() -> None:
    authorized = classify_update(
        _message(), environment=TelegramEnvironment.STAGING, allowed_pairs=PAIR
    )
    assert isinstance(authorized, AuthorizedTextUpdate)
    assert "approve" not in repr(authorized)
    crossed = classify_update(
        _message(chat_id=211), environment=TelegramEnvironment.STAGING, allowed_pairs=PAIR
    )
    assert crossed == IgnoredTelegramUpdate(
        environment=TelegramEnvironment.STAGING,
        update_id=7,
        reason=TelegramIgnoredReason.UNKNOWN_RECIPIENT,
    )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"sender_user_id": None}, TelegramIgnoredReason.MISSING_SENDER),
        ({"chat_type": "group"}, TelegramIgnoredReason.NOT_PRIVATE_CHAT),
        ({"forwarded": True}, TelegramIgnoredReason.FORWARDED_MESSAGE),
        ({"service": True, "text": None}, TelegramIgnoredReason.SERVICE_MESSAGE),
        (
            {"kind": TelegramProviderUpdateKind.UNSUPPORTED, "text": None},
            TelegramIgnoredReason.UNSUPPORTED_UPDATE,
        ),
    ],
)
def test_ignored_classifications(changes: dict[str, object], reason: TelegramIgnoredReason) -> None:
    result = classify_update(
        _message(**changes), environment=TelegramEnvironment.STAGING, allowed_pairs=PAIR
    )
    assert isinstance(result, IgnoredTelegramUpdate)
    assert result.reason is reason


def test_callback_values_are_redacted_and_inline_callbacks_are_ignored() -> None:
    update = TelegramProviderUpdate(
        update_id=9,
        kind=TelegramProviderUpdateKind.CALLBACK,
        sender_user_id=101,
        chat_id=201,
        message_id=301,
        chat_type="private",
        callback_query_id=SecretStr("callback-secret"),
        callback_data=SecretStr("action-secret"),
    )
    result = classify_update(update, environment=TelegramEnvironment.STAGING, allowed_pairs=PAIR)
    assert isinstance(result, AuthorizedCallbackUpdate)
    assert "secret" not in repr(result)
    inline = classify_update(
        update.model_copy(update={"message_id": None, "chat_id": None}),
        environment=TelegramEnvironment.STAGING,
        allowed_pairs=PAIR,
    )
    assert isinstance(inline, IgnoredTelegramUpdate)
    assert inline.reason is TelegramIgnoredReason.INLINE_CALLBACK
    forwarded = classify_update(
        update.model_copy(update={"forwarded": True}),
        environment=TelegramEnvironment.STAGING,
        allowed_pairs=PAIR,
    )
    assert isinstance(forwarded, IgnoredTelegramUpdate)
    assert forwarded.reason is TelegramIgnoredReason.FORWARDED_MESSAGE


def test_batch_orders_collapses_exact_duplicates_and_rejects_conflicts() -> None:
    seven = _message()
    eight = _message(update_id=8)
    assert _ordered_unique_batch((eight, seven, seven)) == (seven, eight)
    with pytest.raises(TelegramPollingFatal, match="conflicting"):
        _ordered_unique_batch((seven, _message(text=SecretStr("different"))))
    with pytest.raises(TelegramPollingFatal, match="oversized"):
        _ordered_unique_batch(tuple(_message(update_id=value) for value in range(101)))


def test_update_id_is_strict_and_bounded() -> None:
    assert _message(update_id=0).update_id == 0
    assert _message(update_id=2**63 - 2).update_id == 2**63 - 2
    with pytest.raises(ValidationError):
        _message(update_id=True)
    with pytest.raises(ValidationError):
        _message(update_id=2**63 - 1)


def test_normalizer_turns_bounded_malformed_content_into_terminal_input() -> None:
    malformed_callback = SimpleNamespace(
        update_id=10,
        callback_query=SimpleNamespace(
            id="contains space",
            data="opaque",
            from_user=SimpleNamespace(id=101),
            message=SimpleNamespace(
                message_id=301,
                chat=SimpleNamespace(id=201, type="private"),
            ),
        ),
        message=None,
    )
    result = _normalize_provider_update(malformed_callback)
    assert result.kind is TelegramProviderUpdateKind.MALFORMED


@pytest.mark.parametrize(
    ("callback_id", "callback_data"),
    [
        ("", "opaque"),
        ("id", ""),
        ("contains space", "opaque"),
        ("id", "x" * 65),
        ("id", "\ud800"),
    ],
)
def test_normalizer_turns_invalid_callback_content_into_terminal_malformed(
    callback_id: str, callback_data: str
) -> None:
    callback = SimpleNamespace(
        update_id=13,
        callback_query=SimpleNamespace(
            id=callback_id,
            data=callback_data,
            from_user=SimpleNamespace(id=101),
            message=SimpleNamespace(
                message_id=301,
                chat=SimpleNamespace(id=201, type="private"),
            ),
        ),
        message=None,
    )
    assert _normalize_provider_update(callback) == TelegramProviderUpdate(
        update_id=13,
        kind=TelegramProviderUpdateKind.MALFORMED,
    )


def test_normalizer_marks_automatic_forward_as_forwarded() -> None:
    automatic_forward = SimpleNamespace(
        update_id=11,
        callback_query=None,
        message=SimpleNamespace(
            text="status",
            from_user=SimpleNamespace(id=101),
            chat=SimpleNamespace(id=201, type="private"),
            message_id=301,
            forward_origin=None,
            forward_date=None,
            is_automatic_forward=True,
        ),
    )
    result = _normalize_provider_update(automatic_forward)
    assert result.forwarded is True


def test_normalizer_marks_overlong_text_and_invalid_message_id_malformed() -> None:
    overlong = SimpleNamespace(
        update_id=12,
        callback_query=None,
        message=SimpleNamespace(
            text="x" * 4097,
            from_user=SimpleNamespace(id=101),
            chat=SimpleNamespace(id=201, type="private"),
            message_id=301,
        ),
    )
    assert _normalize_provider_update(overlong).kind is TelegramProviderUpdateKind.MALFORMED
    classified = classify_update(
        _message(message_id=0),
        environment=TelegramEnvironment.STAGING,
        allowed_pairs=PAIR,
    )
    assert isinstance(classified, IgnoredTelegramUpdate)
    assert classified.reason is TelegramIgnoredReason.MALFORMED_UPDATE

    empty_text = SimpleNamespace(
        update_id=14,
        callback_query=None,
        message=SimpleNamespace(
            text="",
            from_user=SimpleNamespace(id=101),
            chat=SimpleNamespace(id=201, type="private"),
            message_id=301,
        ),
    )
    assert _normalize_provider_update(empty_text) == TelegramProviderUpdate(
        update_id=14,
        kind=TelegramProviderUpdateKind.MALFORMED,
    )


class _RetryAfter(Exception):
    def __init__(self, retry_after: object) -> None:
        self.retry_after = retry_after


class _TimedOut(Exception):
    pass


class _NetworkError(Exception):
    pass


class _InvalidToken(Exception):
    pass


class _Forbidden(Exception):
    pass


class _BadRequest(Exception):
    pass


class _Conflict(Exception):
    pass


def _adapter_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: tuple[object, ...] = (),
    error: BaseException | None = None,
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    class Bot:
        def __init__(self, *, token: str) -> None:
            assert token == "synthetic-token"

        async def get_updates(self, **kwargs: object) -> tuple[object, ...]:
            calls.append(kwargs)
            if error is not None:
                raise error
            return result

    telegram = SimpleNamespace(Bot=Bot)
    errors = SimpleNamespace(
        RetryAfter=_RetryAfter,
        TimedOut=_TimedOut,
        NetworkError=_NetworkError,
        InvalidToken=_InvalidToken,
        Forbidden=_Forbidden,
        BadRequest=_BadRequest,
        Conflict=_Conflict,
    )

    def fake_import(name: str) -> object:
        return errors if name == "telegram.error" else telegram

    monkeypatch.setattr("ainvest.approval.telegram_updates.import_module", fake_import)
    monkeypatch.setattr(telegram_module, "_telegram_modules", lambda: (telegram, errors))
    return calls


def test_https_adapter_passes_exact_bounds_and_terminalizes_empty_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = SimpleNamespace(
        update_id=9,
        callback_query=SimpleNamespace(
            id="callback-id",
            data="",
            from_user=SimpleNamespace(id=101),
            message=SimpleNamespace(
                message_id=301,
                chat=SimpleNamespace(id=201, type="private"),
            ),
        ),
        message=None,
    )
    calls = _adapter_modules(monkeypatch, result=(raw,))
    result = asyncio.run(
        TelegramHttpsUpdateTransport(TelegramHttpsTransport()).get_updates(
            "synthetic-token",
            offset=7,
            timeout=25,
            limit=100,
            allowed_updates=("message", "callback_query"),
            deadline_seconds=35.0,
        )
    )
    assert result == (
        TelegramProviderUpdate(update_id=9, kind=TelegramProviderUpdateKind.MALFORMED),
    )
    assert calls == [
        {
            "offset": 7,
            "timeout": 25,
            "limit": 100,
            "allowed_updates": ("message", "callback_query"),
            "read_timeout": 35.0,
            "write_timeout": 35.0,
            "connect_timeout": 35.0,
            "pool_timeout": 35.0,
        }
    ]


@pytest.mark.parametrize("provider_error", [_TimedOut(), _NetworkError()])
def test_https_adapter_maps_transient_errors(
    monkeypatch: pytest.MonkeyPatch, provider_error: BaseException
) -> None:
    _adapter_modules(monkeypatch, error=provider_error)
    with pytest.raises(TelegramProviderTransient):
        asyncio.run(
            TelegramHttpsUpdateTransport(TelegramHttpsTransport()).get_updates(
                "synthetic-token",
                offset=0,
                timeout=25,
                limit=100,
                allowed_updates=("message", "callback_query"),
                deadline_seconds=35,
            )
        )


@pytest.mark.parametrize(
    "provider_error",
    [_InvalidToken(), _Forbidden(), _BadRequest(), _Conflict()],
)
def test_https_adapter_maps_rejections_to_sanitized_fatal(
    monkeypatch: pytest.MonkeyPatch, provider_error: BaseException
) -> None:
    _adapter_modules(monkeypatch, error=provider_error)
    with pytest.raises(TelegramPollingFatal, match="rejected the polling request"):
        asyncio.run(
            TelegramHttpsUpdateTransport(TelegramHttpsTransport()).get_updates(
                "synthetic-token",
                offset=0,
                timeout=25,
                limit=100,
                allowed_updates=("message", "callback_query"),
                deadline_seconds=35,
            )
        )


def test_https_adapter_maps_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    _adapter_modules(monkeypatch, error=_RetryAfter(17))
    with pytest.raises(TelegramProviderRateLimited) as captured:
        asyncio.run(
            TelegramHttpsUpdateTransport(TelegramHttpsTransport()).get_updates(
                "synthetic-token",
                offset=0,
                timeout=25,
                limit=100,
                allowed_updates=("message", "callback_query"),
                deadline_seconds=35,
            )
        )
    assert captured.value.retry_after_seconds == 17


def test_https_adapter_missing_optional_dependency_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_import(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr("ainvest.approval.telegram_updates.import_module", missing_import)
    with pytest.raises(TelegramPollingFatal, match="dependency is unavailable"):
        asyncio.run(
            TelegramHttpsUpdateTransport(TelegramHttpsTransport()).get_updates(
                "synthetic-token",
                offset=0,
                timeout=25,
                limit=100,
                allowed_updates=("message", "callback_query"),
                deadline_seconds=35,
            )
        )


def test_https_adapter_rejects_oversized_provider_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _adapter_modules(monkeypatch, result=tuple(SimpleNamespace(update_id=i) for i in range(101)))
    with pytest.raises(TelegramPollingFatal, match="oversized"):
        asyncio.run(
            TelegramHttpsUpdateTransport(TelegramHttpsTransport()).get_updates(
                "synthetic-token",
                offset=0,
                timeout=25,
                limit=100,
                allowed_updates=("message", "callback_query"),
                deadline_seconds=35,
            )
        )
