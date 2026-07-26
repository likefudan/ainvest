"""Strategy API version surface for plugin compatibility (P02-T5 / design §5.3).

Full pluggy hooks arrive in P03-T0. This module defines the version constant and
range helpers so plugins can already declare ``ainvest_strategy_api`` support.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

STRATEGY_API_VERSION: Final[str] = "1.0.0"

_SEMVER_CORE = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
_VERSION_RE = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")
_CLAUSE_RE = re.compile(rf"^(?P<op>>=|>|<=|<|==|=)?(?P<version>{_SEMVER_CORE})$")


@dataclass(frozen=True, slots=True)
class _Version:
    major: int
    minor: int
    patch: int

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)


def _parse_version(text: str) -> _Version:
    match = _VERSION_RE.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"invalid Strategy API version: {text!r}")
    return _Version(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
    )


@dataclass(frozen=True, slots=True)
class StrategyApiRange:
    """Parsed plugin declaration of supported ``ainvest_strategy_api`` versions."""

    spec: str
    clauses: tuple[tuple[str, _Version], ...]

    def contains(self, version: str) -> bool:
        target = _parse_version(version)
        for operator, bound in self.clauses:
            left = target.as_tuple()
            right = bound.as_tuple()
            if operator in {"", "=", "=="} and left != right:
                return False
            if operator == ">=" and left < right:
                return False
            if operator == ">" and left <= right:
                return False
            if operator == "<=" and left > right:
                return False
            if operator == "<" and left >= right:
                return False
        return True


def parse_strategy_api_range(spec: str) -> StrategyApiRange:
    """Parse a comma-separated Strategy API range such as ``>=1.0.0,<2.0.0``."""
    text = spec.strip()
    if not text:
        raise ValueError("Strategy API range must not be empty")
    clauses: list[tuple[str, _Version]] = []
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError(f"empty clause in Strategy API range: {spec!r}")
        match = _CLAUSE_RE.fullmatch(part)
        if match is None:
            raise ValueError(f"invalid Strategy API range clause: {part!r}")
        operator = match.group("op") or "=="
        if operator == "=":
            operator = "=="
        clauses.append((operator, _parse_version(match.group("version"))))
    return StrategyApiRange(spec=text, clauses=tuple(clauses))


def strategy_api_range_contains(spec: str, version: str = STRATEGY_API_VERSION) -> bool:
    """Return whether ``version`` satisfies the plugin-declared range ``spec``."""
    return parse_strategy_api_range(spec).contains(version)


def assert_strategy_api_compatible(spec: str, version: str = STRATEGY_API_VERSION) -> None:
    """Fail closed when a plugin range does not include the host API version."""
    if not strategy_api_range_contains(spec, version):
        raise ValueError(
            f"plugin Strategy API range {spec!r} is incompatible with host {version!r}"
        )


__all__ = [
    "STRATEGY_API_VERSION",
    "StrategyApiRange",
    "assert_strategy_api_compatible",
    "parse_strategy_api_range",
    "strategy_api_range_contains",
]
