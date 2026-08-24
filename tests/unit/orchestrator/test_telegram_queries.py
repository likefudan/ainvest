"""Unit tests for the display-only Telegram query boundary."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr, ValidationError

import ainvest.orchestrator.telegram_queries as query_module
from ainvest.approval.telegram import TelegramEnvironment
from ainvest.approval.telegram_updates import (
    AuthorizedCallbackUpdate,
    AuthorizedTextUpdate,
    TelegramHandlerDisposition,
)
from ainvest.config import RobinhoodAccountSecretInvalid
from ainvest.execution.robinhood.display import (
    AdjustmentType,
    DisplaySuccess,
    RobinhoodDisplayService,
)
from ainvest.execution.robinhood.errors import GatewayReadError, GatewayReadErrorCode
from ainvest.execution.robinhood.mappers import MappingErrorCode, RobinhoodMappingError
from ainvest.execution.robinhood.read_models import HistoricalBounds, HistoricalInterval
from ainvest.orchestrator.telegram_queries import (
    AccountSecretInvalid,
    AccountSecretMissing,
    GatewayContextFactory,
    ReadGatewayQueryExecutor,
    TelegramQuery,
    TelegramQueryCommand,
    TelegramQueryHandler,
    TelegramQueryInputError,
    parse_telegram_query,
)

TOKEN = "900000001:" + "A" * 35
ACCOUNT = "synthetic-account-reference"
NOW = datetime(2026, 3, 8, 10, 30, 45, 987654, tzinfo=timezone(timedelta(hours=-7)))


def _success() -> DisplaySuccess:
    return RobinhoodDisplayService(SimpleNamespace()).status()  # type: ignore[arg-type]


def _update(text: str, *, update_id: int = 1, user_id: int = 101) -> AuthorizedTextUpdate:
    return AuthorizedTextUpdate(
        environment=TelegramEnvironment.STAGING,
        update_id=update_id,
        sender_user_id=user_id,
        chat_id=201,
        message_id=301,
        text=SecretStr(text),
    )


class FakeReplyTransport:
    def __init__(self, effect: BaseException | None = None) -> None:
        self.effect = effect
        self.calls: list[tuple[str, int, str, float]] = []

    async def send_plain_message(
        self,
        token: str,
        chat_id: int,
        text: str,
        *,
        timeout_seconds: float,
    ) -> int:
        self.calls.append((token, chat_id, text, timeout_seconds))
        if self.effect is not None:
            raise self.effect
        return 77


class FakeExecutor:
    def __init__(self, effect: BaseException | None = None) -> None:
        self.effect = effect
        self.calls: list[TelegramQuery] = []

    async def execute(self, query: TelegramQuery) -> DisplaySuccess:
        self.calls.append(query)
        if self.effect is not None:
            raise self.effect
        return _success()


@pytest.mark.parametrize(
    ("text", "command", "symbols"),
    [
        ("/help", TelegramQueryCommand.HELP, ()),
        ("/rh_status", TelegramQueryCommand.RH_STATUS, ()),
        ("/accounts", TelegramQueryCommand.ACCOUNTS, ()),
        ("/portfolio", TelegramQueryCommand.PORTFOLIO, ()),
        ("/positions", TelegramQueryCommand.POSITIONS, ()),
        ("/orders open", TelegramQueryCommand.ORDERS, ()),
        ("/orders closed AAPL", TelegramQueryCommand.ORDERS, ("AAPL",)),
        ("/quotes AAPL MSFT", TelegramQueryCommand.QUOTES, ("AAPL", "MSFT")),
        ("/pricebook AAPL MSFT", TelegramQueryCommand.PRICEBOOK, ("AAPL", "MSFT")),
        ("/tradability AAPL", TelegramQueryCommand.TRADABILITY, ("AAPL",)),
        ("/history AAPL 1d", TelegramQueryCommand.HISTORY, ("AAPL",)),
        ("/fundamentals AAPL", TelegramQueryCommand.FUNDAMENTALS, ("AAPL",)),
        ("/fundamentals AAPL 24_5", TelegramQueryCommand.FUNDAMENTALS, ("AAPL",)),
        ("/financials AAPL", TelegramQueryCommand.FINANCIALS, ("AAPL",)),
        ("/financials AAPL annual 2", TelegramQueryCommand.FINANCIALS, ("AAPL",)),
    ],
)
def test_exact_command_grammar(
    text: str,
    command: TelegramQueryCommand,
    symbols: tuple[str, ...],
) -> None:
    parsed = parse_telegram_query(text, clock=lambda: NOW)
    assert parsed.command is command
    assert parsed.symbols == symbols


@pytest.mark.parametrize(
    "text",
    [
        "help",
        "/Help",
        "/help@bot",
        "/help ",
        " /help",
        "/help  extra",
        "/help\textra",
        "/help\n",
        "/quotes",
        "/quotes aapl",
        "/quotes AAPL AAPL",
        "/quotes A B C D E F",
        "/pricebook A B C",
        "/orders OPEN",
        "/orders open AAPL extra",
        "/history AAPL 7d",
        "/fundamentals AAPL 24_7",
        "/financials AAPL quarterly 0",
        "/financials AAPL quarterly 5",
        '"/accounts"',
        "approve",
        "reject",
        "/unknown provider-payload",
    ],
)
def test_invalid_grammar_is_fixed_and_never_echoes_input(text: str) -> None:
    with pytest.raises(TelegramQueryInputError) as caught:
        parse_telegram_query(text, clock=lambda: NOW)
    assert str(caught.value) == "invalid_command"
    assert "provider-payload" not in str(caught.value)


@pytest.mark.parametrize(
    ("window", "hours", "interval"),
    [
        ("1d", 24, HistoricalInterval.MINUTE_5),
        ("5d", 120, HistoricalInterval.MINUTE_30),
        ("1m", 720, HistoricalInterval.HOUR),
        ("3m", 2_160, HistoricalInterval.DAY),
        ("1y", 8_760, HistoricalInterval.DAY),
    ],
)
def test_history_windows_capture_clock_once_and_map_exactly(
    window: str,
    hours: int,
    interval: HistoricalInterval,
) -> None:
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return NOW

    query = parse_telegram_query(f"/history AAPL {window}", clock=clock)
    end = NOW.astimezone(UTC).replace(microsecond=0)
    assert calls == 1
    assert query.history_end == end.isoformat().replace("+00:00", "Z")
    assert query.history_start == (end - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    assert query.historical_interval is interval


def test_history_rejects_naive_clock_as_internal_not_input() -> None:
    with pytest.raises(query_module.TelegramQueryInternalError):
        parse_telegram_query(
            "/history AAPL 1d",
            clock=lambda: datetime(2026, 3, 8, 10, 30, 45),
        )


@pytest.mark.parametrize(
    ("clock", "window", "start", "end"),
    [
        (
            datetime(2026, 3, 31, 12, tzinfo=UTC),
            "1m",
            "2026-03-01T12:00:00Z",
            "2026-03-31T12:00:00Z",
        ),
        (
            datetime(2024, 2, 29, 12, tzinfo=UTC),
            "1y",
            "2023-03-01T12:00:00Z",
            "2024-02-29T12:00:00Z",
        ),
        (
            datetime(2026, 3, 8, 1, 30, tzinfo=ZoneInfo("America/Los_Angeles")),
            "1d",
            "2026-03-07T09:30:00Z",
            "2026-03-08T09:30:00Z",
        ),
    ],
)
def test_history_windows_are_fixed_duration_across_month_leap_and_dst(
    clock: datetime,
    window: str,
    start: str,
    end: str,
) -> None:
    query = parse_telegram_query(f"/history AAPL {window}", clock=lambda: clock)
    assert query.history_start == start
    assert query.history_end == end


def test_query_model_rejects_cross_command_fields() -> None:
    with pytest.raises(ValidationError):
        TelegramQuery(command=TelegramQueryCommand.HELP, symbols=("AAPL",))
    with pytest.raises(ValidationError):
        TelegramQuery(command=TelegramQueryCommand.QUOTES)


@pytest.mark.parametrize(
    ("text", "expected_method"),
    [
        ("/rh_status", "status"),
        ("/accounts", "accounts"),
        ("/portfolio", "portfolio"),
        ("/positions", "positions"),
        ("/orders open AAPL", "orders"),
        ("/quotes AAPL", "quotes"),
        ("/pricebook AAPL", "price_book"),
        ("/tradability AAPL", "tradability"),
        ("/history AAPL 1d", "historicals"),
        ("/fundamentals AAPL regular", "fundamentals"),
        ("/financials AAPL annual 2", "financials"),
    ],
)
def test_executor_uses_one_named_display_call_and_closes_gateway(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    expected_method: str,
) -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
    lifecycle: list[str] = []

    class Service:
        def __init__(self, client: object, *, clock: object) -> None:
            del client, clock

        def status(self) -> DisplaySuccess:
            calls.append(("status", (), {}))
            return _success()

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            async def operation(*args: object, **kwargs: object) -> DisplaySuccess:
                calls.append((name, args, kwargs))
                return _success()

            return operation

    class Gateway:
        async def __aenter__(self) -> SimpleNamespace:
            lifecycle.append("enter")
            return SimpleNamespace(client=object())

        async def __aexit__(self, *args: object) -> None:
            lifecycle.append("exit")

    monkeypatch.setattr(query_module, "RobinhoodDisplayService", Service)
    executor = ReadGatewayQueryExecutor(
        lambda: SecretStr(ACCOUNT),
        gateway_factory=cast(GatewayContextFactory, Gateway),
        clock=lambda: NOW,
    )
    asyncio.run(executor.execute(parse_telegram_query(text, clock=lambda: NOW)))

    assert lifecycle == ["enter", "exit"]
    assert [name for name, _, _ in calls] == [expected_method]
    if expected_method in {"portfolio", "positions", "orders", "tradability"}:
        assert calls[0][1][0] == ACCOUNT
    if expected_method == "historicals":
        assert calls[0][2] == {
            "start_time": "2026-03-07T17:30:45Z",
            "end_time": "2026-03-08T17:30:45Z",
            "interval": HistoricalInterval.MINUTE_5,
            "bounds": HistoricalBounds.REGULAR,
            "adjustment_type": AdjustmentType.SPLIT,
        }


@pytest.mark.parametrize(
    ("window", "expected_start", "interval"),
    [
        ("1d", "2026-03-07T17:30:45Z", HistoricalInterval.MINUTE_5),
        ("5d", "2026-03-03T17:30:45Z", HistoricalInterval.MINUTE_30),
        ("1m", "2026-02-06T17:30:45Z", HistoricalInterval.HOUR),
        ("3m", "2025-12-08T17:30:45Z", HistoricalInterval.DAY),
        ("1y", "2025-03-08T17:30:45Z", HistoricalInterval.DAY),
    ],
)
def test_executor_passes_each_history_window_as_one_exact_argument_dictionary(
    monkeypatch: pytest.MonkeyPatch,
    window: str,
    expected_start: str,
    interval: HistoricalInterval,
) -> None:
    captured: list[dict[str, object]] = []

    class Service:
        def __init__(self, client: object, *, clock: object) -> None:
            del client, clock

        async def historicals(self, symbols: object, **kwargs: object) -> DisplaySuccess:
            assert symbols == ("AAPL",)
            captured.append(kwargs)
            return _success()

    class Gateway:
        async def __aenter__(self) -> SimpleNamespace:
            return SimpleNamespace(client=object())

        async def __aexit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(query_module, "RobinhoodDisplayService", Service)
    executor = ReadGatewayQueryExecutor(
        lambda: SecretStr(ACCOUNT),
        gateway_factory=cast(GatewayContextFactory, Gateway),
    )
    asyncio.run(
        executor.execute(parse_telegram_query(f"/history AAPL {window}", clock=lambda: NOW))
    )
    assert captured == [
        {
            "start_time": expected_start,
            "end_time": "2026-03-08T17:30:45Z",
            "interval": interval,
            "bounds": HistoricalBounds.REGULAR,
            "adjustment_type": AdjustmentType.SPLIT,
        }
    ]


def test_account_secret_failure_precedes_gateway_open() -> None:
    opened = False

    class Gateway:
        async def __aenter__(self) -> SimpleNamespace:
            nonlocal opened
            opened = True
            return SimpleNamespace(client=object())

        async def __aexit__(self, *args: object) -> None:
            pass

    executor = ReadGatewayQueryExecutor(
        lambda: None, gateway_factory=cast(GatewayContextFactory, Gateway)
    )
    with pytest.raises(AccountSecretMissing):
        asyncio.run(executor.execute(parse_telegram_query("/portfolio")))
    assert opened is False


def test_account_secret_wrapper_is_revealed_only_at_named_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle: list[str] = []

    class TrackedSecret(SecretStr):
        def get_secret_value(self) -> str:
            lifecycle.append("reveal")
            return super().get_secret_value()

    account = TrackedSecret(ACCOUNT)

    def load_account() -> SecretStr:
        lifecycle.append("load_wrapper")
        return account

    class Service:
        def __init__(self, client: object, *, clock: object) -> None:
            del client, clock

        async def portfolio(self, account_number: str) -> DisplaySuccess:
            lifecycle.append("portfolio_call")
            assert account_number == ACCOUNT
            return _success()

    class Gateway:
        async def __aenter__(self) -> SimpleNamespace:
            lifecycle.append("gateway_enter")
            return SimpleNamespace(client=object())

        async def __aexit__(self, *args: object) -> None:
            lifecycle.append("gateway_exit")

    monkeypatch.setattr(query_module, "RobinhoodDisplayService", Service)
    executor = ReadGatewayQueryExecutor(
        load_account,
        gateway_factory=cast(GatewayContextFactory, Gateway),
    )
    asyncio.run(executor.execute(parse_telegram_query("/portfolio")))
    assert lifecycle == [
        "load_wrapper",
        "gateway_enter",
        "reveal",
        "portfolio_call",
        "gateway_exit",
    ]


def test_invalid_lazy_account_does_not_block_status_or_nonaccount_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = 0

    def invalid_account() -> SecretStr | None:
        raise RobinhoodAccountSecretInvalid

    class Service:
        def __init__(self, client: object, *, clock: object) -> None:
            del client, clock

        def status(self) -> DisplaySuccess:
            return _success()

        async def quotes(self, symbols: object) -> DisplaySuccess:
            assert symbols == ("AAPL",)
            return _success()

    class Gateway:
        async def __aenter__(self) -> SimpleNamespace:
            nonlocal opened
            opened += 1
            return SimpleNamespace(client=object())

        async def __aexit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(query_module, "RobinhoodDisplayService", Service)
    executor = ReadGatewayQueryExecutor(
        invalid_account,
        gateway_factory=cast(GatewayContextFactory, Gateway),
    )

    asyncio.run(executor.execute(parse_telegram_query("/rh_status")))
    asyncio.run(executor.execute(parse_telegram_query("/quotes AAPL")))
    with pytest.raises(AccountSecretInvalid):
        asyncio.run(executor.execute(parse_telegram_query("/portfolio")))
    assert opened == 2


@pytest.mark.parametrize(
    ("loader", "code"),
    [
        (lambda: None, "account_secret_missing"),
        (
            lambda: (_ for _ in ()).throw(RobinhoodAccountSecretInvalid()),
            "account_secret_invalid",
        ),
    ],
)
def test_account_source_failure_replies_before_gateway_open(
    loader: query_module.AccountSecretLoader,
    code: str,
) -> None:
    opened = False

    class Gateway:
        async def __aenter__(self) -> SimpleNamespace:
            nonlocal opened
            opened = True
            return SimpleNamespace(client=object())

        async def __aexit__(self, *args: object) -> None:
            pass

    executor = ReadGatewayQueryExecutor(
        loader,
        gateway_factory=cast(GatewayContextFactory, Gateway),
    )
    transport = FakeReplyTransport()
    asyncio.run(_handler(transport, executor).handle(_update("/portfolio")))
    assert _wire(transport)["error"] == {"code": code, "retryable": False}
    assert opened is False


def _handler(
    transport: FakeReplyTransport,
    executor: query_module.TelegramDisplayQueryPort,
    *,
    monotonic: Any = lambda: 0.0,
    clock: Any = lambda: NOW,
    outcome_sink: Any = lambda code, retryable: None,
) -> TelegramQueryHandler:
    return TelegramQueryHandler(
        token=SecretStr(TOKEN),
        transport=transport,
        executor=executor,
        monotonic=monotonic,
        clock=clock,
        outcome_sink=outcome_sink,
    )


def _wire(transport: FakeReplyTransport) -> dict[str, Any]:
    assert len(transport.calls) == 1
    token, chat_id, message, timeout = transport.calls[0]
    assert token == TOKEN
    assert chat_id == 201
    assert timeout == 4.0
    header, body = message.split("\n", maxsplit=1)
    assert header == "[READ ONLY - NOT FOR TRADING]"
    return cast(dict[str, Any], json.loads(body))


def test_help_is_static_and_never_opens_gateway() -> None:
    transport = FakeReplyTransport()
    executor = FakeExecutor(RuntimeError("gateway opened"))
    result = asyncio.run(_handler(transport, executor).handle(_update("/help")))
    assert result is TelegramHandlerDisposition.TERMINAL_HANDLED
    assert executor.calls == []
    assert transport.calls[0][2].startswith("/help\n/rh_status")


@pytest.mark.parametrize(
    ("effect", "code", "retryable"),
    [
        (AccountSecretMissing(), "account_secret_missing", False),
        (AccountSecretInvalid(), "account_secret_invalid", False),
        (GatewayReadError(GatewayReadErrorCode.TIMEOUT), "timeout", True),
        (RobinhoodMappingError(MappingErrorCode.INVALID_VALUE), "invalid_value", False),
        (RuntimeError("provider-payload"), "internal_error", False),
    ],
)
def test_expected_failures_have_exact_sanitized_wire(
    effect: BaseException,
    code: str,
    retryable: bool,
) -> None:
    transport = FakeReplyTransport()
    result = asyncio.run(_handler(transport, FakeExecutor(effect)).handle(_update("/quotes AAPL")))
    assert result is TelegramHandlerDisposition.TERMINAL_HANDLED
    wire = _wire(transport)
    assert wire == {
        "schema_version": "1.0",
        "kind": "error",
        "command": "quotes",
        "error": {"code": code, "retryable": retryable},
    }
    assert "provider-payload" not in transport.calls[0][2]
    assert TOKEN not in transport.calls[0][2]


@pytest.mark.parametrize("code", list(GatewayReadErrorCode))
def test_every_gateway_error_retains_public_code_and_retryability(
    code: GatewayReadErrorCode,
) -> None:
    transport = FakeReplyTransport()
    asyncio.run(
        _handler(transport, FakeExecutor(GatewayReadError(code))).handle(_update("/quotes AAPL"))
    )
    assert _wire(transport)["error"] == {
        "code": code.value,
        "retryable": code is GatewayReadErrorCode.TIMEOUT,
    }


@pytest.mark.parametrize("code", list(MappingErrorCode))
def test_every_mapping_error_retains_public_code(code: MappingErrorCode) -> None:
    transport = FakeReplyTransport()
    asyncio.run(
        _handler(transport, FakeExecutor(RobinhoodMappingError(code))).handle(
            _update("/quotes AAPL")
        )
    )
    assert _wire(transport)["error"] == {"code": code.value, "retryable": False}


def test_invalid_command_replies_without_gateway() -> None:
    transport = FakeReplyTransport()
    executor = FakeExecutor()
    asyncio.run(_handler(transport, executor).handle(_update("approve")))
    assert _wire(transport)["error"] == {"code": "invalid_command", "retryable": False}
    assert executor.calls == []


def test_callback_is_silent_and_never_queries_or_sends() -> None:
    transport = FakeReplyTransport()
    executor = FakeExecutor()
    callback = AuthorizedCallbackUpdate(
        environment=TelegramEnvironment.STAGING,
        update_id=1,
        sender_user_id=101,
        chat_id=201,
        message_id=301,
        callback_query_id=SecretStr("callback-id"),
        callback_data=SecretStr("approve"),
    )
    result = asyncio.run(_handler(transport, executor).handle(callback))
    assert result is TelegramHandlerDisposition.RETRY_LATER
    assert executor.calls == []
    assert transport.calls == []


def test_rate_limit_counts_six_distinct_reply_attempts_and_silences_seventh() -> None:
    transport = FakeReplyTransport()
    executor = FakeExecutor()
    outcomes: list[tuple[str, bool]] = []
    handler = _handler(
        transport,
        executor,
        outcome_sink=lambda code, retryable: outcomes.append((code, retryable)),
    )
    for update_id in range(1, 7):
        asyncio.run(handler.handle(_update("/help", update_id=update_id)))
    asyncio.run(handler.handle(_update("/help", update_id=7)))
    assert len(transport.calls) == 6
    assert executor.calls == []
    assert outcomes == [("rate_limited", True)]


def test_rate_window_resets_at_sixty_seconds() -> None:
    now = 0.0
    transport = FakeReplyTransport()
    handler = _handler(transport, FakeExecutor(), monotonic=lambda: now)
    for update_id in range(1, 8):
        asyncio.run(handler.handle(_update("/help", update_id=update_id)))
    assert len(transport.calls) == 6
    now = 60.0
    asyncio.run(handler.handle(_update("/help", update_id=8)))
    assert len(transport.calls) == 7


def test_rate_limit_resets_when_query_process_restarts() -> None:
    first_transport = FakeReplyTransport()
    second_transport = FakeReplyTransport()
    first = _handler(first_transport, FakeExecutor())
    second = _handler(second_transport, FakeExecutor())
    for update_id in range(1, 8):
        asyncio.run(first.handle(_update("/help", update_id=update_id)))
        asyncio.run(second.handle(_update("/help", update_id=update_id)))
    assert len(first_transport.calls) == 6
    assert len(second_transport.calls) == 6


def test_pre_send_cancellation_retries_without_double_rate_charge() -> None:
    transport = FakeReplyTransport()
    executor = FakeExecutor(asyncio.CancelledError())
    handler = _handler(transport, executor)
    first = asyncio.run(handler.handle(_update("/quotes AAPL", update_id=1)))
    executor.effect = None
    second = asyncio.run(handler.handle(_update("/quotes AAPL", update_id=1)))
    assert first is TelegramHandlerDisposition.RETRY_LATER
    assert second is TelegramHandlerDisposition.TERMINAL_HANDLED
    assert len(executor.calls) == 2
    assert len(transport.calls) == 1


def test_only_one_query_is_in_flight_per_environment_and_user() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingExecutor(FakeExecutor):
        async def execute(self, query: TelegramQuery) -> DisplaySuccess:
            self.calls.append(query)
            started.set()
            await release.wait()
            return _success()

    transport = FakeReplyTransport()
    executor = BlockingExecutor()
    handler = _handler(transport, executor)

    async def run() -> TelegramHandlerDisposition:
        first = asyncio.create_task(handler.handle(_update("/rh_status", update_id=1)))
        await started.wait()
        second = await handler.handle(_update("/rh_status", update_id=2))
        release.set()
        assert await first is TelegramHandlerDisposition.TERMINAL_HANDLED
        return second

    assert asyncio.run(run()) is TelegramHandlerDisposition.TERMINAL_HANDLED
    assert len(executor.calls) == 1
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "effect",
    [asyncio.CancelledError(), TimeoutError(), RuntimeError("provider-payload")],
)
def test_send_attempt_failure_is_terminal_and_never_followed_up(effect: BaseException) -> None:
    transport = FakeReplyTransport(effect)
    outcomes: list[tuple[str, bool]] = []
    result = asyncio.run(
        _handler(
            transport,
            FakeExecutor(),
            outcome_sink=lambda code, retryable: outcomes.append((code, retryable)),
        ).handle(_update("/rh_status"))
    )
    assert result is TelegramHandlerDisposition.TERMINAL_HANDLED
    assert len(transport.calls) == 1
    assert outcomes == [("send_failed", False)]


def test_invalid_transport_message_id_is_terminal_send_failure() -> None:
    transport = FakeReplyTransport()
    transport.send_plain_message = lambda *args, **kwargs: asyncio.sleep(0, result=0)  # type: ignore[method-assign]
    outcomes: list[tuple[str, bool]] = []
    result = asyncio.run(
        _handler(
            transport,
            FakeExecutor(),
            outcome_sink=lambda code, retryable: outcomes.append((code, retryable)),
        ).handle(_update("/rh_status"))
    )
    assert result is TelegramHandlerDisposition.TERMINAL_HANDLED
    assert outcomes == [("send_failed", False)]


def test_oversize_success_becomes_one_bounded_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(query_module, "_render_success", lambda success: "x" * 3_501)
    transport = FakeReplyTransport()
    asyncio.run(_handler(transport, FakeExecutor()).handle(_update("/rh_status")))
    wire = _wire(transport)
    assert wire["error"] == {"code": "result_too_large", "retryable": False}
    assert len(transport.calls[0][2]) <= 3_500


def test_render_failure_uses_prebuilt_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(query_module, "_render_success", lambda success: None)
    monkeypatch.setattr(
        query_module.TelegramQueryError,
        "model_dump",
        lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("provider-payload")),
    )
    transport = FakeReplyTransport()
    asyncio.run(_handler(transport, FakeExecutor()).handle(_update("/rh_status")))
    assert '"code":"render_failed"' in transport.calls[0][2]
    assert "provider-payload" not in transport.calls[0][2]


def test_renderer_ascii_escapes_bidi_separators_quotes_and_backslashes() -> None:
    untrusted = 'left\u202eright\u2028"quote"\\pathé'

    class Payload:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {"text": untrusted}

    rendered = query_module._render_success(cast(DisplaySuccess, Payload()))
    assert rendered is not None
    assert rendered.isascii()
    assert "\\u202e" in rendered
    assert "\\u2028" in rendered
    assert "\\u00e9" in rendered
    assert json.loads(rendered.split("\n", maxsplit=1)[1]) == {"text": untrusted}


def test_message_limit_is_applied_after_ascii_escaping() -> None:
    class Payload:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {"text": "é" * 600}

    class Executor:
        async def execute(self, query: TelegramQuery) -> DisplaySuccess:
            del query
            return cast(DisplaySuccess, Payload())

    transport = FakeReplyTransport()
    asyncio.run(
        TelegramQueryHandler(
            token=SecretStr(TOKEN),
            transport=transport,
            executor=Executor(),
        ).handle(_update("/rh_status"))
    )
    assert _wire(transport)["error"] == {
        "code": "result_too_large",
        "retryable": False,
    }
    assert len(transport.calls[0][2]) <= 3_500


def test_module_has_no_generic_or_trading_surface() -> None:
    source = Path(query_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        ".invoke(",
        "place_order",
        "cancel_order",
        "ainvest.agents",
        "ainvest.risk",
        "ainvest.strategies",
        "yfinance",
        "alpaca",
        "prompt",
    ):
        assert forbidden not in source


def _create_runner_database(path: Path, *, revision: str = "bf42c70e30d1") -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        connection.execute("CREATE TABLE telegram_poll_states (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE telegram_processed_updates (id INTEGER PRIMARY KEY)")


def test_runner_accepts_only_existing_migrated_regular_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "runner.sqlite3"
    _create_runner_database(database)
    query_module._require_migrated_sqlite(database)

    wrong_revision = tmp_path / "wrong.sqlite3"
    _create_runner_database(wrong_revision, revision="stale")
    with pytest.raises(query_module.TelegramReadRunnerFailure) as caught:
        query_module._require_migrated_sqlite(wrong_revision)
    assert str(caught.value) == "database_migration_required"

    link = tmp_path / "linked.sqlite3"
    link.symlink_to(database)
    with pytest.raises(query_module.TelegramReadRunnerFailure) as caught:
        query_module._require_migrated_sqlite(link)
    assert str(caught.value) == "database_invalid"


def test_cli_parser_and_failure_output_never_echo_unknown_argv(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    namespace = query_module.build_parser().parse_args(
        ["--environment", "staging", "--database", "state.sqlite3"]
    )
    assert namespace.environment == "staging"
    assert namespace.database == Path("state.sqlite3")

    secret = "unknown-provider-secret"
    assert query_module.main(["--unknown", secret]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == '{"code":"telegram_read_failed","status":"error"}\n'
    assert secret not in captured.err

    async def fail(namespace: object) -> None:
        del namespace
        raise RuntimeError(secret)

    monkeypatch.setattr(query_module, "_run_cli", fail)
    assert query_module.main(["--environment", "staging", "--database", "state.sqlite3"]) == 1
    captured = capsys.readouterr()
    assert captured.err == '{"code":"telegram_read_failed","status":"error"}\n'
    assert secret not in captured.err
