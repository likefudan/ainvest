"""Regression tests for security-critical CI workflow policy."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
ACTION_REF = re.compile(r"^\s*uses:\s+\S+@([0-9a-f]{40})(?:\s+#.*)?$", re.MULTILINE)


@pytest.mark.unit
def test_ci_actions_are_pinned_to_commit_shas() -> None:
    """Mutable action tags cannot silently change merge-gate behavior."""
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    uses_lines = [line for line in workflow.splitlines() if line.lstrip().startswith("uses:")]

    assert uses_lines
    assert len(ACTION_REF.findall(workflow)) == len(uses_lines)


@pytest.mark.unit
def test_ci_secret_scan_has_pull_request_read_permission() -> None:
    """Gitleaks can read PR metadata without write privileges."""
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "contents: read" in workflow
    assert "pull-requests: read" in workflow


@pytest.mark.unit
def test_ci_audits_all_dependency_profiles_through_wrapper() -> None:
    """CI uses the canonical all-profile dependency audit."""
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    dev_script = (REPO_ROOT / "scripts" / "dev").read_text(encoding="utf-8")

    assert "./scripts/dev audit" in workflow
    assert "--all-extras" in dev_script
    assert "--all-groups" in dev_script
    assert "python-version-file" not in workflow
