"""Unit tests for Strategy API version range helpers (P02-T5)."""

from __future__ import annotations

import pytest

from ainvest.strategies.api import (
    STRATEGY_API_VERSION,
    assert_strategy_api_compatible,
    parse_strategy_api_range,
    strategy_api_range_contains,
)


@pytest.mark.unit
def test_host_strategy_api_version_is_semver() -> None:
    assert STRATEGY_API_VERSION == "1.0.0"
    assert strategy_api_range_contains(">=1.0.0,<2.0.0")
    assert not strategy_api_range_contains(">=2.0.0,<3.0.0")


@pytest.mark.unit
def test_exact_and_inequality_clauses() -> None:
    assert parse_strategy_api_range("1.0.0").contains("1.0.0")
    assert not parse_strategy_api_range("==1.0.0").contains("1.0.1")
    assert strategy_api_range_contains(">1.0.0,<1.1.0", "1.0.1")
    assert not strategy_api_range_contains("<=1.0.0", "1.0.1")


@pytest.mark.unit
def test_incompatible_range_fails_closed() -> None:
    with pytest.raises(ValueError, match="incompatible"):
        assert_strategy_api_compatible(">=2.0.0,<3.0.0")
    with pytest.raises(ValueError, match="invalid"):
        parse_strategy_api_range("latest")
