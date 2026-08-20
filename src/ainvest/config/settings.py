"""Settings models and load_settings with fail-closed safe defaults."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Final, Literal, Self
from urllib.parse import urlsplit

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
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SecretsSettingsSource,
    SettingsConfigDict,
    SettingsError,
)

from ainvest.config.documents import RiskLimitsDocument, StrategiesDocument
from ainvest.config.errors import (
    AinvestEnv,
    ApprovalMethod,
    ApprovalScope,
    ConfigError,
    TelegramTransport,
    TradingMode,
)
from ainvest.config.yaml import _validation_error_message, load_yaml_mapping

# Signed 64-bit bounds for Telegram numeric identities (DEC-005).
_INT64_MIN: Final[int] = -(2**63)
_INT64_MAX: Final[int] = 2**63 - 1
MAX_TELEGRAM_TOKEN_FILE_BYTES: Final[int] = 256
TELEGRAM_BOT_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[1-9][0-9]{5,19}:[A-Za-z0-9_-]{30,128}$"
)
TELEGRAM_BOT_TOKEN_FILENAMES: Final[Mapping[str, str]] = {
    "telegram_staging": "TELEGRAM_STAGING__BOT_TOKEN",
    "telegram_production": "TELEGRAM_PRODUCTION__BOT_TOKEN",
}
ROBINHOOD_READ_ACCOUNT_FILENAME: Final[str] = "ROBINHOOD_READ_ACCOUNT_NUMBER"
MAX_ROBINHOOD_ACCOUNT_FILE_BYTES: Final[int] = 130
_ROBINHOOD_ACCOUNT_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[\x21-\x7e]{1,128}$")

_RP_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?=.{1,253}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
    r"(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*$"
)

_YAML_SETTINGS_DATA: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "ainvest_yaml_settings_data",
    default=None,
)


def _is_int64(value: int) -> bool:
    return _INT64_MIN <= value <= _INT64_MAX


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


class TelegramRecipient(BaseModel):
    """One explicitly bound Telegram user/private-chat authorization pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: int
    private_chat_id: int

    @field_validator("user_id", "private_chat_id", mode="before")
    @classmethod
    def _require_integer(cls, value: object) -> object:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("Telegram recipient IDs must be integers")
        return value

    @field_validator("user_id", "private_chat_id")
    @classmethod
    def _validate_positive_int64(cls, value: int) -> int:
        if not _is_int64(value) or value <= 0:
            raise ValueError("Telegram recipient IDs must be positive signed 64-bit integers")
        return value


class TelegramBotSettings(BaseModel):
    """One Telegram Bot environment (staging or production). DEC-005 / DEC-010."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bot_token: SecretStr | None = Field(default=None, repr=False)
    webhook_secret: SecretStr | None = Field(default=None, repr=False)
    expected_bot_id: int | None = None
    allowed_recipients: tuple[TelegramRecipient, ...] = ()
    transport: TelegramTransport = TelegramTransport.LONG_POLLING
    approval_method: ApprovalMethod = ApprovalMethod.TELEGRAM
    approval_scope: ApprovalScope = ApprovalScope.PAPER
    enabled: bool = False

    @field_validator("allowed_recipients", mode="before")
    @classmethod
    def _coerce_recipients(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, (str, bytes, dict)):
            raise ValueError("Telegram recipients must be an array of bound records")
        return value

    @field_validator("expected_bot_id", mode="before")
    @classmethod
    def _reject_non_numeric_bot_id(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("Telegram expected_bot_id must be an integer")
        if isinstance(value, str) and value.isascii() and value.isdigit():
            return value
        if not isinstance(value, int):
            raise ValueError("Telegram expected_bot_id must be an integer")
        return value

    @field_validator("expected_bot_id")
    @classmethod
    def _validate_expected_bot_id(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not _is_int64(value) or value <= 0:
            raise ValueError("Telegram expected_bot_id must be a positive signed 64-bit integer")
        return value

    @field_validator("allowed_recipients")
    @classmethod
    def _require_unique_recipients(
        cls, value: tuple[TelegramRecipient, ...]
    ) -> tuple[TelegramRecipient, ...]:
        pairs = {(item.user_id, item.private_chat_id) for item in value}
        if len(pairs) != len(value):
            raise ValueError("Telegram recipient pairs must be unique")
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
        if self.enabled and self.expected_bot_id is None:
            raise ValueError("enabled Telegram bot requires expected_bot_id")
        if self.enabled and not self.allowed_recipients:
            raise ValueError("enabled Telegram bot requires allowed_recipients")
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


class _YamlSettingsSource(PydanticBaseSettingsSource):
    """Provide validated risk/strategy YAML below dotenv/env priority."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        del field
        yaml_data = _YAML_SETTINGS_DATA.get() or {}
        return yaml_data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(_YAML_SETTINGS_DATA.get() or {})


class _TelegramTokenFileSecretSource(PydanticBaseSettingsSource):
    """Load only the two nested Telegram tokens from explicit secret files."""

    def __init__(self, settings_cls: type[BaseSettings], secrets_dir: object) -> None:
        super().__init__(settings_cls)
        self._secrets_dir = secrets_dir

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        del field
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        if self._secrets_dir is None or isinstance(self._secrets_dir, (list, tuple, set)):
            return {}
        directory = Path(self._secrets_dir)  # type: ignore[arg-type]
        try:
            if not directory.exists() or not directory.is_dir():
                return {}
            entries = {entry.name: entry for entry in directory.iterdir()}
        except OSError:
            raise SettingsError("Unable to read Telegram token file secrets") from None
        values: dict[str, Any] = {}
        for environment, filename in TELEGRAM_BOT_TOKEN_FILENAMES.items():
            path = entries.get(filename)
            if path is None:
                continue
            try:
                if not path.is_file():
                    raise SettingsError("Telegram token secret must be a regular file")
                with path.open("rb") as handle:
                    raw = handle.read(MAX_TELEGRAM_TOKEN_FILE_BYTES + 1)
            except (OSError, SettingsError):
                raise SettingsError("Unable to read Telegram token file secret") from None
            if not raw or len(raw) > MAX_TELEGRAM_TOKEN_FILE_BYTES:
                raise SettingsError("Invalid Telegram token file secret")
            try:
                token = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise SettingsError("Invalid Telegram token file secret") from None
            # Secret tooling commonly writes one POSIX terminal LF.  Permit
            # exactly that normalization and leave every other byte subject to
            # the token grammar; never hide whitespace/control corruption.
            if token.endswith("\n"):
                token = token[:-1]
            if TELEGRAM_BOT_TOKEN_PATTERN.fullmatch(token) is None:
                raise SettingsError("Invalid Telegram token file secret")
            values[environment] = {"bot_token": token}
        return values


class _RobinhoodAccountFileSecretSource(PydanticBaseSettingsSource):
    """Load only the exact P05-T9 account reference from an explicit directory."""

    def __init__(self, settings_cls: type[BaseSettings], secrets_dir: object) -> None:
        super().__init__(settings_cls)
        self._secrets_dir = secrets_dir

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        del field
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        if self._secrets_dir is None or isinstance(self._secrets_dir, (list, tuple, set)):
            return {}
        directory = Path(self._secrets_dir)  # type: ignore[arg-type]
        try:
            if not directory.exists() or not directory.is_dir():
                return {}
            entries = {entry.name: entry for entry in directory.iterdir()}
            path = entries.get(ROBINHOOD_READ_ACCOUNT_FILENAME)
            if path is None:
                return {}
            no_follow = getattr(os, "O_NOFOLLOW", None)
            nonblock = getattr(os, "O_NONBLOCK", None)
            if no_follow is None or nonblock is None:
                raise SettingsError("Secure Robinhood account file open is unavailable")
            descriptor = os.open(path, os.O_RDONLY | no_follow | nonblock)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise SettingsError("Robinhood account secret must be a regular file")
                if metadata.st_size > MAX_ROBINHOOD_ACCOUNT_FILE_BYTES:
                    raise SettingsError("Invalid Robinhood account file secret")
                raw = os.read(descriptor, MAX_ROBINHOOD_ACCOUNT_FILE_BYTES)
                if len(raw) != metadata.st_size:
                    raise SettingsError("Robinhood account file changed while reading")
            finally:
                os.close(descriptor)
        except (OSError, SettingsError):
            raise SettingsError("Unable to read Robinhood account file secret") from None
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise SettingsError("Invalid Robinhood account file secret") from None
        if value.endswith("\n"):
            value = value[:-1]
        if _ROBINHOOD_ACCOUNT_PATTERN.fullmatch(value) is None:
            raise SettingsError("Invalid Robinhood account file secret")
        return {ROBINHOOD_READ_ACCOUNT_FILENAME: value}


def _account_setting_only(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if key.casefold()
        in {
            ROBINHOOD_READ_ACCOUNT_FILENAME.casefold(),
            "robinhood_read_account_number",
        }
    }


class _RobinhoodAccountEnvSource(EnvSettingsSource):
    """Preserve empty account values for exact validation without global drift."""

    def __call__(self) -> dict[str, Any]:
        return _account_setting_only(super().__call__())


class _RobinhoodAccountDotEnvSource(DotEnvSettingsSource):
    """Preserve empty dotenv account values while other fields keep stock behavior."""

    def __call__(self) -> dict[str, Any]:
        return _account_setting_only(super().__call__())


class _FilteredStockFileSecretSource(SecretsSettingsSource):
    """Preserve stock file secrets while excluding Telegram top-level JSON."""

    _EXCLUDED_FIELDS: Final[frozenset[str]] = frozenset(
        {
            "robinhood_read_account_number",
            "telegram_staging",
            "telegram_production",
        }
    )

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        aliases = getattr(field.validation_alias, "choices", ())
        normalized = {field_name.casefold(), *(str(alias).casefold() for alias in aliases)}
        if normalized & {name.casefold() for name in self._EXCLUDED_FIELDS}:
            return None, field_name, False
        return super().get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        try:
            values = super().__call__()
        except (OSError, UnicodeError, SettingsError):
            raise SettingsError("Unable to load file-secret configuration") from None
        for key in tuple(values):
            if key.casefold() == "robinhood_read_account_number":
                values.pop(key)
        return values


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
    robinhood_read_account_number: SecretStr | None = Field(
        default=None,
        repr=False,
        validation_alias=AliasChoices(
            "ROBINHOOD_READ_ACCOUNT_NUMBER",
            "robinhood_read_account_number",
        ),
    )

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
            _RobinhoodAccountEnvSource(settings_cls, env_ignore_empty=False),
            env_settings,
            _RobinhoodAccountDotEnvSource(
                settings_cls,
                env_file=getattr(dotenv_settings, "env_file", None),
                env_file_encoding=getattr(dotenv_settings, "env_file_encoding", None),
                env_ignore_empty=False,
            ),
            dotenv_settings,
            _TelegramTokenFileSecretSource(
                settings_cls,
                getattr(file_secret_settings, "secrets_dir", None),
            ),
            _RobinhoodAccountFileSecretSource(
                settings_cls,
                getattr(file_secret_settings, "secrets_dir", None),
            ),
            _FilteredStockFileSecretSource(
                settings_cls,
                secrets_dir=getattr(file_secret_settings, "secrets_dir", None),
            ),
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

    @field_validator("robinhood_read_account_number", mode="before")
    @classmethod
    def _validate_robinhood_read_account_number(cls, value: object) -> SecretStr | None:
        del cls
        if value is None:
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not isinstance(raw, str) or _ROBINHOOD_ACCOUNT_PATTERN.fullmatch(raw) is None:
            raise ValueError(
                "ROBINHOOD_READ_ACCOUNT_NUMBER must be 1..128 visible ASCII characters"
            )
        return SecretStr(raw)

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

        staging_bot_id = self.telegram_staging.expected_bot_id
        production_bot_id = self.telegram_production.expected_bot_id
        if staging_bot_id is not None and staging_bot_id == production_bot_id:
            raise ValueError("staging and production Telegram expected_bot_id values must differ")

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
    secrets_dir: Path | str | None = None,
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
    secrets_dir:
        Optional explicit file-secret directory. No implicit directory search
        is performed. The Telegram token source recognizes only the exact
        environment-specific filenames documented by P05-T4.
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
                return Settings(  # type: ignore[call-arg]
                    _env_file=dotenv,
                    _secrets_dir=secrets_dir,
                    **init_data,
                )
            return Settings(  # type: ignore[call-arg]
                _env_file=None,
                _secrets_dir=secrets_dir,
                **init_data,
            )
        except (ValidationError, SettingsError) as exc:
            raise ConfigError(
                _validation_error_message(exc)
                if isinstance(exc, ValidationError)
                else "Unable to load file-secret configuration",
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
