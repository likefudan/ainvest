"""Integration: CLI discovers the reference MA plugin and emits reports."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_cli_reference_ma_passes(tmp_path: Path) -> None:
    json_out = tmp_path / "report.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ainvest.strategy_conformance",
            "--strategy",
            "moving_average",
            "--plugin-id",
            "moving_average",
            "--plugin-version",
            "1.0.0",
            "--json-out",
            str(json_out),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "PASSED" in proc.stdout
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["strategy_name"] == "moving_average"
    assert payload["plugin_id"] == "moving_average"
    assert all(check["status"] == "PASSED" for check in payload["checks"])
