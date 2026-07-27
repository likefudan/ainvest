"""Canonical StrategyContext fixtures for conformance evaluations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ainvest.schemas.examples import strategy_context_example
from ainvest.schemas.strategy import StrategyContext, parse_strategy_context


def paper_context_payload(
    *,
    strategy_name: str = "moving_average",
    strategy_version: str = "1.0.0",
    sma_20: str = "211.30",
    sma_50: str = "204.80",
) -> dict[str, Any]:
    """Fixed-clock Paper context used for determinism and Paper example checks."""
    payload = deepcopy(strategy_context_example())
    payload["as_of"] = "2026-07-24T18:30:00Z"
    payload["strategy_state"] = {
        "strategy": strategy_name,
        "strategy_version": strategy_version,
        "updated_at": "2026-07-24T18:00:00Z",
        "entries": [
            {
                "key": "fast_above_slow",
                "kind": "BOOLEAN",
                "boolean_value": False,
            }
        ],
    }
    technical = payload["research"].get("technical") or {}
    technical["sma_20"] = sma_20
    technical["sma_50"] = sma_50
    payload["research"]["technical"] = technical
    return payload


def make_paper_context(
    *,
    strategy_name: str = "moving_average",
    strategy_version: str = "1.0.0",
    **kwargs: Any,
) -> StrategyContext:
    """Build an immutable Paper StrategyContext for conformance runs."""
    return parse_strategy_context(
        paper_context_payload(
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            **kwargs,
        )
    )


__all__ = ["make_paper_context", "paper_context_payload"]
