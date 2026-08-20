"""Display-only Robinhood reads over the authorized Telegram poller.

This is the sole P05-T5/P06-T2 composition boundary.  It accepts only the
typed updates already authorized by :mod:`ainvest.approval.telegram_updates`,
opens one pinned read gateway per admitted query, and attempts at most one
plain-text reply.  It has no approval, mutation, Paper-order, or generic MCP
capability surface.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import signal
import sqlite3
import stat
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Never, Protocol
from urllib.parse import quote

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.telegram import TelegramEnvironment, TelegramHttpsTransport
from ainvest.approval.telegram_updates import (
    TELEGRAM_HANDLER_DEADLINE_SECONDS,
    AsyncioTelegramPollingControl,
    AuthorizedCallbackUpdate,
    AuthorizedTelegramUpdate,
    AuthorizedTextUpdate,
    TelegramHandlerDisposition,
    TelegramHttpsUpdateTransport,
    TelegramIdentityTransport,
    TelegramLongPoller,
    TelegramPollingControl,
    TelegramUpdateTransport,
)
from ainvest.config import (
    RobinhoodAccountSecretInvalid,
    Settings,
    TelegramBotSettings,
    TradingMode,
    load_robinhood_read_account_number,
    load_settings,
)
from ainvest.config.errors import ConfigError
from ainvest.db import create_db_engine, create_session_factory
from ainvest.execution.robinhood.composition import ComposedReadGateway, open_read_gateway
from ainvest.execution.robinhood.display import (
    AdjustmentType,
    DisplaySuccess,
    OrderView,
    RobinhoodDisplayService,
)
from ainvest.execution.robinhood.errors import GatewayReadError, GatewayReadErrorCode
from ainvest.execution.robinhood.mappers import RobinhoodMappingError
from ainvest.execution.robinhood.read_models import (
    FundamentalBounds,
    HistoricalBounds,
    HistoricalInterval,
    ReportingPeriod,
)
from ainvest.schemas.common import Symbol

TELEGRAM_QUERY_GATEWAY_SECONDS: Final[float] = 12.0
TELEGRAM_QUERY_SEND_SECONDS: Final[float] = 4.0
TELEGRAM_QUERY_LOCAL_MARGIN_SECONDS: Final[float] = 4.0
TELEGRAM_QUERY_MESSAGE_LIMIT: Final[int] = 3_500
TELEGRAM_QUERY_RATE_WINDOW_SECONDS: Final[float] = 60.0
TELEGRAM_QUERY_RATE_LIMIT: Final[int] = 6
if (
    TELEGRAM_QUERY_GATEWAY_SECONDS
    + TELEGRAM_QUERY_SEND_SECONDS
    + TELEGRAM_QUERY_LOCAL_MARGIN_SECONDS
    != TELEGRAM_HANDLER_DEADLINE_SECONDS
):
    raise RuntimeError("Telegram query budgets must exactly fill the P05-T5 handler deadline")
_ALEMBIC_HEAD: Final[str] = "bf42c70e30d1"
_REQUIRED_TABLES: Final[frozenset[str]] = frozenset(
    {"alembic_version", "telegram_poll_states", "telegram_processed_updates"}
)
_READ_ONLY_HEADER: Final[str] = "[READ ONLY - NOT FOR TRADING]"
_STRICT_MESSAGE = TypeAdapter(Symbol)


class TelegramQueryCommand(StrEnum):
    HELP = "help"
    RH_STATUS = "rh_status"
    ACCOUNTS = "accounts"
    PORTFOLIO = "portfolio"
    POSITIONS = "positions"
    ORDERS = "orders"
    QUOTES = "quotes"
    PRICEBOOK = "pricebook"
    TRADABILITY = "tradability"
    HISTORY = "history"
    FUNDAMENTALS = "fundamentals"
    FINANCIALS = "financials"


class TelegramQuery(BaseModel):
    """Typed result of the exact first-release command grammar."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command: TelegramQueryCommand
    symbols: tuple[Symbol, ...] = ()
    order_view: OrderView | None = None
    history_start: str | None = None
    history_end: str | None = None
    historical_interval: HistoricalInterval | None = None
    fundamental_bounds: FundamentalBounds | None = None
    reporting_period: ReportingPeriod | None = None
    financial_limit: int | None = Field(default=None, ge=1, le=4)

    @model_validator(mode="after")
    def _exact_shape(self) -> TelegramQuery:
        command = self.command
        if len(self.symbols) != len(set(self.symbols)):
            raise ValueError("symbols must be unique")
        if command in {
            TelegramQueryCommand.HELP,
            TelegramQueryCommand.RH_STATUS,
            TelegramQueryCommand.ACCOUNTS,
            TelegramQueryCommand.PORTFOLIO,
            TelegramQueryCommand.POSITIONS,
        }:
            expected = not self.symbols and all(
                item is None
                for item in (
                    self.order_view,
                    self.history_start,
                    self.history_end,
                    self.historical_interval,
                    self.fundamental_bounds,
                    self.reporting_period,
                    self.financial_limit,
                )
            )
        elif command is TelegramQueryCommand.ORDERS:
            expected = (
                self.order_view is not None
                and len(self.symbols) <= 1
                and self.history_start is None
                and self.history_end is None
                and self.historical_interval is None
                and self.fundamental_bounds is None
                and self.reporting_period is None
                and self.financial_limit is None
            )
        elif command in {TelegramQueryCommand.QUOTES, TelegramQueryCommand.TRADABILITY}:
            expected = 1 <= len(self.symbols) <= 5 and self._only_symbols()
        elif command is TelegramQueryCommand.PRICEBOOK:
            expected = 1 <= len(self.symbols) <= 2 and self._only_symbols()
        elif command is TelegramQueryCommand.HISTORY:
            expected = (
                len(self.symbols) == 1
                and self.history_start is not None
                and self.history_end is not None
                and self.historical_interval is not None
                and self.order_view is None
                and self.fundamental_bounds is None
                and self.reporting_period is None
                and self.financial_limit is None
            )
        elif command is TelegramQueryCommand.FUNDAMENTALS:
            expected = (
                len(self.symbols) == 1
                and self.order_view is None
                and self.history_start is None
                and self.history_end is None
                and self.historical_interval is None
                and self.reporting_period is None
                and self.financial_limit is None
            )
        else:
            expected = (
                len(self.symbols) == 1
                and self.reporting_period is not None
                and self.financial_limit is not None
                and self.order_view is None
                and self.history_start is None
                and self.history_end is None
                and self.historical_interval is None
                and self.fundamental_bounds is None
            )
        if not expected:
            raise ValueError("query fields do not match the command")
        return self

    def _only_symbols(self) -> bool:
        return all(
            item is None
            for item in (
                self.order_view,
                self.history_start,
                self.history_end,
                self.historical_interval,
                self.fundamental_bounds,
                self.reporting_period,
                self.financial_limit,
            )
        )


class TelegramQueryErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    retryable: bool


class TelegramQueryError(BaseModel):
    """Telegram-owned deterministic error wire."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["error"] = "error"
    command: TelegramQueryCommand | None
    error: TelegramQueryErrorDetail


class TelegramQueryInputError(ValueError):
    def __init__(self, command: TelegramQueryCommand | None) -> None:
        super().__init__("invalid_command")
        self.command = command


class TelegramQueryInternalError(Exception):
    """Fixed local failure with no untrusted detail."""


class AccountSecretUnavailable(Exception):
    """The account-bound command has no approved server-side account secret."""


class AccountSecretMissing(AccountSecretUnavailable):
    """The lazy READ_BROKER account source has no configured value."""


class AccountSecretInvalid(AccountSecretUnavailable):
    """The lazy READ_BROKER account source failed strict validation."""


class TelegramPlainMessageTransport(Protocol):
    async def send_plain_message(
        self,
        token: str,
        chat_id: int,
        text: str,
        *,
        timeout_seconds: float,
    ) -> int: ...


class TelegramDisplayQueryPort(Protocol):
    async def execute(self, query: TelegramQuery) -> DisplaySuccess: ...


GatewayContextFactory = Callable[[], AbstractAsyncContextManager[ComposedReadGateway]]
AccountSecretLoader = Callable[[], SecretStr | None]
TelegramTransportContextFactory = Callable[
    [str], AbstractAsyncContextManager[TelegramHttpsTransport]
]
WallClock = Callable[[], datetime]
MonotonicClock = Callable[[], float]
OutcomeSink = Callable[[str, bool], None]


_HISTORY_WINDOWS: Final[dict[str, tuple[timedelta, HistoricalInterval]]] = {
    "1d": (timedelta(hours=24), HistoricalInterval.MINUTE_5),
    "5d": (timedelta(hours=120), HistoricalInterval.MINUTE_30),
    "1m": (timedelta(hours=720), HistoricalInterval.HOUR),
    "3m": (timedelta(hours=2_160), HistoricalInterval.DAY),
    "1y": (timedelta(hours=8_760), HistoricalInterval.DAY),
}
_ACCOUNT_COMMANDS: Final[frozenset[TelegramQueryCommand]] = frozenset(
    {
        TelegramQueryCommand.PORTFOLIO,
        TelegramQueryCommand.POSITIONS,
        TelegramQueryCommand.ORDERS,
        TelegramQueryCommand.TRADABILITY,
    }
)
_HELP_MESSAGE: Final[str] = "\n".join(
    (
        "/help",
        "/rh_status",
        "/accounts",
        "/portfolio",
        "/positions",
        "/orders open|closed [SYMBOL]",
        "/quotes SYMBOL [SYMBOL ...]",
        "/pricebook SYMBOL [SYMBOL]",
        "/tradability SYMBOL [SYMBOL ...]",
        "/history SYMBOL 1d|5d|1m|3m|1y",
        "/fundamentals SYMBOL [regular|trading|extended|24_5]",
        "/financials SYMBOL [quarterly|annual] [1|2|3|4]",
    )
)
_RENDER_FAILED_MESSAGE: Final[str] = (
    _READ_ONLY_HEADER
    + "\n"
    + '{"schema_version":"1.0","kind":"error","command":null,'
    + '"error":{"code":"render_failed","retryable":false}}'
)


def parse_telegram_query(
    text: str,
    *,
    clock: WallClock = lambda: datetime.now(UTC),
) -> TelegramQuery:
    """Parse only the fixed ASCII/single-space Telegram grammar."""
    raw_first = text.split(" ", maxsplit=1)[0]
    command = _command_from_token(raw_first)
    if (
        not text
        or not text.isascii()
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in text)
        or text.startswith(" ")
        or text.endswith(" ")
        or "  " in text
    ):
        raise TelegramQueryInputError(command)
    tokens = text.split(" ")
    command = _command_from_token(tokens[0])
    if command is None:
        raise TelegramQueryInputError(None)
    arguments = tokens[1:]
    try:
        if command in {
            TelegramQueryCommand.HELP,
            TelegramQueryCommand.RH_STATUS,
            TelegramQueryCommand.ACCOUNTS,
            TelegramQueryCommand.PORTFOLIO,
            TelegramQueryCommand.POSITIONS,
        }:
            if arguments:
                raise TelegramQueryInputError(command)
            return TelegramQuery(command=command)
        if command is TelegramQueryCommand.ORDERS:
            if len(arguments) not in {1, 2} or arguments[0] not in {"open", "closed"}:
                raise TelegramQueryInputError(command)
            symbols = () if len(arguments) == 1 else _symbols(arguments[1:], limit=1)
            return TelegramQuery(
                command=command,
                symbols=symbols,
                order_view=OrderView(arguments[0]),
            )
        if command is TelegramQueryCommand.QUOTES:
            return TelegramQuery(command=command, symbols=_symbols(arguments, limit=5))
        if command is TelegramQueryCommand.PRICEBOOK:
            return TelegramQuery(command=command, symbols=_symbols(arguments, limit=2))
        if command is TelegramQueryCommand.TRADABILITY:
            return TelegramQuery(command=command, symbols=_symbols(arguments, limit=5))
        if command is TelegramQueryCommand.HISTORY:
            if len(arguments) != 2 or arguments[1] not in _HISTORY_WINDOWS:
                raise TelegramQueryInputError(command)
            symbols = _symbols(arguments[:1], limit=1)
            duration, interval = _HISTORY_WINDOWS[arguments[1]]
            end = _history_time(clock())
            return TelegramQuery(
                command=command,
                symbols=symbols,
                history_start=_rfc3339(end - duration),
                history_end=_rfc3339(end),
                historical_interval=interval,
            )
        if command is TelegramQueryCommand.FUNDAMENTALS:
            if len(arguments) not in {1, 2}:
                raise TelegramQueryInputError(command)
            bounds = None if len(arguments) == 1 else FundamentalBounds(arguments[1])
            return TelegramQuery(
                command=command,
                symbols=_symbols(arguments[:1], limit=1),
                fundamental_bounds=bounds,
            )
        if command is TelegramQueryCommand.FINANCIALS:
            if not 1 <= len(arguments) <= 3:
                raise TelegramQueryInputError(command)
            period = ReportingPeriod.QUARTERLY
            limit = 4
            if len(arguments) >= 2:
                period = ReportingPeriod(arguments[1])
            if len(arguments) == 3:
                if arguments[2] not in {"1", "2", "3", "4"}:
                    raise TelegramQueryInputError(command)
                limit = int(arguments[2])
            return TelegramQuery(
                command=command,
                symbols=_symbols(arguments[:1], limit=1),
                reporting_period=period,
                financial_limit=limit,
            )
    except (ValidationError, ValueError):
        raise TelegramQueryInputError(command) from None
    raise TelegramQueryInputError(command)


def _command_from_token(token: str) -> TelegramQueryCommand | None:
    if not token.startswith("/"):
        return None
    try:
        return TelegramQueryCommand(token[1:])
    except ValueError:
        return None


def _symbols(values: Sequence[str], *, limit: int) -> tuple[Symbol, ...]:
    if not values or len(values) > limit or len(values) != len(set(values)):
        raise TelegramQueryInputError(None)
    try:
        return tuple(_STRICT_MESSAGE.validate_python(value) for value in values)
    except ValidationError:
        raise TelegramQueryInputError(None) from None


def _history_time(value: datetime) -> datetime:
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(UTC).replace(microsecond=0)
    except Exception:
        raise TelegramQueryInternalError from None


def _rfc3339(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


class ReadGatewayQueryExecutor:
    """Account-owning READ_BROKER subcomposition over named display calls."""

    __slots__ = ("_account_loader", "_clock", "_gateway_factory")

    def __init__(
        self,
        account_loader: AccountSecretLoader,
        *,
        gateway_factory: GatewayContextFactory = lambda: open_read_gateway(),
        clock: WallClock = lambda: datetime.now(UTC),
    ) -> None:
        self._account_loader = account_loader
        self._gateway_factory = gateway_factory
        self._clock = clock

    async def execute(self, query: TelegramQuery) -> DisplaySuccess:
        account = self._account() if query.command in _ACCOUNT_COMMANDS else None
        async with self._gateway_factory() as composed:
            service = RobinhoodDisplayService(composed.client, clock=self._clock)
            return await self._execute_named(service, query, account=account)

    async def _execute_named(
        self,
        service: RobinhoodDisplayService,
        query: TelegramQuery,
        *,
        account: str | None,
    ) -> DisplaySuccess:
        command = query.command
        if command is TelegramQueryCommand.RH_STATUS:
            return service.status()
        if command is TelegramQueryCommand.ACCOUNTS:
            return await service.accounts()
        if command is TelegramQueryCommand.PORTFOLIO:
            assert account is not None
            return await service.portfolio(account)
        if command is TelegramQueryCommand.POSITIONS:
            assert account is not None
            return await service.positions(account)
        if command is TelegramQueryCommand.ORDERS:
            assert query.order_view is not None
            filters = {} if not query.symbols else {"symbol": query.symbols[0]}
            assert account is not None
            return await service.orders(account, view=query.order_view, filters=filters)
        if command is TelegramQueryCommand.QUOTES:
            return await service.quotes(query.symbols)
        if command is TelegramQueryCommand.PRICEBOOK:
            return await service.price_book(query.symbols)
        if command is TelegramQueryCommand.TRADABILITY:
            assert account is not None
            return await service.tradability(account, query.symbols)
        if command is TelegramQueryCommand.HISTORY:
            assert query.history_start is not None
            assert query.history_end is not None
            assert query.historical_interval is not None
            return await service.historicals(
                query.symbols,
                start_time=query.history_start,
                end_time=query.history_end,
                interval=query.historical_interval,
                bounds=HistoricalBounds.REGULAR,
                adjustment_type=AdjustmentType.SPLIT,
            )
        if command is TelegramQueryCommand.FUNDAMENTALS:
            return await service.fundamentals(query.symbols, bounds=query.fundamental_bounds)
        if command is TelegramQueryCommand.FINANCIALS:
            assert query.reporting_period is not None
            assert query.financial_limit is not None
            return await service.financials(
                query.symbols,
                period=query.reporting_period,
                limit=query.financial_limit,
            )
        raise TelegramQueryInternalError

    def _account(self) -> str:
        try:
            account = self._account_loader()
        except RobinhoodAccountSecretInvalid:
            raise AccountSecretInvalid from None
        except Exception:
            raise AccountSecretInvalid from None
        if account is None:
            raise AccountSecretMissing
        return account.get_secret_value()


@dataclass(slots=True)
class _RateWindow:
    opened_at: float
    admitted_update_ids: set[int] = field(default_factory=set)


class _RateGate:
    __slots__ = ("_clock", "_in_flight", "_windows")

    def __init__(self, clock: MonotonicClock) -> None:
        self._clock = clock
        self._windows: dict[tuple[TelegramEnvironment, int], _RateWindow] = {}
        self._in_flight: set[tuple[TelegramEnvironment, int]] = set()

    def enter(self, update: AuthorizedTextUpdate) -> bool:
        key = (update.environment, update.sender_user_id)
        now = self._clock()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            raise TelegramQueryInternalError
        if key in self._in_flight:
            return False
        window = self._windows.get(key)
        if window is None or now - window.opened_at >= TELEGRAM_QUERY_RATE_WINDOW_SECONDS:
            window = _RateWindow(opened_at=float(now))
            self._windows[key] = window
        if update.update_id not in window.admitted_update_ids:
            if len(window.admitted_update_ids) >= TELEGRAM_QUERY_RATE_LIMIT:
                return False
            window.admitted_update_ids.add(update.update_id)
        self._in_flight.add(key)
        return True

    def leave(self, update: AuthorizedTextUpdate) -> None:
        self._in_flight.discard((update.environment, update.sender_user_id))


class TelegramQueryHandler:
    """P05-T5 handler implementing bounded query, render, rate, and delivery."""

    __slots__ = (
        "_clock",
        "_executor",
        "_outcome_sink",
        "_rate",
        "_token",
        "_transport",
    )

    def __init__(
        self,
        *,
        token: SecretStr,
        transport: TelegramPlainMessageTransport,
        executor: TelegramDisplayQueryPort,
        clock: WallClock = lambda: datetime.now(UTC),
        monotonic: MonotonicClock = time.monotonic,
        outcome_sink: OutcomeSink = lambda code, retryable: None,
    ) -> None:
        self._token = token
        self._transport = transport
        self._executor = executor
        self._clock = clock
        self._rate = _RateGate(monotonic)
        self._outcome_sink = outcome_sink

    async def handle(self, update: AuthorizedTelegramUpdate) -> TelegramHandlerDisposition:
        if isinstance(update, AuthorizedCallbackUpdate):
            # Query-only composition must park callbacks until P05-T1 owns a
            # composite router. Terminalizing here would irreversibly consume it.
            return TelegramHandlerDisposition.RETRY_LATER
        try:
            admitted = self._rate.enter(update)
        except TelegramQueryInternalError:
            return await self._send_error(update, None, "internal_error", retryable=False)
        if not admitted:
            self._observe("rate_limited", retryable=True)
            return TelegramHandlerDisposition.TERMINAL_HANDLED
        try:
            return await self._handle_admitted(update)
        finally:
            self._rate.leave(update)

    async def _handle_admitted(self, update: AuthorizedTextUpdate) -> TelegramHandlerDisposition:
        command: TelegramQueryCommand | None = None
        try:
            query = parse_telegram_query(update.text.get_secret_value(), clock=self._clock)
            command = query.command
        except TelegramQueryInputError as exc:
            return await self._send_error(update, exc.command, "invalid_command", retryable=False)
        except TelegramQueryInternalError:
            return await self._send_error(update, command, "internal_error", retryable=False)
        except asyncio.CancelledError:
            return TelegramHandlerDisposition.RETRY_LATER

        if query.command is TelegramQueryCommand.HELP:
            return await self._attempt_send(update, _HELP_MESSAGE)
        try:
            async with asyncio.timeout(TELEGRAM_QUERY_GATEWAY_SECONDS):
                success = await self._executor.execute(query)
        except AccountSecretMissing:
            return await self._send_error(
                update, command, "account_secret_missing", retryable=False
            )
        except AccountSecretInvalid:
            return await self._send_error(
                update, command, "account_secret_invalid", retryable=False
            )
        except GatewayReadError as exc:
            return await self._send_error(update, command, exc.code.value, retryable=exc.retryable)
        except RobinhoodMappingError as exc:
            return await self._send_error(update, command, exc.code.value, retryable=False)
        except TimeoutError:
            return await self._send_error(
                update, command, GatewayReadErrorCode.TIMEOUT.value, retryable=True
            )
        except asyncio.CancelledError:
            return TelegramHandlerDisposition.RETRY_LATER
        except Exception:
            return await self._send_error(update, command, "internal_error", retryable=False)

        rendered = _render_success(success)
        if rendered is None:
            return await self._send_error(update, command, "render_failed", retryable=False)
        if len(rendered) > TELEGRAM_QUERY_MESSAGE_LIMIT:
            return await self._send_error(update, command, "result_too_large", retryable=False)
        return await self._attempt_send(update, rendered)

    async def _send_error(
        self,
        update: AuthorizedTextUpdate,
        command: TelegramQueryCommand | None,
        code: str,
        *,
        retryable: bool,
    ) -> TelegramHandlerDisposition:
        message = _render_error(command, code, retryable=retryable)
        return await self._attempt_send(update, message)

    async def _attempt_send(
        self, update: AuthorizedTextUpdate, message: str
    ) -> TelegramHandlerDisposition:
        # Crossing this line means an ambiguous provider outcome may have sent
        # the message.  Every exception thereafter is terminal and never retried.
        token = self._token.get_secret_value()
        try:
            async with asyncio.timeout(TELEGRAM_QUERY_SEND_SECONDS):
                message_id = await self._transport.send_plain_message(
                    token,
                    update.chat_id,
                    message,
                    timeout_seconds=TELEGRAM_QUERY_SEND_SECONDS,
                )
                if (
                    not isinstance(message_id, int)
                    or isinstance(message_id, bool)
                    or message_id <= 0
                ):
                    raise ValueError("invalid transport result")
        except asyncio.CancelledError:
            self._observe("send_failed", retryable=False)
            return TelegramHandlerDisposition.TERMINAL_HANDLED
        except Exception:
            self._observe("send_failed", retryable=False)
            return TelegramHandlerDisposition.TERMINAL_HANDLED
        finally:
            token = ""
        return TelegramHandlerDisposition.TERMINAL_HANDLED

    def _observe(self, code: str, *, retryable: bool) -> None:
        try:
            self._outcome_sink(code, retryable)
        except Exception:
            # Observability is deliberately best effort and cannot alter delivery.
            return


def _render_success(success: DisplaySuccess) -> str | None:
    try:
        body = json.dumps(
            success.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
        )
    except Exception:
        return None
    return f"{_READ_ONLY_HEADER}\n{body}"


def _render_error(
    command: TelegramQueryCommand | None,
    code: str,
    *,
    retryable: bool,
) -> str:
    try:
        error = TelegramQueryError(
            command=command,
            error=TelegramQueryErrorDetail(code=code, retryable=retryable),
        )
        body = json.dumps(
            error.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        rendered = f"{_READ_ONLY_HEADER}\n{body}"
        if len(rendered) > TELEGRAM_QUERY_MESSAGE_LIMIT:
            return _RENDER_FAILED_MESSAGE
        return rendered
    except Exception:
        return _RENDER_FAILED_MESSAGE


class TelegramReadRunnerFailure(Exception):
    """Fixed, sanitized executable setup failure."""


class _RunnerParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        raise TelegramReadRunnerFailure("invalid_cli_input") from None


def build_parser() -> argparse.ArgumentParser:
    parser = _RunnerParser(
        prog="ainvest-telegram-read",
        description="Run display-only Robinhood Telegram queries. Trading is disabled.",
    )
    parser.add_argument(
        "--environment",
        required=True,
        choices=[item.value for item in TelegramEnvironment],
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--secrets-dir", type=Path)
    return parser


async def run_telegram_read(
    *,
    settings: Settings,
    environment: TelegramEnvironment,
    engine: Engine,
    session_factory: sessionmaker[Session],
    identity_transport: TelegramIdentityTransport,
    update_transport: TelegramUpdateTransport,
    reply_transport: TelegramPlainMessageTransport,
    control: TelegramPollingControl,
    gateway_factory: GatewayContextFactory = lambda: open_read_gateway(),
    clock: WallClock = lambda: datetime.now(UTC),
    monotonic: MonotonicClock = time.monotonic,
    account_loader: AccountSecretLoader = lambda: None,
) -> None:
    """Run the real P05-T5 poller and always dispose its database engine."""
    try:
        config = _selected_bot(settings, environment)
        _require_runtime(settings, config)
        assert config.bot_token is not None
        executor = ReadGatewayQueryExecutor(
            account_loader,
            gateway_factory=gateway_factory,
            clock=clock,
        )
        handler = TelegramQueryHandler(
            token=config.bot_token,
            transport=reply_transport,
            executor=executor,
            clock=clock,
            monotonic=monotonic,
        )
        poller = TelegramLongPoller(
            settings=settings,
            environment=environment,
            session_factory=session_factory,
            identity_transport=identity_transport,
            update_transport=update_transport,
            handler=handler,
        )
        await poller.run(control)
    finally:
        engine.dispose()


def _selected_bot(settings: Settings, environment: TelegramEnvironment) -> TelegramBotSettings:
    return (
        settings.telegram_staging
        if environment is TelegramEnvironment.STAGING
        else settings.telegram_production
    )


def _require_runtime(settings: Settings, config: TelegramBotSettings) -> None:
    if settings.trading_mode is not TradingMode.PAPER or settings.live_trading_enabled:
        raise TelegramReadRunnerFailure("paper_mode_required")
    if (
        not config.enabled
        or config.bot_token is None
        or config.expected_bot_id is None
        or not config.allowed_recipients
    ):
        raise TelegramReadRunnerFailure("telegram_configuration_incomplete")


def _require_migrated_sqlite(path: Path) -> None:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise TelegramReadRunnerFailure("database_invalid")
        uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            revisions = {
                row[0] for row in connection.execute("SELECT version_num FROM alembic_version")
            }
    except TelegramReadRunnerFailure:
        raise
    except Exception:
        raise TelegramReadRunnerFailure("database_invalid") from None
    if not tables >= _REQUIRED_TABLES or revisions != {_ALEMBIC_HEAD}:
        raise TelegramReadRunnerFailure("database_migration_required")


async def _run_cli(
    namespace: argparse.Namespace,
    *,
    transport_factory: TelegramTransportContextFactory = lambda token: TelegramHttpsTransport(
        token
    ),
    gateway_factory: GatewayContextFactory = lambda: open_read_gateway(),
) -> None:
    try:
        settings = load_settings(
            env_file=namespace.env_file,
            secrets_dir=namespace.secrets_dir,
        )
    except ConfigError:
        raise TelegramReadRunnerFailure("configuration_invalid") from None
    environment = TelegramEnvironment(namespace.environment)
    config = _selected_bot(settings, environment)
    _require_runtime(settings, config)
    _require_migrated_sqlite(namespace.database)
    stop = asyncio.Event()
    control = AsyncioTelegramPollingControl(stop)
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for item in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(item, stop.set)
            installed.append(item)
        except (NotImplementedError, RuntimeError):
            continue
    try:
        assert config.bot_token is not None
        token = config.bot_token.get_secret_value()
        async with transport_factory(token) as transport:
            engine = create_db_engine(f"sqlite+pysqlite:///{namespace.database.resolve()}")
            session_factory = create_session_factory(engine)
            await run_telegram_read(
                settings=settings,
                environment=environment,
                engine=engine,
                session_factory=session_factory,
                identity_transport=transport,
                update_transport=TelegramHttpsUpdateTransport(transport),
                reply_transport=transport,
                control=control,
                gateway_factory=gateway_factory,
                account_loader=lambda: load_robinhood_read_account_number(
                    env_file=namespace.env_file,
                    secrets_dir=namespace.secrets_dir,
                ),
            )
    finally:
        token = ""
        for item in installed:
            loop.remove_signal_handler(item)


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        namespace = build_parser().parse_args(raw)
        asyncio.run(_run_cli(namespace))
    except (TelegramReadRunnerFailure, KeyboardInterrupt):
        sys.stderr.write('{"code":"telegram_read_failed","status":"error"}\n')
        return 1
    except Exception:
        sys.stderr.write('{"code":"telegram_read_failed","status":"error"}\n')
        return 1
    return 0


__all__ = [
    "TELEGRAM_QUERY_GATEWAY_SECONDS",
    "TELEGRAM_QUERY_LOCAL_MARGIN_SECONDS",
    "TELEGRAM_QUERY_MESSAGE_LIMIT",
    "TELEGRAM_QUERY_RATE_LIMIT",
    "TELEGRAM_QUERY_RATE_WINDOW_SECONDS",
    "TELEGRAM_QUERY_SEND_SECONDS",
    "AccountSecretInvalid",
    "AccountSecretLoader",
    "AccountSecretMissing",
    "AccountSecretUnavailable",
    "GatewayContextFactory",
    "ReadGatewayQueryExecutor",
    "TelegramDisplayQueryPort",
    "TelegramPlainMessageTransport",
    "TelegramQuery",
    "TelegramQueryCommand",
    "TelegramQueryError",
    "TelegramQueryHandler",
    "TelegramQueryInputError",
    "TelegramReadRunnerFailure",
    "TelegramTransportContextFactory",
    "build_parser",
    "main",
    "parse_telegram_query",
    "run_telegram_read",
]
