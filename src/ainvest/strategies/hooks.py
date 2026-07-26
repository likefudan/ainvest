"""pluggy hook specifications and markers for strategy plugins (P03-T0 / P03-T1)."""

from __future__ import annotations

import pluggy

from ainvest.strategies.definitions import StrategyDefinition

HOOK_NAMESPACE: str = "ainvest"
ENTRY_POINT_GROUP: str = "ainvest.strategies"

hookspec = pluggy.HookspecMarker(HOOK_NAMESPACE)
hookimpl = pluggy.HookimplMarker(HOOK_NAMESPACE)


class StrategyHookSpec:
    """Stable hook surface for strategy plugin packages."""

    @hookspec
    def strategy_definitions(self) -> list[StrategyDefinition]:
        """Return strategy declarations only; never execute strategy logic."""
        raise NotImplementedError


__all__ = [
    "ENTRY_POINT_GROUP",
    "HOOK_NAMESPACE",
    "StrategyHookSpec",
    "hookimpl",
    "hookspec",
]
