"""Configuration-to-private-send integration test for P05-T4."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from ainvest.approval.order_hash import attach_order_hash
from ainvest.approval.telegram import (
    TelegramBotIdentity,
    TelegramChatIdentity,
    TelegramDeliveryCode,
    TelegramEnvironment,
    TelegramNotificationCategory,
    TelegramNotificationRequest,
    TelegramNotificationSender,
    TelegramOutboundAction,
)
from ainvest.approval.tokens import OpaqueApprovalToken
from ainvest.config import load_settings
from ainvest.schemas.orders import OrderProposal, order_proposal_example
from ainvest.schemas.risk import RiskDecision

_TOKEN = "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg"


class _FakeHttpsBoundary:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.text: str | None = None

    async def get_me(self, token: str, *, timeout_seconds: float) -> TelegramBotIdentity:
        del token, timeout_seconds
        self.calls.append("get_me")
        return TelegramBotIdentity(id=900000001)

    async def get_chat(
        self, token: str, chat_id: int, *, timeout_seconds: float
    ) -> TelegramChatIdentity:
        del token, timeout_seconds
        self.calls.append("get_chat")
        return TelegramChatIdentity(id=chat_id, type="private")

    async def send_message(
        self,
        token: str,
        chat_id: int,
        text: str,
        action: TelegramOutboundAction,
        *,
        timeout_seconds: float,
    ) -> int:
        del token, chat_id, action, timeout_seconds
        self.calls.append("send_message")
        self.text = text
        return 808


def _proposal() -> OrderProposal:
    payload = order_proposal_example()
    payload["account_scope"] = "paper"
    return OrderProposal.model_validate(attach_order_hash(payload))


def _risk(proposal: OrderProposal) -> RiskDecision:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "risk_decision_id": proposal.risk_decision_id,
        "candidate_id": proposal.candidate_id,
        "outcome": "APPROVED",
        "decided_at": "2026-07-24T18:30:13Z",
        "rule_set_version": "v1",
        "violations": [],
        "reason_code": "ALL_RULES_PASSED",
        "reason": "All configured risk rules passed.",
    }
    return RiskDecision.model_validate(payload)


@pytest.mark.integration
def test_environment_configuration_composes_with_outbound_sender() -> None:
    settings = load_settings(
        environ={
            "TELEGRAM_STAGING__ENABLED": "true",
            "TELEGRAM_STAGING__BOT_TOKEN": "synthetic-integration-token",
            "TELEGRAM_STAGING__EXPECTED_BOT_ID": "900000001",
            "TELEGRAM_STAGING__ALLOWED_RECIPIENTS": (
                '[{"user_id":900000101,"private_chat_id":900000201}]'
            ),
        },
        env_file=None,
    )
    proposal = _proposal()
    request = TelegramNotificationRequest(
        environment=TelegramEnvironment.STAGING,
        category=TelegramNotificationCategory.PAPER,
        intent_correlation_id="notify_01HZYEXAMPLE0001",
        recipient_user_id=900000101,
        recipient_private_chat_id=900000201,
        proposal=proposal,
        risk_decision=_risk(proposal),
        expires_at=datetime(2026, 7, 24, 18, 31, 30, tzinfo=UTC),
        paper_nonce=OpaqueApprovalToken(_TOKEN),
    )
    transport = _FakeHttpsBoundary()

    outcome = asyncio.run(TelegramNotificationSender(settings, transport).send(request))

    assert outcome.code is TelegramDeliveryCode.SENT
    assert outcome.telegram_message_id == 808
    assert transport.calls == ["get_me", "get_chat", "send_message"]
    assert transport.text is not None and transport.text.startswith("PAPER ORDER NOTIFICATION")
    assert _TOKEN not in transport.text
    assert "synthetic-integration-token" not in repr(outcome)
