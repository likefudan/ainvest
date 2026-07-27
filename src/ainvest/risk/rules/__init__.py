"""Risk rule protocol, registry, and rule implementations (P03-T8).

Package layout: registry lives here; concrete rules are submodules. The C4a
owner of ``engine.py`` also owns this registry.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Final, Protocol, runtime_checkable

from ainvest.risk.models import RiskContext, RuleResult

RuleCodeName = str


@runtime_checkable
class RiskRule(Protocol):
    """Pure rule: immutable context in, explainable result out."""

    @property
    def code(self) -> RuleCodeName:
        """Stable machine-readable rule identifier."""
        ...

    def evaluate(self, context: RiskContext) -> RuleResult:
        """Return one rule result; must not mutate ``context``."""
        ...


RuleFactory = Callable[[], RiskRule]


_REGISTRY: dict[str, RuleFactory] = {}


def register_rule(code: str, factory: RuleFactory) -> None:
    """Register a rule factory under a stable code (fail on duplicates)."""
    if code in _REGISTRY:
        raise ValueError(f"duplicate risk rule registration: {code}")
    _REGISTRY[code] = factory


def unregister_rule(code: str) -> None:
    """Remove a rule from the registry (tests only)."""
    _REGISTRY.pop(code, None)


def clear_registry() -> None:
    """Drop all registrations (tests only)."""
    _REGISTRY.clear()


def registered_rule_codes() -> frozenset[str]:
    return frozenset(_REGISTRY)


def get_rule(code: str) -> RiskRule:
    factory = _REGISTRY.get(code)
    if factory is None:
        raise KeyError(code)
    return factory()


def resolve_rules(codes: Sequence[str] | None = None) -> tuple[RiskRule, ...]:
    """Instantiate rules. Unknown codes raise ``KeyError`` (engine fails closed)."""
    ordered = tuple(sorted(_REGISTRY)) if codes is None else tuple(codes)
    return tuple(get_rule(code) for code in ordered)


def registry_snapshot() -> Mapping[str, RuleFactory]:
    return dict(_REGISTRY)


DEFAULT_C4A_RULE_CODES: Final[tuple[str, ...]] = (
    "ELIGIBILITY_ASSET_CLASS",
    "ELIGIBILITY_ALLOWLIST",
    "ELIGIBILITY_IDENTITY",
    "ELIGIBILITY_SIDE_AND_PRODUCT",
    "ELIGIBILITY_SESSION",
    "MARKET_QUALITY_QUOTE",
    "MARKET_QUALITY_SPREAD",
    "MARKET_QUALITY_VOLATILITY",
    "MARKET_QUALITY_LIMIT_DEVIATION",
)


__all__ = [
    "DEFAULT_C4A_RULE_CODES",
    "RiskRule",
    "RuleFactory",
    "clear_registry",
    "get_rule",
    "register_rule",
    "registered_rule_codes",
    "registry_snapshot",
    "resolve_rules",
    "unregister_rule",
]
