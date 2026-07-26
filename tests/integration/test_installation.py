"""Installation smoke tests."""

from importlib.metadata import version

import pytest

import ainvest


@pytest.mark.integration
def test_distribution_metadata_matches_imported_package() -> None:
    """Editable installs expose the same version through both metadata paths."""
    assert version("ainvest") == ainvest.__version__
