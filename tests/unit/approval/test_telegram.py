"""Outbound-only Telegram notification tests for P05-T4."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

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
    message_id: int = 77
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
        return self.message_id


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
@pytest.mark.parametrize("effect", [TelegramDeliveryUnknown(), asyncio.CancelledError()])
def test_ambiguous_send_is_unknown_nonretryable_and_never_retried(
    effect: BaseException,
) -> None:
    transport = FakeTelegramTransport(send_effect=effect)

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
    calls: list[dict[str, Any]] = []

    class FakeProviderError:
        class BadRequest(Exception):
            pass

        class TimedOut(Exception):
            pass

        class NetworkError(Exception):
            pass

    class FakeBot:
        def __init__(self, token: str) -> None:
            assert token == "synthetic-token"

        async def send_message(self, **kwargs: Any) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(message_id=707)

    fake_telegram = SimpleNamespace(
        Bot=FakeBot,
        InlineKeyboardButton=lambda **kwargs: kwargs,
        InlineKeyboardMarkup=lambda rows: rows,
    )
    monkeypatch.setattr(
        telegram_module,
        "_telegram_modules",
        lambda: (fake_telegram, FakeProviderError),
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
