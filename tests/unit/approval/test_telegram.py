"""Outbound-only Telegram notification tests for P05-T4."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import SecretStr, ValidationError

import ainvest.approval.telegram as telegram_module
from ainvest.approval.order_hash import attach_order_hash
from ainvest.approval.telegram import (
    TelegramBotIdentity,
    TelegramChatIdentity,
    TelegramDeliveryCode,
    TelegramDeliveryUnknown,
    TelegramEnvironment,
    TelegramHttpsTransport,
    TelegramNotificationCategory,
    TelegramNotificationOutcome,
    TelegramNotificationRequest,
    TelegramNotificationSender,
    TelegramOutboundAction,
    TelegramTransportRejected,
    TelegramValidationTimeout,
)
from ainvest.approval.tokens import OpaqueApprovalToken
from ainvest.config import Settings, TelegramBotSettings, TelegramRecipient, WebAuthnSettings
from ainvest.schemas.orders import OrderProposal, order_proposal_example
from ainvest.schemas.risk import RiskDecision, RiskOutcome

TOKEN_VALUE = "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg"


@dataclass
class FakeTelegramTransport:
    """Deterministic fake for the three outbound HTTPS operations only."""

    bot_id: int = 900000001
    chat_id: int = 900000201
    chat_type: str = "private"
    message_id: object = 77
    bot_ids_by_token: dict[str, int] | None = None
    get_me_effects: list[BaseException] = field(default_factory=list)
    get_chat_effects: list[BaseException] = field(default_factory=list)
    send_effect: BaseException | None = None
    get_me_calls: int = 0
    get_chat_calls: int = 0
    send_calls: int = 0
    sent_text: str | None = None
    sent_action: TelegramOutboundAction | None = None

    async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity:
        del timeout_seconds
        self.get_me_calls += 1
        if self.get_me_effects:
            raise self.get_me_effects.pop(0)
        bot_id = (
            self.bot_ids_by_token.get(token, self.bot_id) if self.bot_ids_by_token else self.bot_id
        )
        return TelegramBotIdentity(id=bot_id)

    async def get_chat(
        self, token: str, chat_id: int, *, timeout_seconds: float
    ) -> TelegramChatIdentity:
        del token, chat_id, timeout_seconds
        self.get_chat_calls += 1
        if self.get_chat_effects:
            raise self.get_chat_effects.pop(0)
        return TelegramChatIdentity(id=self.chat_id, type=self.chat_type)

    async def send_message(
        self,
        token: str,
        chat_id: int,
        text: str,
        action: TelegramOutboundAction,
        *,
        timeout_seconds: float,
    ) -> int:
        del token, chat_id, timeout_seconds
        self.send_calls += 1
        self.sent_text = text
        self.sent_action = action
        if self.send_effect is not None:
            raise self.send_effect
        return cast(int, self.message_id)


class FakeProviderError:
    class BadRequest(Exception):
        pass

    class Forbidden(Exception):
        pass

    class InvalidToken(Exception):
        pass

    class TimedOut(Exception):
        pass

    class NetworkError(Exception):
        pass


def _fake_adapter_modules(
    *,
    effect: BaseException | None = None,
    message_id: object = 707,
) -> tuple[SimpleNamespace, type[FakeProviderError], list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    class FakeBot:
        def __init__(self, token: str) -> None:
            assert token == "synthetic-token"

        async def send_message(self, **kwargs: Any) -> SimpleNamespace:
            calls.append(kwargs)
            if effect is not None:
                raise effect
            return SimpleNamespace(message_id=message_id)

    telegram = SimpleNamespace(
        Bot=FakeBot,
        InlineKeyboardButton=lambda **kwargs: kwargs,
        InlineKeyboardMarkup=lambda rows: rows,
    )
    return telegram, FakeProviderError, calls


def _proposal(**updates: Any) -> OrderProposal:
    payload = order_proposal_example()
    payload["account_scope"] = "paper"
    payload.update(updates)
    return OrderProposal.model_validate(attach_order_hash(payload))


def _risk(proposal: OrderProposal, **updates: Any) -> RiskDecision:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "risk_decision_id": proposal.risk_decision_id,
        "proposal_id": proposal.proposal_id,
        "outcome": "APPROVED",
        "decided_at": "2026-07-24T18:30:13Z",
        "rule_set_version": "v1",
        "violations": [],
        "reason_code": "ALL_RULES_PASSED",
        "reason": "All configured risk rules passed.",
    }
    payload.update(updates)
    return RiskDecision.model_validate(payload)


def _settings(*, production: bool = False, live_origin: str | None = None) -> Settings:
    staging = TelegramBotSettings(
        enabled=not production,
        bot_token=SecretStr("staging-fake-token"),
        expected_bot_id=900000001,
        allowed_recipients=(
            TelegramRecipient(user_id=900000101, private_chat_id=900000201),
            TelegramRecipient(user_id=900000111, private_chat_id=900000211),
        ),
    )
    prod = TelegramBotSettings(
        enabled=production,
        bot_token=SecretStr("production-fake-token"),
        expected_bot_id=900000002,
        allowed_recipients=(TelegramRecipient(user_id=900000102, private_chat_id=900000202),),
    )
    return Settings(
        telegram_staging=staging,
        telegram_production=prod,
        webauthn=WebAuthnSettings(origin=live_origin, rp_id="approve.example.test")
        if live_origin
        else WebAuthnSettings(),
    )


def _request(
    *,
    category: TelegramNotificationCategory = TelegramNotificationCategory.PAPER,
    environment: TelegramEnvironment = TelegramEnvironment.STAGING,
    proposal: OrderProposal | None = None,
    risk: RiskDecision | None = None,
    user_id: int = 900000101,
    chat_id: int = 900000201,
    link: str = "https://approve.example.test/live/challenge-opaque",
) -> TelegramNotificationRequest:
    selected_proposal = proposal or _proposal(
        account_scope=("agentic" if category is TelegramNotificationCategory.LIVE else "paper")
    )
    return TelegramNotificationRequest(
        environment=environment,
        category=category,
        intent_correlation_id="notify_01HZYEXAMPLE0001",
        recipient_user_id=user_id,
        recipient_private_chat_id=chat_id,
        proposal=selected_proposal,
        risk_decision=risk or _risk(selected_proposal),
        expires_at=datetime(2026, 7, 24, 18, 31, 30, tzinfo=UTC),
        paper_nonce=OpaqueApprovalToken(TOKEN_VALUE)
        if category is TelegramNotificationCategory.PAPER
        else None,
        live_approval_link=SecretStr(link)
        if category is TelegramNotificationCategory.LIVE
        else None,
    )


def _send(
    request: TelegramNotificationRequest,
    transport: FakeTelegramTransport,
    settings: Settings | None = None,
) -> TelegramNotificationOutcome:
    return asyncio.run(TelegramNotificationSender(settings or _settings(), transport).send(request))


@pytest.mark.unit
def test_paper_notification_has_complete_plain_text_and_bound_callback() -> None:
    transport = FakeTelegramTransport()

    outcome = _send(_request(), transport)

    assert outcome.code is TelegramDeliveryCode.SENT
    assert outcome.telegram_message_id == 77
    assert outcome.retryable is False
    assert transport.get_me_calls == transport.get_chat_calls == transport.send_calls == 1
    assert transport.sent_text == (
        "PAPER ORDER NOTIFICATION\n"
        "Proposal: ordp_01HZYEXAMPLE0001\n"
        "Instrument: AAPL (rh_inst_aapl_xnas)\n"
        "Side: BUY\n"
        "Quantity: 2 shares\n"
        "Order type: LIMIT\n"
        "Limit price: 214.5 USD\n"
        "Maximum notional: 429 USD\n"
        "Time in force: DAY\n"
        "Expires: 2026-07-24T18:31:30Z\n"
        "Strategy: sma_crossover v1.2.0\n"
        "Risk: APPROVED\n"
        "Risk reasons:\n"
        "- ALL_RULES_PASSED: All configured risk rules passed."
    )
    assert transport.sent_action is not None
    assert transport.sent_action.reveal() == (TOKEN_VALUE, None)
    assert TOKEN_VALUE not in repr(transport.sent_action)


@pytest.mark.unit
def test_live_notification_uses_only_fixed_origin_link() -> None:
    transport = FakeTelegramTransport(bot_id=900000002, chat_id=900000202)
    request = _request(
        category=TelegramNotificationCategory.LIVE,
        environment=TelegramEnvironment.PRODUCTION,
        user_id=900000102,
        chat_id=900000202,
    )

    outcome = _send(
        request,
        transport,
        _settings(production=True, live_origin="https://approve.example.test"),
    )

    assert outcome.code is TelegramDeliveryCode.SENT
    assert transport.sent_text is not None and transport.sent_text.startswith("LIVE ")
    assert transport.sent_text == (
        "LIVE ORDER NOTIFICATION\n"
        "Proposal: ordp_01HZYEXAMPLE0001\n"
        "Instrument: AAPL (rh_inst_aapl_xnas)\n"
        "Side: BUY\n"
        "Quantity: 2 shares\n"
        "Order type: LIMIT\n"
        "Limit price: 214.5 USD\n"
        "Maximum notional: 429 USD\n"
        "Time in force: DAY\n"
        "Expires: 2026-07-24T18:31:30Z\n"
        "Strategy: sma_crossover v1.2.0\n"
        "Risk: APPROVED\n"
        "Risk reasons:\n"
        "- ALL_RULES_PASSED: All configured risk rules passed."
    )
    assert transport.sent_action is not None
    assert transport.sent_action.reveal() == (
        None,
        "https://approve.example.test/live/challenge-opaque",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "link",
    [
        "http://approve.example.test/live/challenge",
        "https://evil.example.test/live/challenge",
        "https://approve.example.test.evil/live/challenge",
        "https://user@approve.example.test/live/challenge",
        "https://approve.example.test/live/challenge#secret",
        "https://approve.example.test/live/\u202eevil",
    ],
)
def test_live_notification_rejects_unsafe_link_before_send(link: str) -> None:
    transport = FakeTelegramTransport(bot_id=900000002, chat_id=900000202)
    request = _request(
        category=TelegramNotificationCategory.LIVE,
        environment=TelegramEnvironment.PRODUCTION,
        user_id=900000102,
        chat_id=900000202,
        link=link,
    )

    outcome = _send(
        request,
        transport,
        _settings(production=True, live_origin="https://approve.example.test"),
    )

    assert outcome.code is TelegramDeliveryCode.MESSAGE_INVALID
    assert transport.get_me_calls == transport.send_calls == 0
    assert link not in repr(request)
    assert link not in repr(outcome)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("user_id", "chat_id"),
    [
        (900000101, 900000211),  # crossed known values
        (900000999, 900000201),
        (900000101, 900000999),
    ],
)
def test_unbound_recipient_pair_fails_before_network(user_id: int, chat_id: int) -> None:
    transport = FakeTelegramTransport()

    outcome = _send(_request(user_id=user_id, chat_id=chat_id), transport)

    assert outcome.code is TelegramDeliveryCode.RECIPIENT_NOT_ALLOWED
    assert transport.get_me_calls == transport.get_chat_calls == transport.send_calls == 0


@pytest.mark.unit
def test_bot_identity_failure_stops_before_chat_and_send() -> None:
    transport = FakeTelegramTransport(bot_id=900000999)

    outcome = _send(_request(), transport)

    assert outcome.code is TelegramDeliveryCode.BOT_IDENTITY_MISMATCH
    assert transport.get_me_calls == 1
    assert transport.get_chat_calls == transport.send_calls == 0


@pytest.mark.unit
def test_swapped_environment_token_fails_exact_bot_identity() -> None:
    settings = _settings().model_copy(
        update={
            "telegram_staging": TelegramBotSettings(
                enabled=True,
                bot_token=SecretStr("production-fake-token"),
                expected_bot_id=900000001,
                allowed_recipients=(
                    TelegramRecipient(user_id=900000101, private_chat_id=900000201),
                ),
            )
        }
    )
    transport = FakeTelegramTransport(
        bot_ids_by_token={"production-fake-token": 900000002},
    )

    outcome = _send(_request(), transport, settings)

    assert outcome.code is TelegramDeliveryCode.BOT_IDENTITY_MISMATCH
    assert transport.get_chat_calls == transport.send_calls == 0


@pytest.mark.unit
@pytest.mark.parametrize("chat_type", ["group", "channel", "supergroup"])
def test_non_private_or_wrong_chat_fails_before_send(chat_type: str) -> None:
    group = FakeTelegramTransport(chat_type=chat_type)
    wrong = FakeTelegramTransport(chat_id=900000999)

    assert _send(_request(), group).code is TelegramDeliveryCode.CHAT_NOT_PRIVATE
    assert _send(_request(), wrong).code is TelegramDeliveryCode.CHAT_NOT_PRIVATE
    assert group.send_calls == wrong.send_calls == 0


@pytest.mark.unit
def test_validation_timeout_retries_twice_and_is_only_retryable_failure() -> None:
    transport = FakeTelegramTransport(
        get_me_effects=[TelegramValidationTimeout(), TelegramValidationTimeout()]
    )

    outcome = _send(_request(), transport)

    assert outcome.code is TelegramDeliveryCode.VALIDATION_TIMEOUT
    assert outcome.retryable is True
    assert transport.get_me_calls == 2
    assert transport.get_chat_calls == transport.send_calls == 0


@pytest.mark.unit
def test_transient_validation_recovers_within_bound() -> None:
    transport = FakeTelegramTransport(
        get_me_effects=[TelegramValidationTimeout()],
        get_chat_effects=[TelegramValidationTimeout()],
    )

    outcome = _send(_request(), transport)

    assert outcome.code is TelegramDeliveryCode.SENT
    assert transport.get_me_calls == transport.get_chat_calls == 2
    assert transport.send_calls == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "effect",
    [
        TelegramDeliveryUnknown(),
        asyncio.CancelledError(),
        RuntimeError("provider detail must remain hidden"),
    ],
)
def test_ambiguous_send_is_unknown_nonretryable_and_never_retried(
    effect: BaseException,
) -> None:
    transport = FakeTelegramTransport(send_effect=effect)

    outcome = _send(_request(), transport)

    assert outcome.code is TelegramDeliveryCode.DELIVERY_UNKNOWN
    assert outcome.retryable is False
    assert transport.send_calls == 1
    assert "provider detail" not in repr(outcome)


@pytest.mark.unit
@pytest.mark.parametrize("message_id", [None, 0, -1, True, "77"])
def test_unusable_post_send_result_is_unknown_and_never_retried(message_id: object) -> None:
    transport = FakeTelegramTransport(message_id=message_id)

    outcome = _send(_request(), transport)

    assert outcome.code is TelegramDeliveryCode.DELIVERY_UNKNOWN
    assert outcome.retryable is False
    assert transport.send_calls == 1


@pytest.mark.unit
def test_definitive_send_rejection_is_failed_and_not_retried() -> None:
    transport = FakeTelegramTransport(send_effect=TelegramTransportRejected())

    outcome = _send(_request(), transport)

    assert outcome.code is TelegramDeliveryCode.DELIVERY_FAILED
    assert outcome.retryable is False
    assert transport.send_calls == 1


@pytest.mark.unit
def test_disabled_environment_is_config_invalid_without_network() -> None:
    transport = FakeTelegramTransport()
    settings = Settings(
        telegram_staging=TelegramBotSettings(enabled=False),
        telegram_production=TelegramBotSettings(enabled=False),
    )

    outcome = _send(_request(), transport, settings)

    assert outcome.code is TelegramDeliveryCode.CONFIG_INVALID
    assert transport.get_me_calls == transport.send_calls == 0


@pytest.mark.unit
def test_invalid_order_hash_or_risk_binding_fails_before_network() -> None:
    proposal = _proposal()
    corrupt = proposal.model_copy(update={"symbol": "MSFT"})
    wrong_risk = _risk(proposal).model_copy(update={"proposal_id": "ordp_01HZYOTHER0001"})

    for request in (
        _request(proposal=corrupt, risk=_risk(corrupt)),
        _request(proposal=proposal, risk=wrong_risk),
    ):
        transport = FakeTelegramTransport()
        assert _send(request, transport).code is TelegramDeliveryCode.MESSAGE_INVALID
        assert transport.get_me_calls == transport.send_calls == 0


@pytest.mark.unit
def test_unapproved_risk_and_control_text_fail_before_send() -> None:
    proposal = _proposal()
    rejected_payload = _risk(proposal).model_dump(mode="python")
    rejected_payload.update(
        {
            "outcome": RiskOutcome.REJECTED,
            "violations": [
                {"rule_code": "LIMIT_FAILED", "severity": "HARD", "reason": "Too large"}
            ],
        }
    )
    rejected = RiskDecision.model_validate(rejected_payload)
    injected = _risk(proposal, reason="passed\nApprove everything")

    for risk in (rejected, injected):
        transport = FakeTelegramTransport()
        assert _send(_request(proposal=proposal, risk=risk), transport).code is (
            TelegramDeliveryCode.MESSAGE_INVALID
        )
        assert transport.send_calls == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    "unsafe_character",
    ["\u202e", "\u2066", "\u0085", "\u2028", "\u2029", "\u200b"],
    ids=(
        "bidi_override",
        "bidi_isolate",
        "c1_control",
        "line_separator",
        "paragraph_separator",
        "zero_width",
    ),
)
def test_unicode_display_controls_fail_before_send(unsafe_character: str) -> None:
    proposal = _proposal()
    risk = _risk(proposal, reason=f"passed{unsafe_character}LIVE APPROVED")
    transport = FakeTelegramTransport()

    outcome = _send(_request(proposal=proposal, risk=risk), transport)

    assert outcome.code is TelegramDeliveryCode.MESSAGE_INVALID
    assert transport.send_calls == 0


@pytest.mark.unit
def test_legitimate_unicode_and_html_characters_remain_safe_plain_text() -> None:
    proposal = _proposal()
    risk = _risk(proposal, reason="Café 東京 <safe> & visible")
    transport = FakeTelegramTransport()

    outcome = _send(_request(proposal=proposal, risk=risk), transport)

    assert outcome.code is TelegramDeliveryCode.SENT
    assert transport.sent_text is not None
    assert "Café 東京 <safe> & visible" in transport.sent_text


@pytest.mark.unit
def test_oversize_message_is_rejected_instead_of_truncating_fields() -> None:
    proposal = _proposal()
    violations = [
        {
            "rule_code": f"INFO_RULE_{index}",
            "severity": "INFO",
            "reason": "x" * 500,
        }
        for index in range(8)
    ]
    risk = _risk(proposal, violations=violations)
    transport = FakeTelegramTransport()

    outcome = _send(_request(proposal=proposal, risk=risk), transport)

    assert outcome.code is TelegramDeliveryCode.MESSAGE_INVALID
    assert transport.send_calls == 0


@pytest.mark.unit
def test_request_requires_exactly_one_category_action_and_hides_it() -> None:
    proposal = _proposal()
    base: dict[str, Any] = {
        "environment": TelegramEnvironment.STAGING,
        "category": TelegramNotificationCategory.PAPER,
        "intent_correlation_id": "notify_01HZYEXAMPLE0001",
        "recipient_user_id": 900000101,
        "recipient_private_chat_id": 900000201,
        "proposal": proposal,
        "risk_decision": _risk(proposal),
        "expires_at": datetime(2026, 7, 24, 18, 31, 30, tzinfo=UTC),
    }
    with pytest.raises(ValidationError):
        TelegramNotificationRequest.model_validate(base)
    with pytest.raises(ValidationError):
        TelegramNotificationRequest.model_validate(
            {
                **base,
                "paper_nonce": OpaqueApprovalToken(TOKEN_VALUE),
                "live_approval_link": SecretStr("https://approve.example.test/live/secret"),
            }
        )

    request = _request()
    assert TOKEN_VALUE not in repr(request)
    assert "staging-fake-token" not in repr(_settings())


@pytest.mark.unit
def test_https_adapter_uses_plain_text_and_one_send_call(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_telegram, fake_error, calls = _fake_adapter_modules()
    monkeypatch.setattr(
        telegram_module,
        "_telegram_modules",
        lambda: (fake_telegram, fake_error),
    )
    action = TelegramOutboundAction(
        label="Approve PAPER",
        callback_data=TOKEN_VALUE,
        url=None,
    )

    message_id = asyncio.run(
        TelegramHttpsTransport().send_message(
            "synthetic-token",
            900000201,
            "PAPER ORDER NOTIFICATION",
            action,
            timeout_seconds=3.0,
        )
    )

    assert message_id == 707
    assert len(calls) == 1
    assert calls[0]["parse_mode"] is None
    assert calls[0]["chat_id"] == 900000201
    assert calls[0]["text"] == "PAPER ORDER NOTIFICATION"


@pytest.mark.unit
def test_https_adapter_plain_send_has_no_action_or_parse_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_telegram, fake_error, calls = _fake_adapter_modules()
    monkeypatch.setattr(
        telegram_module,
        "_telegram_modules",
        lambda: (fake_telegram, fake_error),
    )

    message_id = asyncio.run(
        TelegramHttpsTransport().send_plain_message(
            "synthetic-token",
            900000201,
            "[READ ONLY - NOT FOR TRADING]",
            timeout_seconds=4.0,
        )
    )

    assert message_id == 707
    assert calls == [
        {
            "chat_id": 900000201,
            "text": "[READ ONLY - NOT FOR TRADING]",
            "parse_mode": None,
            "reply_markup": None,
            "read_timeout": 4.0,
            "write_timeout": 4.0,
            "connect_timeout": 4.0,
            "pool_timeout": 4.0,
        }
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        (FakeProviderError.BadRequest("provider-payload"), TelegramTransportRejected),
        (FakeProviderError.TimedOut("provider-payload"), TelegramDeliveryUnknown),
        (asyncio.CancelledError(), TelegramDeliveryUnknown),
        (RuntimeError("provider-payload"), TelegramDeliveryUnknown),
    ],
)
def test_https_adapter_plain_send_failures_are_one_call_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    effect: BaseException,
    expected: type[Exception],
) -> None:
    fake_telegram, fake_error, calls = _fake_adapter_modules(effect=effect)
    monkeypatch.setattr(
        telegram_module,
        "_telegram_modules",
        lambda: (fake_telegram, fake_error),
    )

    with pytest.raises(expected) as caught:
        asyncio.run(
            TelegramHttpsTransport().send_plain_message(
                "synthetic-token",
                900000201,
                "[READ ONLY - NOT FOR TRADING]",
                timeout_seconds=4.0,
            )
        )

    assert len(calls) == 1
    assert "provider-payload" not in str(caught.value)
    assert "synthetic-token" not in str(caught.value)


@pytest.mark.unit
def test_managed_https_transport_initializes_once_reuses_one_bot_and_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Request:
        def __init__(self) -> None:
            self.index = events.count("request_created")
            events.append("request_created")

        async def shutdown(self) -> None:
            events.append(f"request_{self.index}_shutdown")

    class Bot:
        instances = 0

        def __init__(self, **kwargs: object) -> None:
            assert kwargs["token"] == "synthetic-token"
            self.request = cast(Request, kwargs["request"])
            self.get_updates_request = cast(Request, kwargs["get_updates_request"])
            Bot.instances += 1

        async def initialize(self) -> None:
            events.append("initialize")

        async def shutdown(self) -> None:
            events.append("shutdown")
            await self.request.shutdown()
            await self.get_updates_request.shutdown()

        async def get_me(self, **kwargs: object) -> SimpleNamespace:
            events.append("get_me")
            return SimpleNamespace(id=9001)

        async def get_updates(self, **kwargs: object) -> tuple[object, ...]:
            events.append("get_updates")
            return ()

        async def send_message(self, **kwargs: object) -> SimpleNamespace:
            events.append("send_message")
            return SimpleNamespace(message_id=77)

    telegram = SimpleNamespace(
        Bot=Bot,
        request=SimpleNamespace(HTTPXRequest=Request),
        InlineKeyboardButton=lambda **kwargs: kwargs,
        InlineKeyboardMarkup=lambda rows: rows,
    )
    monkeypatch.setattr(
        telegram_module,
        "_telegram_modules",
        lambda: (telegram, FakeProviderError),
    )

    async def run() -> None:
        transport = TelegramHttpsTransport("synthetic-token")
        async with transport:
            assert (await transport.get_me("synthetic-token", timeout_seconds=5)).id == 9001
            assert (
                await transport.get_raw_updates(
                    "synthetic-token",
                    offset=0,
                    timeout=25,
                    limit=100,
                    allowed_updates=("message", "callback_query"),
                    deadline_seconds=35,
                )
                == ()
            )
            assert (
                await transport.send_plain_message(
                    "synthetic-token", 201, "read only", timeout_seconds=4
                )
                == 77
            )

    asyncio.run(run())
    assert Bot.instances == 1
    assert events == [
        "request_created",
        "request_created",
        "initialize",
        "get_me",
        "get_updates",
        "send_message",
        "shutdown",
        "request_0_shutdown",
        "request_1_shutdown",
    ]


@pytest.mark.unit
@pytest.mark.parametrize("failure", [RuntimeError("startup"), asyncio.CancelledError()])
def test_managed_https_transport_partial_startup_closes_both_requests_once(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    shutdowns: list[int] = []

    class Request:
        next_index = 0

        def __init__(self) -> None:
            self.index = Request.next_index
            Request.next_index += 1

        async def shutdown(self) -> None:
            shutdowns.append(self.index)

    class Bot:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def initialize(self) -> None:
            raise failure

    telegram = SimpleNamespace(
        Bot=Bot,
        request=SimpleNamespace(HTTPXRequest=Request),
    )
    monkeypatch.setattr(
        telegram_module,
        "_telegram_modules",
        lambda: (telegram, FakeProviderError),
    )

    async def run() -> None:
        async with TelegramHttpsTransport("synthetic-token"):
            raise AssertionError("unreachable")

    with pytest.raises(type(failure)):
        asyncio.run(run())
    assert sorted(shutdowns) == [0, 1]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fail_at", "expected_shutdowns"),
    [("second_request", [0]), ("bot", [0, 1])],
)
def test_managed_https_transport_constructor_failure_closes_created_requests(
    monkeypatch: pytest.MonkeyPatch,
    fail_at: str,
    expected_shutdowns: list[int],
) -> None:
    shutdowns: list[int] = []

    class Request:
        next_index = 0

        def __init__(self) -> None:
            self.index = Request.next_index
            Request.next_index += 1
            if fail_at == "second_request" and self.index == 1:
                raise RuntimeError("synthetic request construction failure")

        async def shutdown(self) -> None:
            shutdowns.append(self.index)

    class Bot:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            if fail_at == "bot":
                raise RuntimeError("synthetic Bot construction failure")

    telegram = SimpleNamespace(
        Bot=Bot,
        request=SimpleNamespace(HTTPXRequest=Request),
    )
    monkeypatch.setattr(
        telegram_module,
        "_telegram_modules",
        lambda: (telegram, FakeProviderError),
    )

    with pytest.raises(TelegramTransportRejected):
        asyncio.run(TelegramHttpsTransport("synthetic-token").__aenter__())
    assert shutdowns == expected_shutdowns


@pytest.mark.unit
def test_managed_https_transport_uses_locked_ptb_bot_request_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram = pytest.importorskip(
        "telegram",
        reason="requires the locked optional approval runtime",
    )
    lifecycle: list[str] = []

    async def initialize(bot: object) -> None:
        lifecycle.append("initialize")

    async def shutdown(bot: object) -> None:
        lifecycle.append("shutdown")

    monkeypatch.setattr(telegram.Bot, "initialize", initialize)
    monkeypatch.setattr(telegram.Bot, "shutdown", shutdown)

    async def run() -> None:
        transport = TelegramHttpsTransport("900000001:" + "A" * 35)
        async with transport:
            assert transport._bot is not None
            assert type(transport._request).__name__ == "HTTPXRequest"
            assert type(transport._get_updates_request).__name__ == "HTTPXRequest"

    asyncio.run(run())
    assert telegram.__version__ == "22.8"
    assert lifecycle == ["initialize", "shutdown"]


@pytest.mark.unit
def test_managed_https_transport_body_cancellation_shuts_down_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle: list[str] = []

    class Request:
        async def shutdown(self) -> None:
            lifecycle.append("request_shutdown")

    class Bot:
        def __init__(
            self,
            *,
            token: str,
            request: Request,
            get_updates_request: Request,
        ) -> None:
            del token
            self._requests = (request, get_updates_request)

        async def initialize(self) -> None:
            lifecycle.append("initialize")

        async def shutdown(self) -> None:
            lifecycle.append("shutdown")
            await asyncio.gather(*(request.shutdown() for request in self._requests))

    telegram = SimpleNamespace(
        Bot=Bot,
        request=SimpleNamespace(HTTPXRequest=Request),
    )
    monkeypatch.setattr(
        telegram_module,
        "_telegram_modules",
        lambda: (telegram, FakeProviderError),
    )

    async def run() -> None:
        async with TelegramHttpsTransport("synthetic-token"):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    assert lifecycle == [
        "initialize",
        "shutdown",
        "request_shutdown",
        "request_shutdown",
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "effect",
    [
        FakeProviderError.TimedOut("timeout"),
        FakeProviderError.NetworkError("disconnect"),
        asyncio.CancelledError(),
        RuntimeError("unexpected provider detail"),
    ],
)
def test_https_adapter_maps_every_ambiguous_post_start_exception_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
    effect: BaseException,
) -> None:
    fake_telegram, fake_error, calls = _fake_adapter_modules(effect=effect)
    monkeypatch.setattr(
        telegram_module,
        "_telegram_modules",
        lambda: (fake_telegram, fake_error),
    )
    action = TelegramOutboundAction(
        label="Approve PAPER",
        callback_data=TOKEN_VALUE,
        url=None,
    )

    with pytest.raises(TelegramDeliveryUnknown) as exc_info:
        asyncio.run(
            TelegramHttpsTransport().send_message(
                "synthetic-token",
                900000201,
                "PAPER ORDER NOTIFICATION",
                action,
                timeout_seconds=3.0,
            )
        )

    assert len(calls) == 1
    assert str(exc_info.value) == ""
    assert exc_info.value.__cause__ is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "effect",
    [
        FakeProviderError.BadRequest("rejected"),
        FakeProviderError.Forbidden("forbidden"),
        FakeProviderError.InvalidToken("invalid"),
    ],
)
def test_https_adapter_reserves_rejected_for_definitive_provider_responses(
    monkeypatch: pytest.MonkeyPatch,
    effect: BaseException,
) -> None:
    fake_telegram, fake_error, calls = _fake_adapter_modules(effect=effect)
    monkeypatch.setattr(
        telegram_module,
        "_telegram_modules",
        lambda: (fake_telegram, fake_error),
    )
    action = TelegramOutboundAction(
        label="Approve PAPER",
        callback_data=TOKEN_VALUE,
        url=None,
    )

    with pytest.raises(TelegramTransportRejected) as exc_info:
        asyncio.run(
            TelegramHttpsTransport().send_message(
                "synthetic-token",
                900000201,
                "PAPER ORDER NOTIFICATION",
                action,
                timeout_seconds=3.0,
            )
        )

    assert len(calls) == 1
    assert str(exc_info.value) == ""
    assert exc_info.value.__cause__ is None


@pytest.mark.unit
@pytest.mark.parametrize("message_id", [None, 0, -1, True, "707"])
def test_https_adapter_maps_unusable_success_result_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
    message_id: object,
) -> None:
    fake_telegram, fake_error, calls = _fake_adapter_modules(message_id=message_id)
    monkeypatch.setattr(
        telegram_module,
        "_telegram_modules",
        lambda: (fake_telegram, fake_error),
    )
    action = TelegramOutboundAction(
        label="Approve PAPER",
        callback_data=TOKEN_VALUE,
        url=None,
    )

    with pytest.raises(TelegramDeliveryUnknown):
        asyncio.run(
            TelegramHttpsTransport().send_message(
                "synthetic-token",
                900000201,
                "PAPER ORDER NOTIFICATION",
                action,
                timeout_seconds=3.0,
            )
        )

    assert len(calls) == 1
