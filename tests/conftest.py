"""Shared pytest configuration for ainvest."""

from __future__ import annotations

from collections.abc import Sequence

import pytest


def pytest_report_header() -> Sequence[str]:
    """Show the safety-relevant baseline in every pytest report."""
    return (
        "ainvest test baseline: paper trading only",
        "Python compatibility baseline: 3.12+",
        "live_safety tests are required CI gates (skip/skipif forbidden)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Reject ordinary skip policies on live_safety tests."""
    del config  # Required hook signature; policy is item-local.
    violations: list[str] = []
    for item in items:
        if item.get_closest_marker("live_safety") is None:
            continue
        if item.get_closest_marker("skip") is not None:
            violations.append(f"{item.nodeid}: @pytest.mark.skip is forbidden")
        if item.get_closest_marker("skipif") is not None:
            violations.append(f"{item.nodeid}: @pytest.mark.skipif is forbidden")
    if violations:
        joined = "\n".join(violations)
        raise pytest.UsageError(
            "live_safety tests are required safety gates and cannot be skipped "
            f"with ordinary marker policies:\n{joined}"
        )
