"""Unit tests for the display-only CLI grammar and wire errors."""

from __future__ import annotations

import getpass as getpass_module
import io
import json
from argparse import Namespace
from collections.abc import Sequence
from typing import Any

import pytest

from ainvest.execution.robinhood import cli
from ainvest.execution.robinhood.display import (
    DisplayCommand,
    DisplayLimitations,
    DisplayStatusData,
    DisplaySuccess,
)
from ainvest.execution.robinhood.errors import GatewayReadError, GatewayReadErrorCode


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def _streams(stdin: io.StringIO | None = None) -> tuple[io.StringIO, io.StringIO, io.StringIO]:
    return stdin or io.StringIO(), io.StringIO(), io.StringIO()


def _status() -> DisplaySuccess:
    return DisplaySuccess(
        command=DisplayCommand.STATUS,
        limitations=DisplayLimitations(
            identity="not_applicable",
            account_binding="not_applicable",
            session_evidence="not_applicable",
        ),
        data=DisplayStatusData(),
    )


@pytest.mark.unit
def test_ready_status_writes_one_exact_json_document(monkeypatch: pytest.MonkeyPatch) -> None:
    async def execute(
        namespace: Namespace, command: DisplayCommand, account_number: str | None
    ) -> DisplaySuccess:
        assert namespace.command == "status"
        assert command is DisplayCommand.STATUS
        assert account_number is None
        return _status()

    monkeypatch.setattr(cli, "_execute", execute)
    stdin, stdout, stderr = _streams()

    assert cli.main(["status"], stdin=stdin, stdout=stdout, stderr=stderr) == 0
    assert stderr.getvalue() == ""
    assert stdout.getvalue().endswith("\n") and stdout.getvalue().count("\n") == 1
    assert json.loads(stdout.getvalue()) == _status().model_dump(mode="json")


@pytest.mark.unit
@pytest.mark.parametrize("argv", [[], ["unknown"], ["quotes"], ["financials", "aapl"]])
def test_usage_errors_are_sanitized_json(
    argv: Sequence[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    async def execute(*args: Any) -> DisplaySuccess:
        nonlocal called
        called = True
        return _status()

    monkeypatch.setattr(cli, "_execute", execute)
    stdin, stdout, stderr = _streams()

    assert cli.main(argv, stdin=stdin, stdout=stdout, stderr=stderr) == 2
    assert stdout.getvalue() == ""
    error = json.loads(stderr.getvalue())
    assert error["error"] == {"code": "invalid_cli_input", "retryable": False}
    expected_command = None if not argv or argv[0] == "unknown" else argv[0]
    assert error["command"] == expected_command
    assert called is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("view", "state", "valid"),
    [
        (view, state, (view == "open") == (state in cli._OPEN_STATES))
        for view in ("open", "closed")
        for state in sorted(cli._ALL_STATES)
    ],
)
def test_every_orders_view_state_pair_is_checked_before_gateway(
    view: str,
    state: str,
    valid: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def execute(*args: Any) -> DisplaySuccess:
        nonlocal called
        called = True
        raise GatewayReadError(GatewayReadErrorCode.NOT_READY)

    monkeypatch.setattr(cli, "_execute", execute)
    stdin, stdout, stderr = _streams(io.StringIO("opaque-account\n"))
    exit_code = cli.main(
        ["orders", "--account-number-stdin", "--view", view, "--state", state],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == (1 if valid else 2)
    assert called is valid
    assert stdout.getvalue() == ""


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        "",
        "\n",
        "contains space\n",
        "contains\t tab\n",
        "line-one\nline-two\n",
        "carriage\rreturn\n",
        "x" * 129,
        "x" * 128 + "\nextra",
    ],
)
def test_non_tty_account_input_rejects_adversarial_forms_without_echo(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    async def execute(*args: Any) -> DisplaySuccess:
        nonlocal called
        called = True
        return _status()

    monkeypatch.setattr(cli, "_execute", execute)
    stdin, stdout, stderr = _streams(io.StringIO(value))

    assert (
        cli.main(
            ["portfolio", "--account-number-stdin"],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
        == 2
    )
    assert called is False
    assert stdout.getvalue() == ""
    rejected_content = value.strip()
    if rejected_content:
        assert rejected_content not in stderr.getvalue()


@pytest.mark.unit
def test_account_input_requires_the_exact_tty_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    async def execute(*args: Any) -> DisplaySuccess:
        raise AssertionError("gateway must not open")

    monkeypatch.setattr(cli, "_execute", execute)
    for argv, stream in (
        (["portfolio"], io.StringIO("opaque-account\n")),
        (["portfolio", "--account-number-stdin"], _TTY("opaque-account\n")),
    ):
        _, stdout, stderr = _streams()
        assert cli.main(argv, stdin=stream, stdout=stdout, stderr=stderr) == 2
        assert stdout.getvalue() == ""


@pytest.mark.unit
def test_tty_getpass_value_is_never_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "opaque-account"
    captured: list[str | None] = []

    monkeypatch.setattr(getpass_module, "getpass", lambda prompt: secret)

    async def execute(
        namespace: Namespace, command: DisplayCommand, account_number: str | None
    ) -> DisplaySuccess:
        del namespace, command
        captured.append(account_number)
        raise GatewayReadError(GatewayReadErrorCode.TIMEOUT)

    monkeypatch.setattr(cli, "_execute", execute)
    stdin, stdout, stderr = _streams(_TTY())

    assert cli.main(["positions"], stdin=stdin, stdout=stdout, stderr=stderr) == 1
    assert captured == [secret]
    assert stdout.getvalue() == ""
    assert secret not in stderr.getvalue()
    assert json.loads(stderr.getvalue())["error"] == {"code": "timeout", "retryable": True}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("limit", "valid"), [("0", False), ("1", True), ("40", True), ("41", False)]
)
def test_financial_limit_is_closed_range(
    limit: str, valid: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    async def execute(*args: Any) -> DisplaySuccess:
        nonlocal called
        called = True
        raise GatewayReadError(GatewayReadErrorCode.NOT_READY)

    monkeypatch.setattr(cli, "_execute", execute)
    stdin, stdout, stderr = _streams()

    exit_code = cli.main(
        ["financials", "AAPL", "--limit", limit],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )
    assert exit_code == (1 if valid else 2)
    assert called is valid
    assert stdout.getvalue() == ""


@pytest.mark.unit
@pytest.mark.parametrize(
    "argv",
    [
        ["quotes", *(f"A{index}" for index in range(21))],
        ["price-book", "AAPL", "MSFT", "NVDA", "TSLA", "AMD"],
        ["historicals", "AAPL", "--start-time", "2026-13-01T00:00:00Z"],
        ["historicals", "AAPL", "--start-time", "2026-08-01"],
        [
            "historicals",
            "AAPL",
            "--start-time",
            "2026-08-02T00:00:00Z",
            "--end-time",
            "2026-08-01T00:00:00Z",
        ],
        ["orders", "--account-number-stdin", "--view", "open", "--placed-agent", "bad value"],
        ["orders", "--account-number-stdin", "--view", "open", "--created-at-gte", "20260809"],
        [
            "orders",
            "--account-number-stdin",
            "--view",
            "open",
            "--created-at-gte",
            "2026-W32-7",
        ],
    ],
)
def test_argument_boundaries_fail_before_gateway(
    argv: Sequence[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def execute(*args: Any) -> DisplaySuccess:
        raise AssertionError("gateway must not open")

    monkeypatch.setattr(cli, "_execute", execute)
    stdin, stdout, stderr = _streams(io.StringIO("opaque-account\n"))

    assert cli.main(argv, stdin=stdin, stdout=stdout, stderr=stderr) == 2
    assert stdout.getvalue() == ""


@pytest.mark.unit
def test_unexpected_error_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    async def execute(*args: Any) -> DisplaySuccess:
        raise RuntimeError("provider prose and payload")

    monkeypatch.setattr(cli, "_execute", execute)
    stdin, stdout, stderr = _streams()

    assert cli.main(["status"], stdin=stdin, stdout=stdout, stderr=stderr) == 1
    assert stdout.getvalue() == ""
    assert "provider prose" not in stderr.getvalue()
    assert json.loads(stderr.getvalue())["error"] == {
        "code": "internal_error",
        "retryable": False,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("command", "limit", "needs_account"),
    [
        ("quotes", 20, False),
        ("price-book", 4, False),
        ("tradability", 10, True),
        ("historicals", 10, False),
        ("fundamentals", 10, False),
        ("financials", 20, False),
    ],
)
def test_every_symbol_command_enforces_maximum_and_duplicate_rejection(
    command: str,
    limit: int,
    needs_account: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def execute(*args: Any) -> DisplaySuccess:
        nonlocal calls
        calls += 1
        raise GatewayReadError(GatewayReadErrorCode.NOT_READY)

    def argv(symbols: list[str]) -> list[str]:
        values = [command]
        if needs_account:
            values.append("--account-number-stdin")
        values.extend(symbols)
        if command == "historicals":
            values.extend(["--start-time", "2026-08-01T00:00:00Z"])
        return values

    monkeypatch.setattr(cli, "_execute", execute)
    symbols = [f"A{index}" for index in range(limit + 1)]
    for requested, expected_exit in (
        (symbols[:limit], 1),
        (symbols, 2),
        (["AAPL", "AAPL"], 2),
    ):
        stdin, stdout, stderr = _streams(io.StringIO("opaque-account\n" if needs_account else ""))
        assert cli.main(argv(requested), stdin=stdin, stdout=stdout, stderr=stderr) == expected_exit
        assert stdout.getvalue() == ""
    assert calls == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "created_at",
    ["2026-08-09", "2026-08-09T00:00:00Z", "2026-08-08T17:00:00-07:00"],
)
def test_orders_accept_only_canonical_date_or_rfc3339_filter(
    created_at: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def execute(*args: Any) -> DisplaySuccess:
        raise GatewayReadError(GatewayReadErrorCode.NOT_READY)

    monkeypatch.setattr(cli, "_execute", execute)
    stdin, stdout, stderr = _streams(io.StringIO("opaque-account\n"))
    assert (
        cli.main(
            [
                "orders",
                "--account-number-stdin",
                "--view",
                "open",
                "--created-at-gte",
                created_at,
            ],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
        == 1
    )
    assert stdout.getvalue() == ""


@pytest.mark.unit
def test_render_failure_is_sanitized_and_leaves_stdout_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def execute(*args: Any) -> DisplaySuccess:
        return _status()

    def fail_render(self: DisplaySuccess, *, mode: str) -> dict[str, Any]:
        del self, mode
        raise ValueError("provider-like rendering detail")

    monkeypatch.setattr(cli, "_execute", execute)
    monkeypatch.setattr(DisplaySuccess, "model_dump", fail_render)
    stdin, stdout, stderr = _streams()
    assert cli.main(["status"], stdin=stdin, stdout=stdout, stderr=stderr) == 1
    assert stdout.getvalue() == ""
    assert "provider-like" not in stderr.getvalue()
    assert json.loads(stderr.getvalue())["error"] == {
        "code": "internal_error",
        "retryable": False,
    }
