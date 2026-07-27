"""Structural YAML document models for risk and strategy configuration."""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ainvest.config.yaml import _reject_executable_yaml


class RiskLimitsDocument(BaseModel):
    """Structural container for risk YAML.

    Concrete numeric owner limits are ``DEC-012`` and remain unresolved until
    accepted. This model validates document shape only and never supplies
    implicit tradable defaults (DEC-002).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1"
    limits: dict[str, Any] = Field(default_factory=dict)
    instrument_allowlist: list[dict[str, Any]] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def _reject_executable_limit_values(self) -> Self:
        _reject_executable_yaml({"limits": self.limits, "allowlist": self.instrument_allowlist})
        return self


class StrategiesDocument(BaseModel):
    """Structural container for strategy instance YAML (DEC-011)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1"
    strategies: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reject_executable_strategy_values(self) -> Self:
        _reject_executable_yaml({"strategies": self.strategies})
        return self
