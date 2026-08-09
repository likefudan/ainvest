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
@pytest.mark.parametrize("limit", ["0", "41"])
def test_financial_limit_is_closed_range(limit: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async def execute(*args: Any) -> DisplaySuccess:
        raise AssertionError("gateway must not open")

    monkeypatch.setattr(cli, "_execute", execute)
    stdin, stdout, stderr = _streams()

    assert (
        cli.main(
            ["financials", "AAPL", "--limit", limit],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
        == 2
    )
    assert stdout.getvalue() == ""


@pytest.mark.unit
@pytest.mark.parametrize(
    "argv",
    [
        ["quotes", *(["AAPL"] * 21)],
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
