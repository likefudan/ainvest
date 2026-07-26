"""Contract smoke tests for the public package surface."""

import pytest

import ainvest


@pytest.mark.contract
def test_package_exports_only_version_at_baseline() -> None:
    """The baseline public package surface remains intentionally minimal."""
    assert ainvest.__all__ == ["__version__"]
