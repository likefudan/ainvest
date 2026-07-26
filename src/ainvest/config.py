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

import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

# Signed 64-bit bounds for Telegram numeric identities (DEC-005).
_INT64_MIN: Final[int] = -(2**63)
_INT64_MAX: Final[int] = 2**63 - 1

_EXECUTABLE_YAML_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:\beval\s*\(|\blambda\b|\b__import__\b|\bexec\s*\()",
    re.IGNORECASE,
)

_RP_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?=.{1,253}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
    r"(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*$"
)

_SECRET_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "openai_api_key",
        "bot_token",
        "webhook_secret",
        "database_password",
        "robinhood_oauth_token",
        "webauthn_server_secret",
    }
)

CONFIG_PRECEDENCE: Final[tuple[str, ...]] = (
    "built-in fail-closed defaults",
    "optional YAML files (safe loader)",
    "optional file-secret directory",
    "optional .env file",
    "environment variables",
    "explicit load_settings overrides",
)

_YAML_SETTINGS_DATA: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "ainvest_yaml_settings_data",
    default=None,
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


def _redact_secrets_in_text(text: str) -> str:
    """Replace likely secret assignments in error text with a placeholder."""
    redacted = text
    for name in _SECRET_FIELD_NAMES:
        redacted = re.sub(
            rf"({name}\s*[=:]\s*)([^\s,}}\]]+)",
            r"\1***REDACTED***",
            redacted,
            flags=re.IGNORECASE,
        )
    redacted = re.sub(
        r"SecretStr\('.*?'\)",
        "SecretStr('***REDACTED***')",
        redacted,
    )
    redacted = re.sub(
        r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b",
        "***REDACTED***",
        redacted,
    )
    return redacted


def _validation_error_message(exc: ValidationError) -> str:
    """Format a ValidationError without echoing secret values."""
    try:
        body = exc.json(include_url=False, include_context=False, include_input=False)
    except TypeError:  # pragma: no cover - older pydantic fallback
        body = str(exc)
    return _redact_secrets_in_text(body)


def _is_int64(value: int) -> bool:
    return _INT64_MIN <= value <= _INT64_MAX


def _reject_executable_yaml(node: object, *, path: str = "$") -> None:
    """Reject YAML content that looks like executable configuration."""
    if isinstance(node, str):
        if _EXECUTABLE_YAML_PATTERN.search(node):
            raise ConfigError(
                f"Executable expression rejected in YAML at {path}",
                code="CONFIG_YAML_EXECUTABLE",
            )
        return
    if isinstance(node, Mapping):
        for key, value in node.items():
            key_path = f"{path}.{key}"
            if isinstance(key, str):
                _reject_executable_yaml(key, path=f"{path}@key")
            _reject_executable_yaml(value, path=key_path)
        return
    if isinstance(node, list):
        for index, item in enumerate(node):
            _reject_executable_yaml(item, path=f"{path}[{index}]")
        return
    if isinstance(node, (int, float, bool)) or node is None:
        return
    raise ConfigError(
        f"Unsupported YAML node type at {path}: {type(node).__name__}",
        code="CONFIG_YAML_UNSAFE_TYPE",
    )


def load_yaml_mapping(path: Path | str) -> dict[str, Any]:
    """Load a YAML mapping with :func:`yaml.safe_load` only.

    Arbitrary objects, custom tags that construct Python types, ``eval``,
    ``lambda``, and other executable configuration are rejected.
    """
    file_path = Path(path)
    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"Unable to read YAML file: {file_path}",
            code="CONFIG_YAML_UNREADABLE",
        ) from exc

    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"Invalid YAML syntax in {file_path}",
            code="CONFIG_YAML_SYNTAX",
        ) from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(
            f"YAML root must be a mapping in {file_path}",
            code="CONFIG_YAML_ROOT",
        )
    for key in loaded:
        if not isinstance(key, str):
            raise ConfigError(
                f"YAML mapping keys must be strings in {file_path}",
                code="CONFIG_YAML_KEY_TYPE",
            )
    _reject_executable_yaml(loaded)
    return loaded


class AISettings(BaseModel):
    """OpenAI / Pydantic AI defaults (DEC-004)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["openai"] = "openai"
    model: str = "gpt-5.6-sol"
    api: Literal["responses"] = "responses"
    reasoning_effort: Literal["medium"] = "medium"
    store: bool = False
    builtin_web_search: bool = False
    model_fallback: bool = False
    max_attempts: int = Field(default=2, ge=1, le=2)
    prompt_version: str = "v1"
    openai_api_key: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def _lock_first_release_ai_policy(self) -> Self:
        if self.store:
            raise ValueError("AI store must remain false (DEC-004)")
        if self.builtin_web_search:
            raise ValueError("Built-in web search must remain disabled (DEC-004)")
        if self.model_fallback:
            raise ValueError("Automatic model fallback must remain disabled (DEC-004)")
        if self.max_attempts > 2:
            raise ValueError("AI max_attempts must be at most 2 (DEC-004)")
        if self.model != "gpt-5.6-sol":
            raise ValueError("First-release AI model must be gpt-5.6-sol (DEC-004)")
        if self.reasoning_effort != "medium":
            raise ValueError("First-release reasoning_effort must be medium (DEC-004)")
        return self


class TelegramBotSettings(BaseModel):
    """One Telegram Bot environment (staging or production). DEC-005 / DEC-010."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bot_token: SecretStr | None = Field(default=None, repr=False)
    webhook_secret: SecretStr | None = Field(default=None, repr=False)
    allowed_user_ids: tuple[int, ...] = ()
    allowed_chat_ids: tuple[int, ...] = ()
    transport: TelegramTransport = TelegramTransport.LONG_POLLING
    approval_method: ApprovalMethod = ApprovalMethod.TELEGRAM
    approval_scope: ApprovalScope = ApprovalScope.PAPER
    enabled: bool = False

    @field_validator("allowed_user_ids", "allowed_chat_ids", mode="before")
    @classmethod
    def _coerce_id_tuple(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, (str, bytes)):
            raise ValueError("Telegram allowlists must be numeric IDs, not strings")
        if isinstance(value, int):
            return (value,)
        return value

    @field_validator("allowed_user_ids", "allowed_chat_ids")
    @classmethod
    def _validate_int64_ids(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        for item in value:
            if not isinstance(item, int) or isinstance(item, bool):
                raise ValueError("Telegram allowlist entries must be integers")
            if not _is_int64(item):
                raise ValueError("Telegram allowlist IDs must fit in signed 64-bit")
            if item <= 0:
                raise ValueError("Telegram allowlists accept only positive private user/chat IDs")
        return value

    @model_validator(mode="after")
    def _lock_first_release_telegram_policy(self) -> Self:
        if self.transport is not TelegramTransport.LONG_POLLING:
            raise ValueError("First-release Telegram transport must be long_polling (DEC-005)")
        if self.approval_method is not ApprovalMethod.TELEGRAM:
            raise ValueError("Telegram approval_method must be telegram (DEC-005)")
        if self.approval_scope is not ApprovalScope.PAPER:
            raise ValueError("Telegram approval_scope must be paper (DEC-005)")
        if self.enabled and self.bot_token is None:
            raise ValueError("enabled Telegram bot requires bot_token")
        if self.enabled and not self.allowed_user_ids:
            raise ValueError("enabled Telegram bot requires allowed_user_ids")
        if self.enabled and not self.allowed_chat_ids:
            raise ValueError("enabled Telegram bot requires allowed_chat_ids")
        return self


class WebAuthnSettings(BaseModel):
    """Live Passkey/WebAuthn prerequisites (DEC-006 / DEC-015 / DEC-016)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: str | None = None
    rp_id: str | None = None
    credential_ids: tuple[str, ...] = ()
    approval_method: ApprovalMethod = ApprovalMethod.WEBAUTHN
    approval_scope: ApprovalScope = ApprovalScope.LIVE
    bootstrap_closed: bool = True
    webauthn_server_secret: SecretStr | None = Field(default=None, repr=False)

    @field_validator("credential_ids", mode="before")
    @classmethod
    def _coerce_credentials(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        return value

    @field_validator("credential_ids")
    @classmethod
    def _validate_credentials(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(credential_id.strip() for credential_id in value)
        if any(not credential_id for credential_id in normalized):
            raise ValueError("WebAuthn credential IDs must be non-empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("WebAuthn recovery credentials must have distinct IDs")
        return normalized

    @field_validator("origin")
    @classmethod
    def _validate_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != value.strip():
            raise ValueError("WebAuthn origin must not contain surrounding whitespace")
        parsed = urlsplit(value)
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("WebAuthn origin contains an invalid port") from exc
        if parsed.scheme != "https" or parsed.hostname is None:
            raise ValueError("WebAuthn origin must be an absolute fixed https:// origin (DEC-006)")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("WebAuthn origin must not contain user information")
        if parsed.path or parsed.query or parsed.fragment:
            raise ValueError("WebAuthn origin must not contain a path, query, or fragment")
        if parsed.hostname.endswith("."):
            raise ValueError("WebAuthn origin hostname must not have a trailing dot")
        hostname = parsed.hostname.lower()
        authority = f"[{hostname}]" if ":" in hostname else hostname
        if parsed.port not in (None, 443):
            authority = f"{authority}:{parsed.port}"
        return f"https://{authority}"

    @field_validator("rp_id")
    @classmethod
    def _validate_rp_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized != value.lower() or not _RP_ID_PATTERN.fullmatch(normalized):
            raise ValueError("WebAuthn rp_id must be a bare hostname")
        return normalized

    @model_validator(mode="after")
    def _lock_live_method_scope(self) -> Self:
        if self.approval_method is not ApprovalMethod.WEBAUTHN:
            raise ValueError("Live WebAuthn approval_method must be webauthn (DEC-006)")
        if self.approval_scope is not ApprovalScope.LIVE:
            raise ValueError("Live WebAuthn approval_scope must be live (DEC-006)")
        if self.origin is not None and self.rp_id is not None:
            origin_host = urlsplit(self.origin).hostname
            if origin_host is None or origin_host.lower() != self.rp_id:
                raise ValueError(
                    "First-release WebAuthn origin hostname must exactly match rp_id (DEC-006)"
                )
        return self

    def is_complete_for_live(self) -> bool:
        """Return True when live broker-write prerequisites are present."""
        return (
            self.origin is not None
            and self.rp_id is not None
            and len(self.credential_ids) >= 2
            and len(set(self.credential_ids)) == len(self.credential_ids)
            and all(self.credential_ids)
            and self.approval_method is ApprovalMethod.WEBAUTHN
            and self.approval_scope is ApprovalScope.LIVE
            and self.bootstrap_closed
        )


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


class _YamlSettingsSource(PydanticBaseSettingsSource):
    """Provide validated risk/strategy YAML below dotenv/env priority."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        del field
        yaml_data = _YAML_SETTINGS_DATA.get() or {}
        return yaml_data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(_YAML_SETTINGS_DATA.get() or {})


class Settings(BaseSettings):
    """Application settings with paper-safe defaults (design.md §12)."""

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        case_sensitive=False,
        validate_default=True,
    )

    ainvest_env: AinvestEnv = Field(
        default=AinvestEnv.DEVELOPMENT,
        validation_alias=AliasChoices("AINVEST_ENV", "ainvest_env"),
    )
    trading_mode: TradingMode = Field(
        default=TradingMode.PAPER,
        validation_alias=AliasChoices("TRADING_MODE", "trading_mode"),
    )
    live_trading_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("LIVE_TRADING_ENABLED", "live_trading_enabled"),
    )
    require_human_approval: bool = Field(
        default=True,
        validation_alias=AliasChoices("REQUIRE_HUMAN_APPROVAL", "require_human_approval"),
    )
    regular_trading_hours_only: bool = Field(
        default=True,
        validation_alias=AliasChoices("REGULAR_TRADING_HOURS_ONLY", "regular_trading_hours_only"),
    )
    require_complete_risk_limits: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "REQUIRE_COMPLETE_RISK_LIMITS", "require_complete_risk_limits"
        ),
    )

    ai: AISettings = Field(default_factory=AISettings)
    telegram_staging: TelegramBotSettings = Field(default_factory=TelegramBotSettings)
    telegram_production: TelegramBotSettings = Field(default_factory=TelegramBotSettings)
    webauthn: WebAuthnSettings = Field(default_factory=WebAuthnSettings)

    risk: RiskLimitsDocument = Field(default_factory=RiskLimitsDocument)
    strategies: StrategiesDocument = Field(default_factory=StrategiesDocument)

    database_password: SecretStr | None = Field(default=None, repr=False)
    robinhood_oauth_token: SecretStr | None = Field(default=None, repr=False)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Apply explicit > env > dotenv > file-secret > YAML precedence."""
        del cls
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            _YamlSettingsSource(settings_cls),
        )

    @field_validator("regular_trading_hours_only")
    @classmethod
    def _lock_regular_hours(cls, value: bool) -> bool:
        # DEC-001: first release rejects disabling regular-hours-only.
        if value is not True:
            raise ValueError(
                "REGULAR_TRADING_HOURS_ONLY cannot be false in the first release (DEC-001)"
            )
        return value

    @field_validator("require_complete_risk_limits")
    @classmethod
    def _lock_complete_risk_limits(cls, value: bool) -> bool:
        # DEC-002: first release rejects disabling complete risk limits.
        if value is not True:
            raise ValueError(
                "REQUIRE_COMPLETE_RISK_LIMITS cannot be false in the first release (DEC-002)"
            )
        return value

    @model_validator(mode="after")
    def _reject_unsafe_combinations(self) -> Self:
        live_requested = self.trading_mode is TradingMode.LIVE or self.live_trading_enabled

        if self.trading_mode is TradingMode.LIVE and not self.live_trading_enabled:
            raise ValueError("TRADING_MODE=live requires LIVE_TRADING_ENABLED=true")

        if self.live_trading_enabled and self.trading_mode is not TradingMode.LIVE:
            raise ValueError("LIVE_TRADING_ENABLED=true requires TRADING_MODE=live")

        if live_requested and not self.require_human_approval:
            raise ValueError("Live trading requires REQUIRE_HUMAN_APPROVAL=true")

        if live_requested and not self.webauthn.is_complete_for_live():
            raise ValueError(
                "Live trading requires fixed WebAuthn origin, rp_id, at least two "
                "credentials, approval_method=webauthn, approval_scope=live, and "
                "closed bootstrap (DEC-006)"
            )

        return self

    @property
    def is_production(self) -> bool:
        """True when running under the production environment label."""
        return self.ainvest_env is AinvestEnv.PRODUCTION

    @property
    def is_live_requested(self) -> bool:
        """True when live mode or the live enablement flag is set."""
        return self.trading_mode is TradingMode.LIVE or self.live_trading_enabled


@contextmanager
def _temporary_environ(environ: Mapping[str, str]) -> Iterator[None]:
    previous = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update({str(key): str(value) for key, value in environ.items()})
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def load_settings(
    *,
    env_file: Path | str | None = None,
    risk_yaml: Path | str | None = None,
    strategies_yaml: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    **overrides: Any,
) -> Settings:
    """Load Settings using documented precedence and fail-closed validation.

    Parameters
    ----------
    env_file:
        Optional ``.env`` path. When ``environ`` is provided, dotenv loading is
        disabled unless ``env_file`` is set explicitly.
    risk_yaml / strategies_yaml:
        Optional YAML paths merged under ``risk`` / ``strategies`` before env
        overrides.
    environ:
        Optional environment mapping used instead of the process environment.
        Isolates unit tests from ambient variables.
    overrides:
        Explicit keyword overrides applied last (highest precedence).
    """
    yaml_data: dict[str, Any] = {}

    if risk_yaml is not None:
        risk_raw = load_yaml_mapping(risk_yaml)
        try:
            yaml_data["risk"] = RiskLimitsDocument.model_validate(risk_raw).model_dump()
        except ValidationError as exc:
            raise ConfigError(
                _validation_error_message(exc),
                code="CONFIG_RISK_INVALID",
            ) from exc

    if strategies_yaml is not None:
        strategies_raw = load_yaml_mapping(strategies_yaml)
        try:
            yaml_data["strategies"] = StrategiesDocument.model_validate(strategies_raw).model_dump()
        except ValidationError as exc:
            raise ConfigError(
                _validation_error_message(exc),
                code="CONFIG_STRATEGIES_INVALID",
            ) from exc

    init_data = dict(overrides)

    def _build(*, dotenv: Path | str | None) -> Settings:
        yaml_token = _YAML_SETTINGS_DATA.set(yaml_data)
        try:
            if dotenv is not None:
                return Settings(_env_file=dotenv, **init_data)  # type: ignore[call-arg]
            return Settings(_env_file=None, **init_data)  # type: ignore[call-arg]
        except ValidationError as exc:
            raise ConfigError(
                _validation_error_message(exc),
                code="CONFIG_INVALID",
            ) from exc
        finally:
            _YAML_SETTINGS_DATA.reset(yaml_token)

    if environ is not None:
        with _temporary_environ(environ):
            return _build(dotenv=env_file)

    if env_file is not None:
        return _build(dotenv=env_file)
    return _build(dotenv=".env")
