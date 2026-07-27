"""Unit tests for the strategy conformance CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ainvest.strategy_conformance.cli import main, resolve_definition


def test_resolve_definition_loads_reference_ma() -> None:
    definition = resolve_definition(
        "moving_average",
        plugin_id="moving_average",
        plugin_version="1.0.0",
    )
    assert definition.name == "moving_average"
    assert definition.metadata.plugin_id == "moving_average"


def test_main_writes_json_and_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    json_out = tmp_path / "out.json"
    code = main(
        [
            "--strategy",
            "moving_average",
            "--json-out",
            str(json_out),
        ]
    )
    assert code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    captured = capsys.readouterr()
    assert "PASSED" in captured.out


def test_main_unknown_strategy_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--strategy", "does_not_exist_strategy"])
    assert code == 2
    assert "failed to load strategy" in capsys.readouterr().err
