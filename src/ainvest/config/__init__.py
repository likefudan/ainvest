"""Centralized configuration loading with fail-closed safe defaults.

All runtime configuration for ainvest must flow through this module. Other
modules must not read arbitrary environment variables or parse YAML themselves.

Configuration precedence (later sources override earlier ones):

1. Built-in fail-closed defaults defined on the Settings models
2. Optional YAML files (``risk`` / ``strategies`` via :func:`load_yaml_mapping`)
3. Optional file-secret directory (Pydantic Settings)
4. Optional ``.env`` file (Pydantic Settings)
5. Environment variables
6. Explicit keyword overrides passed to :func:`load_settings`

Production (``AINVEST_ENV=production``) and all other environments reject unknown
fields on Settings/YAML document models and reject unsafe mode combinations.
Live trading additionally requires a complete WebAuthn configuration.

Decision references: ``DEC-001``, ``DEC-002``, ``DEC-004``, ``DEC-005``,
``DEC-006``.
"""

from __future__ import annotations

from ainvest.config.documents import RiskLimitsDocument, StrategiesDocument
from ainvest.config.errors import (
    CONFIG_PRECEDENCE,
    AinvestEnv,
    ApprovalMethod,
    ApprovalScope,
    ConfigError,
    TelegramTransport,
    TradingMode,
)
from ainvest.config.settings import (
    AISettings,
    RobinhoodAccountSecretInvalid,
    Settings,
    TelegramBotSettings,
    TelegramRecipient,
    WebAuthnSettings,
    load_robinhood_read_account_number,
    load_settings,
)
from ainvest.config.yaml import load_yaml_mapping

__all__ = [
    "CONFIG_PRECEDENCE",
    "AISettings",
    "AinvestEnv",
    "ApprovalMethod",
    "ApprovalScope",
    "ConfigError",
    "RiskLimitsDocument",
    "RobinhoodAccountSecretInvalid",
    "Settings",
    "StrategiesDocument",
    "TelegramBotSettings",
    "TelegramRecipient",
    "TelegramTransport",
    "TradingMode",
    "WebAuthnSettings",
    "load_robinhood_read_account_number",
    "load_settings",
    "load_yaml_mapping",
]
