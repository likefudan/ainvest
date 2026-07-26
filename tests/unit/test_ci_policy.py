"""Regression tests for security-critical CI workflow policy."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEPENDABOT_CONFIG = REPO_ROOT / ".github" / "dependabot.yml"
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


@pytest.mark.unit
def test_setup_uv_preserves_cache_pruning() -> None:
    """setup-uv major upgrades must not silently disable cache pruning."""
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    setup_uv_steps = workflow.count("uses: astral-sh/setup-uv@")

    assert setup_uv_steps > 0
    assert workflow.count("prune-cache: true") == setup_uv_steps


@pytest.mark.unit
def test_dependabot_groups_github_action_updates() -> None:
    """A weekly Actions refresh should produce one reviewable PR."""
    config = yaml.safe_load(DEPENDABOT_CONFIG.read_text(encoding="utf-8"))
    actions_update = next(
        update for update in config["updates"] if update["package-ecosystem"] == "github-actions"
    )

    assert actions_update["groups"]["github-actions"]["patterns"] == ["*"]
