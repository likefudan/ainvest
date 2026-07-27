"""Configuration errors and shared enumeration types."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

CONFIG_PRECEDENCE: Final[tuple[str, ...]] = (
    "built-in fail-closed defaults",
    "optional YAML files (safe loader)",
    "optional file-secret directory",
    "optional .env file",
    "environment variables",
    "explicit load_settings overrides",
)


class ConfigError(ValueError):
    """Raised when configuration is missing, invalid, or unsafe."""

    def __init__(self, message: str, *, code: str = "CONFIG_INVALID") -> None:
        self.code = code
        super().__init__(message)


class TradingMode(StrEnum):
    """Supported runtime trading modes (design.md §12)."""

    RESEARCH = "research"
    PAPER = "paper"
    LIVE = "live"


class AinvestEnv(StrEnum):
    """Deployment environment label."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class ApprovalMethod(StrEnum):
    """Human approval method bound to an order hash."""

    TELEGRAM = "telegram"
    WEBAUTHN = "webauthn"


class ApprovalScope(StrEnum):
    """Broker/approval scope boundary (DEC-005, DEC-006)."""

    PAPER = "paper"
    LIVE = "live"


class TelegramTransport(StrEnum):
    """Telegram update transport. First release is long polling only."""

    LONG_POLLING = "long_polling"
