"""Unit smoke tests for the package."""

import pytest

import ainvest


@pytest.mark.unit
def test_package_import_exposes_a_version() -> None:
    """The installed package exposes non-empty version metadata."""
    assert ainvest.__version__
