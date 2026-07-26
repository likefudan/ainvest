"""Strategy Protocol, definitions, plugin metadata, and evaluation results (P03-T0).

``strategy_definitions()`` hooks must return declarations only. Instantiating a
strategy and calling ``evaluate`` is the caller's responsibility and must never
run during plugin import or definition collection.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ainvest.schemas.common import SCHEMA_VERSION_V1, DomainModel, MachineCode, SchemaVersion
from ainvest.schemas.strategy import (
    StrategyContext,
    StrategyName,
    StrategyState,
    StrategyVersion,
    TradeSignal,
)
from ainvest.strategies.api import assert_strategy_api_compatible, parse_strategy_api_range

_PLUGIN_ID_PATTERN: Final[str] = r"^[a-z][a-z0-9_]{1,63}$"
_SEMVER_PATTERN: Final[str] = r"^[0-9]+\.[0-9]+\.[0-9]+$"
_COMMIT_PATTERN: Final[str] = r"^[0-9a-f]{7,64}$|^unknown$|^local$"
_OWNER_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._@/\-]{1,127}$"
_REPO_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._\-/:@]{2,255}$"


class StrategyError(ValueError):
    """Raised when strategy plugin metadata, params, or API ranges are invalid."""

    def __init__(self, message: str, *, code: str = "STRATEGY_INVALID") -> None:
        self.code = code
        super().__init__(message)


class StrategyDiagnostics(DomainModel):
    """Machine-readable evaluation diagnostics (no secrets)."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    reason_codes: tuple[MachineCode, ...] = ()
    notes: tuple[str, ...] = ()
    metrics: dict[str, str] = Field(default_factory=dict)


class StrategyResult(DomainModel):
    """Structured outcome of one strategy evaluation.

    Strategies emit ``TradeSignal`` intents only. They never submit orders.
    """

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    signals: tuple[TradeSignal, ...] = ()
    next_state: StrategyState | None = None
    diagnostics: StrategyDiagnostics = Field(default_factory=StrategyDiagnostics)


class StrategyParams(BaseModel):
    """Base class for strategy parameter models.

    Plugins should subclass this (or plain ``BaseModel`` with ``extra='forbid'``)
    so unknown YAML parameters fail closed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


@runtime_checkable
class Strategy(Protocol):
    """Stable strategy contract shared by independent plugin teams."""

    name: ClassVar[str]
    version: ClassVar[str]
    params_model: ClassVar[type[BaseModel]]

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        """Return signals, next state, and diagnostics from an immutable context."""
        ...


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    """Required metadata for every strategy plugin package."""

    plugin_id: str
    plugin_version: str
    ainvest_strategy_api: str
    source_commit: str
    owner: str
    repository: str

    def validate(self) -> None:
        """Fail closed on missing or malformed metadata fields."""
        _require_match("plugin_id", self.plugin_id, _PLUGIN_ID_PATTERN)
        _require_match("plugin_version", self.plugin_version, _SEMVER_PATTERN)
        if not self.ainvest_strategy_api or not str(self.ainvest_strategy_api).strip():
            raise StrategyError(
                "plugin metadata missing ainvest_strategy_api range",
                code="STRATEGY_METADATA_MISSING",
            )
        try:
            parse_strategy_api_range(self.ainvest_strategy_api)
            assert_strategy_api_compatible(self.ainvest_strategy_api)
        except ValueError as exc:
            raise StrategyError(str(exc), code="STRATEGY_API_INCOMPATIBLE") from exc
        _require_match("source_commit", self.source_commit, _COMMIT_PATTERN)
        _require_match("owner", self.owner, _OWNER_PATTERN)
        _require_match("repository", self.repository, _REPO_PATTERN)


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    """Immutable validated declaration of one strategy implementation."""

    name: str
    version: str
    params_model: type[BaseModel]
    strategy_type: type[Any]
    metadata: PluginMetadata

    @classmethod
    def from_type(
        cls,
        strategy_type: type[Any],
        *,
        metadata: PluginMetadata,
    ) -> StrategyDefinition:
        """Build a definition from a strategy class without executing it."""
        metadata.validate()
        name = getattr(strategy_type, "name", None)
        version = getattr(strategy_type, "version", None)
        params_model = getattr(strategy_type, "params_model", None)
        if not isinstance(name, str) or not name.strip():
            raise StrategyError(
                f"strategy {strategy_type!r} missing name",
                code="STRATEGY_METADATA_MISSING",
            )
        if not isinstance(version, str) or not version.strip():
            raise StrategyError(
                f"strategy {name!r} missing version",
                code="STRATEGY_METADATA_MISSING",
            )
        _require_strategy_name(name)
        _require_match("strategy_version", version, _SEMVER_PATTERN)
        if not isinstance(params_model, type) or not issubclass(params_model, BaseModel):
            raise StrategyError(
                f"strategy {name!r} params_model must be a Pydantic BaseModel subclass",
                code="STRATEGY_PARAMS_INVALID",
            )
        extra = params_model.model_config.get("extra")
        if extra != "forbid":
            raise StrategyError(
                f"strategy {name!r} params_model must set extra='forbid'",
                code="STRATEGY_PARAMS_INVALID",
            )
        if not callable(getattr(strategy_type, "evaluate", None)):
            raise StrategyError(
                f"strategy {name!r} must define evaluate(context)",
                code="STRATEGY_METADATA_MISSING",
            )
        return cls(
            name=name,
            version=version,
            params_model=params_model,
            strategy_type=strategy_type,
            metadata=metadata,
        )

    def validate_params(self, raw: Mapping[str, Any] | BaseModel) -> BaseModel:
        """Validate instance parameters against this definition's params model."""
        try:
            if isinstance(raw, BaseModel):
                if type(raw) is self.params_model:
                    return raw
                return self.params_model.model_validate(raw.model_dump(mode="python"))
            return self.params_model.model_validate(dict(raw))
        except ValidationError as exc:
            raise StrategyError(
                f"invalid parameters for strategy {self.name!r}: {exc.error_count()} error(s)",
                code="STRATEGY_PARAMS_INVALID",
            ) from exc

    def create(self, params: Mapping[str, Any] | BaseModel | None = None) -> Any:
        """Instantiate the strategy with validated parameters (does not evaluate)."""
        validated = self.validate_params({} if params is None else params)
        return self.strategy_type(validated)


def _require_match(field: str, value: str, pattern: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise StrategyError(
            f"plugin metadata missing {field}",
            code="STRATEGY_METADATA_MISSING",
        )
    if re.fullmatch(pattern, value) is None:
        raise StrategyError(
            f"invalid plugin metadata {field}: {value!r}",
            code="STRATEGY_METADATA_INVALID",
        )


def _require_strategy_name(name: str) -> None:
    if re.fullmatch(r"^[a-z][a-z0-9_]{1,63}$", name) is None:
        raise StrategyError(
            f"invalid strategy name: {name!r}",
            code="STRATEGY_METADATA_INVALID",
        )


# Re-exported for plugin authors importing from definitions.
_ = (StrategyName, StrategyVersion)


__all__ = [
    "PluginMetadata",
    "Strategy",
    "StrategyDefinition",
    "StrategyDiagnostics",
    "StrategyError",
    "StrategyParams",
    "StrategyResult",
]
