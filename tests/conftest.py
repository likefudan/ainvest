"""Shared pytest configuration for ainvest."""

from collections.abc import Sequence


def pytest_report_header() -> Sequence[str]:
    """Show the safety-relevant baseline in every pytest report."""
    return (
        "ainvest test baseline: paper trading only",
        "Python compatibility baseline: 3.12+",
    )
