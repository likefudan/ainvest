"""Machine-readable, display-only Robinhood CLI (P06-T2 Part 1)."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import re
import sys
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Final, Never, TextIO, cast

from pydantic import TypeAdapter, ValidationError

from ainvest.execution.robinhood.composition import open_read_gateway
from ainvest.execution.robinhood.display import (
    AdjustmentType,
    DisplayCommand,
    DisplayPosture,
    DisplaySuccess,
    OrderView,
    RobinhoodDisplayService,
)
from ainvest.execution.robinhood.errors import GatewayReadError
from ainvest.execution.robinhood.mappers import RobinhoodMappingError
from ainvest.execution.robinhood.read_client import RFC3339_DATETIME_PATTERN
from ainvest.execution.robinhood.read_models import (
    FundamentalBounds,
    HistoricalBounds,
    HistoricalInterval,
    ReportingPeriod,
)
from ainvest.schemas.common import Symbol

_SYMBOL = TypeAdapter(Symbol)
_ACCOUNT_PATTERN: Final = re.compile(r"\A[\x21-\x7e]{1,128}\Z")
_MACHINE_FILTER_PATTERN: Final = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")
_ORDER_ID_PATTERN: Final = re.compile(r"\A[A-Za-z0-9_-]{3,128}\Z")

_OPEN_STATES: Final = frozenset({"new", "queued", "confirmed", "unconfirmed", "partially_filled"})
_CLOSED_STATES: Final = frozenset({"filled", "cancelled", "rejected", "failed", "voided"})
_ALL_STATES: Final = _OPEN_STATES | _CLOSED_STATES
_ACCOUNT_COMMANDS: Final = frozenset(
    {
        DisplayCommand.PORTFOLIO,
        DisplayCommand.POSITIONS,
        DisplayCommand.ORDERS,
        DisplayCommand.TRADABILITY,
    }
)


class CliInputError(ValueError):
    """Sanitized CLI validation failure; never includes a rejected value."""


class _WireParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        raise CliInputError from None


def build_parser() -> argparse.ArgumentParser:
    parser = _WireParser(
        prog="ainvest-robinhood-read",
        description="Display normalized Robinhood reads. Trading is disabled.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=_WireParser)

    subparsers.add_parser("status", help="Check pinned gateway readiness")
    subparsers.add_parser("accounts", help="Display non-identifying account eligibility")

    for name in ("portfolio", "positions"):
        child = subparsers.add_parser(name)
        _add_account_input(child)

    orders = subparsers.add_parser("orders")
    _add_account_input(orders)
    orders.add_argument("--view", required=True, choices=[view.value for view in OrderView])
    orders.add_argument("--symbol")
    orders.add_argument("--order-id")
    orders.add_argument("--state", choices=sorted(_ALL_STATES))
    orders.add_argument("--created-at-gte")
    orders.add_argument("--placed-agent")

    quotes = subparsers.add_parser("quotes")
    _add_symbols(quotes)
    price_book = subparsers.add_parser("price-book")
    _add_symbols(price_book)

    tradability = subparsers.add_parser("tradability")
    _add_account_input(tradability)
    _add_symbols(tradability)

    historicals = subparsers.add_parser("historicals")
    _add_symbols(historicals)
    historicals.add_argument("--start-time", required=True)
    historicals.add_argument("--end-time")
    historicals.add_argument("--interval", choices=[item.value for item in HistoricalInterval])
    historicals.add_argument("--bounds", choices=[item.value for item in HistoricalBounds])
    historicals.add_argument("--adjustment-type", choices=[item.value for item in AdjustmentType])

    fundamentals = subparsers.add_parser("fundamentals")
    _add_symbols(fundamentals)
    fundamentals.add_argument("--bounds", choices=[item.value for item in FundamentalBounds])

    financials = subparsers.add_parser("financials")
    _add_symbols(financials)
    financials.add_argument(
        "--period",
        choices=[item.value for item in ReportingPeriod],
        default=ReportingPeriod.QUARTERLY.value,
    )
    financials.add_argument("--limit", type=int, default=4)
    return parser


def _add_account_input(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--account-number-stdin",
        action="store_true",
        help="Read one account number from non-interactive stdin",
    )


def _add_symbols(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbols", nargs="+")


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one display command and emit exactly one JSON document on non-help paths."""
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parsed_command = _parsed_command(raw_argv)
    try:
        namespace = build_parser().parse_args(raw_argv)
        command = DisplayCommand(namespace.command)
        _validate_namespace(namespace, command)
        account_number = (
            _read_account_number(namespace, input_stream) if command in _ACCOUNT_COMMANDS else None
        )
        success = asyncio.run(_execute(namespace, command, account_number))
        rendered = _render_json(success.model_dump(mode="json"))
    except CliInputError:
        _write_failure(error_stream, parsed_command, "invalid_cli_input", retryable=False)
        return 2
    except GatewayReadError as exc:
        _write_failure(error_stream, parsed_command, exc.code.value, retryable=exc.retryable)
        return 1
    except RobinhoodMappingError as exc:
        _write_failure(error_stream, parsed_command, exc.code.value, retryable=False)
        return 1
    except Exception:
        _write_failure(error_stream, parsed_command, "internal_error", retryable=False)
        return 1
    output_stream.write(rendered)
    return 0


def _parsed_command(argv: Sequence[str]) -> DisplayCommand | None:
    if not argv:
        return None
    try:
        return DisplayCommand(argv[0])
    except ValueError:
        return None


def _validate_namespace(namespace: argparse.Namespace, command: DisplayCommand) -> None:
    if hasattr(namespace, "symbols"):
        limit = {
            DisplayCommand.QUOTES: 20,
            DisplayCommand.PRICE_BOOK: 4,
            DisplayCommand.TRADABILITY: 10,
            DisplayCommand.HISTORICALS: 10,
            DisplayCommand.FUNDAMENTALS: 10,
            DisplayCommand.FINANCIALS: 20,
        }[command]
        namespace.symbols = _symbols(namespace.symbols, limit=limit)
    if command is DisplayCommand.HISTORICALS:
        _rfc3339(namespace.start_time)
        if namespace.end_time is not None:
            _rfc3339(namespace.end_time)
            if _rfc3339_value(namespace.end_time) < _rfc3339_value(namespace.start_time):
                raise CliInputError from None
    if command is DisplayCommand.FINANCIALS and (
        isinstance(namespace.limit, bool) or not 1 <= namespace.limit <= 40
    ):
        raise CliInputError from None
    if command is DisplayCommand.ORDERS:
        if namespace.symbol is not None:
            namespace.symbol = _symbols([namespace.symbol], limit=1)[0]
        if namespace.order_id is not None and not _ORDER_ID_PATTERN.fullmatch(namespace.order_id):
            raise CliInputError from None
        if namespace.placed_agent is not None and not _MACHINE_FILTER_PATTERN.fullmatch(
            namespace.placed_agent
        ):
            raise CliInputError from None
        if namespace.created_at_gte is not None:
            _created_at(namespace.created_at_gte)
        states = _OPEN_STATES if namespace.view == OrderView.OPEN.value else _CLOSED_STATES
        if namespace.state is not None and namespace.state not in states:
            raise CliInputError from None


def _symbols(raw: Sequence[str], *, limit: int) -> tuple[Symbol, ...]:
    if not raw or len(raw) > limit or len(raw) != len(set(raw)):
        raise CliInputError from None
    try:
        return tuple(_SYMBOL.validate_python(value) for value in raw)
    except ValidationError:
        raise CliInputError from None


def _rfc3339(value: str) -> None:
    _rfc3339_value(value)


def _rfc3339_value(value: str) -> datetime:
    if RFC3339_DATETIME_PATTERN.fullmatch(value) is None:
        raise CliInputError from None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CliInputError from None
    if parsed.utcoffset() is None:
        raise CliInputError from None
    return parsed


def _created_at(value: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError:
        _rfc3339(value)


def _read_account_number(namespace: argparse.Namespace, stdin: TextIO) -> str:
    flagged = bool(namespace.account_number_stdin)
    interactive = stdin.isatty()
    if flagged == interactive:
        raise CliInputError from None
    if flagged:
        # 128 visible bytes plus one LF is the largest accepted input. Reading
        # one extra character detects every oversized or multi-line form.
        value = stdin.read(130)
        if value.endswith("\n"):
            value = value[:-1]
        if "\n" in value or "\r" in value:
            raise CliInputError from None
    else:
        try:
            value = getpass.getpass("Account number: ")
        except (EOFError, OSError):
            raise CliInputError from None
    if _ACCOUNT_PATTERN.fullmatch(value) is None:
        raise CliInputError from None
    return value


async def _execute(
    namespace: argparse.Namespace,
    command: DisplayCommand,
    account_number: str | None,
) -> DisplaySuccess:
    async with open_read_gateway() as composed:
        service = RobinhoodDisplayService(composed.client)
        if command is DisplayCommand.STATUS:
            return service.status()
        if command is DisplayCommand.ACCOUNTS:
            return await service.accounts()
        if command is DisplayCommand.PORTFOLIO:
            return await service.portfolio(_account(account_number))
        if command is DisplayCommand.POSITIONS:
            return await service.positions(_account(account_number))
        if command is DisplayCommand.ORDERS:
            filters = {
                key: value
                for key, value in {
                    "symbol": namespace.symbol,
                    "order_id": namespace.order_id,
                    "state": namespace.state,
                    "created_at_gte": namespace.created_at_gte,
                    "placed_agent": namespace.placed_agent,
                }.items()
                if value is not None
            }
            return await service.orders(
                _account(account_number),
                view=OrderView(namespace.view),
                filters=cast(dict[str, str], filters),
            )
        symbols = cast(tuple[Symbol, ...], namespace.symbols)
        if command is DisplayCommand.QUOTES:
            return await service.quotes(symbols)
        if command is DisplayCommand.PRICE_BOOK:
            return await service.price_book(symbols)
        if command is DisplayCommand.TRADABILITY:
            return await service.tradability(_account(account_number), symbols)
        if command is DisplayCommand.HISTORICALS:
            return await service.historicals(
                symbols,
                start_time=namespace.start_time,
                end_time=namespace.end_time,
                interval=None
                if namespace.interval is None
                else HistoricalInterval(namespace.interval),
                bounds=None if namespace.bounds is None else HistoricalBounds(namespace.bounds),
                adjustment_type=None
                if namespace.adjustment_type is None
                else AdjustmentType(namespace.adjustment_type),
            )
        if command is DisplayCommand.FUNDAMENTALS:
            return await service.fundamentals(
                symbols,
                bounds=None if namespace.bounds is None else FundamentalBounds(namespace.bounds),
            )
        if command is DisplayCommand.FINANCIALS:
            return await service.financials(
                symbols,
                period=ReportingPeriod(namespace.period),
                limit=namespace.limit,
            )
    raise AssertionError("unreachable display command")


def _account(value: str | None) -> str:
    if value is None:
        raise CliInputError from None
    return value


def _write_failure(
    stream: TextIO,
    command: DisplayCommand | None,
    code: str,
    *,
    retryable: bool,
) -> None:
    _write_json(
        stream,
        {
            "schema_version": "1.0",
            "command": None if command is None else command.value,
            "ready": False,
            "posture": DisplayPosture().model_dump(mode="json"),
            "limitations": {"usable_for_trading": False},
            "error": {"code": code, "retryable": retryable},
        },
    )


def _write_json(stream: TextIO, value: dict[str, Any]) -> None:
    stream.write(_render_json(value))


def _render_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


if __name__ == "__main__":
    sys.exit(main())
