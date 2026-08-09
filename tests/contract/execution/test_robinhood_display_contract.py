"""Contract tests for the P06-T2 Part 1 display boundary."""

from __future__ import annotations

import ast
import io
import json
from pathlib import Path
from typing import Final

import pytest

from ainvest.execution.robinhood import cli
from ainvest.execution.robinhood.display import (
    DisplayCommand,
    DisplayLimitations,
    DisplayStatusData,
    DisplaySuccess,
)
from ainvest.execution.robinhood.pins import (
    APPROVED_NON_TRADING_MUTATIONS,
    DENIED_TRADING_CAPABILITIES,
)

ROOT: Final = Path(__file__).resolve().parents[3]
DISPLAY_MODULE: Final = ROOT / "src" / "ainvest" / "execution" / "robinhood" / "display.py"
CLI_MODULE: Final = ROOT / "src" / "ainvest" / "execution" / "robinhood" / "cli.py"


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


@pytest.mark.contract
def test_success_and_failure_wire_envelopes_have_exact_top_level_keys() -> None:
    success = _status().model_dump(mode="json")
    assert list(success) == [
        "schema_version",
        "command",
        "ready",
        "posture",
        "limitations",
        "data",
    ]

    failure_stream = io.StringIO()
    cli._write_failure(
        failure_stream,
        DisplayCommand.STATUS,
        "not_ready",
        retryable=False,
    )
    failure = json.loads(failure_stream.getvalue())
    assert list(failure) == [
        "schema_version",
        "command",
        "ready",
        "posture",
        "limitations",
        "error",
    ]
    assert failure["limitations"] == {"usable_for_trading": False}
    assert set(failure["error"]) == {"code", "retryable"}


@pytest.mark.contract
def test_display_schema_is_closed_and_pins_safety_literals() -> None:
    schema = DisplaySuccess.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == "1.0"
    assert schema["properties"]["ready"]["const"] is True

    posture = schema["$defs"]["DisplayPosture"]
    limitations = schema["$defs"]["DisplayLimitations"]
    assert posture["additionalProperties"] is False
    assert limitations["additionalProperties"] is False
    assert posture["properties"]["read_only"]["const"] is True
    assert posture["properties"]["mode"]["const"] == "display_only"
    assert posture["properties"]["execution"]["const"] == "disabled"
    assert limitations["properties"]["usable_for_trading"]["const"] is False


@pytest.mark.contract
def test_display_modules_import_no_privileged_or_fallback_boundary() -> None:
    forbidden_fragments = {
        "approval",
        "paper",
        "risk",
        "strategies",
        "telegram",
        "yfinance",
        "alpaca",
        "mcp",
        "rh_mcp",
    }
    for path in (DISPLAY_MODULE, CLI_MODULE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert all(
            fragment not in module for module in imported for fragment in forbidden_fragments
        )


@pytest.mark.contract
def test_service_calls_only_the_ten_named_client_reads() -> None:
    tree = ast.parse(DISPLAY_MODULE.read_text(encoding="utf-8"))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "_client"
    }
    assert calls == {
        "read_accounts",
        "read_portfolio",
        "read_equity_positions",
        "read_equity_orders",
        "read_equity_quotes",
        "read_equity_price_book",
        "read_equity_tradability",
        "read_equity_historicals",
        "read_equity_fundamentals",
        "read_financials",
    }
    source = DISPLAY_MODULE.read_text(encoding="utf-8") + CLI_MODULE.read_text(encoding="utf-8")
    assert ".invoke(" not in source
    for capability in APPROVED_NON_TRADING_MUTATIONS | DENIED_TRADING_CAPABILITIES:
        assert capability not in source


@pytest.mark.contract
def test_command_surface_and_console_script_are_exact() -> None:
    assert {command.value for command in DisplayCommand} == {
        "status",
        "accounts",
        "portfolio",
        "positions",
        "orders",
        "quotes",
        "price-book",
        "tradability",
        "historicals",
        "fundamentals",
        "financials",
    }
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'ainvest-robinhood-read = "ainvest.execution.robinhood.cli:main"' in metadata


@pytest.mark.contract
def test_json_is_the_only_provider_value_rendering_boundary() -> None:
    source = CLI_MODULE.read_text(encoding="utf-8")
    assert "json.dumps(" in source
    assert "rich" not in source
    assert "print(" not in source

    rendered = cli._render_json(
        {"value": 'bounded "text" <tag>', "marker": "[unavailable: untrusted text omitted]"}
    )
    assert json.loads(rendered) == {
        "value": 'bounded "text" <tag>',
        "marker": "[unavailable: untrusted text omitted]",
    }
