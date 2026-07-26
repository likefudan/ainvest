"""Policy checks for the required live_safety pytest marker."""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_live_safety_marker_is_registered(pytestconfig: pytest.Config) -> None:
    """The live_safety marker must be registered under --strict-markers."""
    markers = pytestconfig.getini("markers")
    assert any(marker.split(":", maxsplit=1)[0].strip() == "live_safety" for marker in markers), (
        "live_safety must be declared in pyproject.toml [tool.pytest.ini_options].markers"
    )


@pytest.mark.unit
@pytest.mark.live_safety
def test_live_safety_marker_is_collectable() -> None:
    """Placeholder ensuring live_safety tests are collected and run in CI."""
    assert True
