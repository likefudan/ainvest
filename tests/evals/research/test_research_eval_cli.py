"""The reusable runner is fixed to synthetic, credential-free fixtures."""

import json
import subprocess
import sys
from pathlib import Path

from ainvest.agents.research_evaluation import ResearchEvalReport

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_cli_prints_machine_readable_offline_report() -> None:
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_research_evals.py")],
        cwd=REPO_ROOT,
        env={},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout
    assert len(completed.stdout.encode()) < 1_048_576
    report = ResearchEvalReport.model_validate_json(completed.stdout)
    assert report.offline_checks_passed and not report.real_ai_eligible
    assert report.rates.basis == "synthetic"


def test_cli_failed_comparison_is_sanitized(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_research_evals.py"),
            "--compare",
            str(tmp_path / "absent.json"),
        ],
        cwd=REPO_ROOT,
        env={},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {"status": "error", "code": "EVAL_FAILED"}
    assert not completed.stderr
