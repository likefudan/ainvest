"""Unit tests for fail-closed configuration loading (P01-T4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from ainvest.config import (
    CONFIG_PRECEDENCE,
    AISettings,
    ApprovalMethod,
    ApprovalScope,
    ConfigError,
    RobinhoodAccountSecretInvalid,
    Settings,
    TelegramBotSettings,
    TelegramRecipient,
    TradingMode,
    WebAuthnSettings,
    load_robinhood_read_account_number,
    load_settings,
    load_yaml_mapping,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_RISK = REPO_ROOT / "config" / "risk.example.yaml"
EXAMPLE_STRATEGIES = REPO_ROOT / "config" / "strategies.example.yaml"
FAKE_STAGING_FILE_TOKEN = "900000001:" + ("A" * 35)
FAKE_PRODUCTION_FILE_TOKEN = "900000002:" + ("B" * 35)
FAKE_ACCOUNT_REFERENCE = "synthetic-account-reference"


def _complete_webauthn() -> WebAuthnSettings:
    return WebAuthnSettings(
        origin="https://approve.example.com",
        rp_id="approve.example.com",
        credential_ids=("cred_primary", "cred_recovery"),
        approval_method=ApprovalMethod.WEBAUTHN,
        approval_scope=ApprovalScope.LIVE,
        bootstrap_closed=True,
    )


@pytest.mark.unit
def test_missing_optional_config_starts_paper_safe() -> None:
    """Absent optional config loads paper-safe defaults."""
    settings = load_settings(environ={}, env_file=None)

    assert settings.trading_mode is TradingMode.PAPER
    assert settings.live_trading_enabled is False
    assert settings.require_human_approval is True
    assert settings.regular_trading_hours_only is True
    assert settings.require_complete_risk_limits is True
    assert settings.ai.provider == "openai"
    assert settings.ai.model == "gpt-5.6-sol"
    assert settings.ai.api == "responses"
    assert settings.ai.reasoning_effort == "medium"
    assert settings.ai.store is False
    assert settings.ai.builtin_web_search is False
    assert settings.ai.model_fallback is False
    assert settings.ai.max_attempts == 2
    assert settings.telegram_staging.enabled is False
    assert settings.telegram_production.enabled is False
    assert settings.telegram_staging.approval_scope is ApprovalScope.PAPER
    assert settings.webauthn.is_complete_for_live() is False
    assert settings.is_live_requested is False


@pytest.mark.unit
def test_config_precedence_constant_documents_order() -> None:
    """The public precedence declaration matches the implemented source order."""
    assert CONFIG_PRECEDENCE == (
        "built-in fail-closed defaults",
        "optional YAML files (safe loader)",
        "optional file-secret directory",
        "optional .env file",
        "environment variables",
        "explicit load_settings overrides",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("environ", "dotenv", "risk_yaml_text", "overrides", "field", "expected"),
    [
        (
            {"TRADING_MODE": "research"},
            None,
            None,
            {"trading_mode": TradingMode.PAPER},
            "trading_mode",
            TradingMode.PAPER,
        ),
        (
            {"TRADING_MODE": "research"},
            "TRADING_MODE=paper\n",
            None,
            {},
            "trading_mode",
            TradingMode.RESEARCH,
        ),
        (
            {"RISK__NOTES": "environment-value"},
            None,
            "schema_version: '1'\nnotes: yaml-value\n",
            {},
            "risk.notes",
            "environment-value",
        ),
        (
            {},
            "RISK__NOTES=dotenv-value\n",
            "schema_version: '1'\nnotes: yaml-value\n",
            {},
            "risk.notes",
            "dotenv-value",
        ),
        (
            {"RISK__NOTES": "environment-value"},
            None,
            "schema_version: '1'\nnotes: yaml-value\n",
            {"risk": {"schema_version": "1", "notes": "explicit-value"}},
            "risk.notes",
            "explicit-value",
        ),
    ],
    ids=(
        "explicit_beats_environment",
        "environment_beats_dotenv",
        "environment_beats_risk_yaml",
        "dotenv_beats_risk_yaml",
        "explicit_beats_environment_and_risk_yaml",
    ),
)
def test_config_source_precedence(
    tmp_path: Path,
    environ: dict[str, str],
    dotenv: str | None,
    risk_yaml_text: str | None,
    overrides: dict[str, object],
    field: str,
    expected: object,
) -> None:
    """Higher-priority config sources win over lower ones (CONFIG_PRECEDENCE)."""
    env_file: Path | None = None
    if dotenv is not None:
        env_file = tmp_path / ".env"
        env_file.write_text(dotenv, encoding="utf-8")

    risk_yaml: Path | None = None
    if risk_yaml_text is not None:
        risk_yaml = tmp_path / "risk.yaml"
        risk_yaml.write_text(risk_yaml_text, encoding="utf-8")

    settings = load_settings(
        environ=environ,
        env_file=env_file,
        risk_yaml=risk_yaml,
        **overrides,  # type: ignore[arg-type]
    )

    actual: object = settings
    for part in field.split("."):
        actual = getattr(actual, part)
    assert actual == expected


@pytest.mark.unit
def test_unknown_settings_field_rejected() -> None:
    """Unknown Settings fields are rejected (production-safe extra=forbid)."""
    with pytest.raises(ConfigError, match="extra"):
        load_settings(environ={}, env_file=None, not_a_real_field=True)


@pytest.mark.unit
def test_unknown_yaml_field_rejected(tmp_path: Path) -> None:
    """Unknown top-level YAML keys fail closed."""
    path = tmp_path / "risk.yaml"
    path.write_text("schema_version: '1'\nunexpected_top_level: true\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_settings(environ={}, env_file=None, risk_yaml=path)
    assert exc_info.value.code == "CONFIG_RISK_INVALID"


@pytest.mark.unit
def test_invalid_types_rejected() -> None:
    """Invalid typed environment values raise ConfigError."""
    with pytest.raises(ConfigError):
        load_settings(
            environ={"LIVE_TRADING_ENABLED": "not-a-bool"},
            env_file=None,
        )


@pytest.mark.unit
def test_locked_regular_trading_hours_only_rejects_false() -> None:
    """DEC-001: first release rejects REGULAR_TRADING_HOURS_ONLY=false."""
    with pytest.raises(ConfigError, match="REGULAR_TRADING_HOURS_ONLY"):
        load_settings(
            environ={"REGULAR_TRADING_HOURS_ONLY": "false"},
            env_file=None,
        )


@pytest.mark.unit
def test_locked_require_complete_risk_limits_rejects_false() -> None:
    """DEC-002: first release rejects REQUIRE_COMPLETE_RISK_LIMITS=false."""
    with pytest.raises(ConfigError, match="REQUIRE_COMPLETE_RISK_LIMITS"):
        load_settings(
            environ={"REQUIRE_COMPLETE_RISK_LIMITS": "false"},
            env_file=None,
        )


@pytest.mark.unit
def test_dangerous_live_without_enablement_rejected() -> None:
    """TRADING_MODE=live without LIVE_TRADING_ENABLED fails closed."""
    with pytest.raises(ConfigError, match="LIVE_TRADING_ENABLED"):
        load_settings(
            environ={},
            env_file=None,
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=False,
            webauthn=_complete_webauthn(),
        )


@pytest.mark.unit
def test_dangerous_enablement_without_live_mode_rejected() -> None:
    """LIVE_TRADING_ENABLED without TRADING_MODE=live fails closed."""
    with pytest.raises(ConfigError, match="TRADING_MODE=live"):
        load_settings(
            environ={},
            env_file=None,
            trading_mode=TradingMode.PAPER,
            live_trading_enabled=True,
            webauthn=_complete_webauthn(),
        )


@pytest.mark.unit
def test_incomplete_live_config_rejected() -> None:
    """Live requests without complete WebAuthn prerequisites fail closed."""
    incomplete_cases = [
        WebAuthnSettings(),  # empty
        WebAuthnSettings(
            origin="https://approve.example.com",
            rp_id="approve.example.com",
            credential_ids=("only_one",),
        ),
    ]
    for webauthn in incomplete_cases:
        with pytest.raises(ConfigError):
            load_settings(
                environ={},
                env_file=None,
                trading_mode=TradingMode.LIVE,
                live_trading_enabled=True,
                webauthn=webauthn,
            )

    with pytest.raises(ValidationError, match="https://"):
        WebAuthnSettings(
            origin="http://insecure.example.com",
            rp_id="insecure.example.com",
            credential_ids=("a", "b"),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "origin",
    [
        "https://approve.example.com/",
        "https://approve.example.com/path",
        "https://approve.example.com?query=value",
        "https://approve.example.com#fragment",
        "https://user@approve.example.com",
    ],
)
def test_webauthn_rejects_non_origin_urls(origin: str) -> None:
    """A WebAuthn origin is scheme/host/port only, never a general URL."""
    with pytest.raises(ValidationError, match="origin"):
        WebAuthnSettings(
            origin=origin,
            rp_id="approve.example.com",
            credential_ids=("primary", "recovery"),
        )


@pytest.mark.unit
def test_webauthn_rejects_rp_id_unrelated_to_origin() -> None:
    """The first-release RP ID must exactly match the configured origin host."""
    with pytest.raises(ValidationError, match="exactly match"):
        WebAuthnSettings(
            origin="https://approve.example.com",
            rp_id="unrelated.example",
            credential_ids=("primary", "recovery"),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("https://APPROVE.Example.COM:8443", "https://approve.example.com:8443"),
        ("https://APPROVE.Example.COM:443", "https://approve.example.com"),
    ],
)
def test_webauthn_normalizes_origin(origin: str, expected: str) -> None:
    """Stored origins match the browser's canonical hostname and port form."""
    settings = WebAuthnSettings(
        origin=origin,
        rp_id="approve.example.com",
        credential_ids=("primary", "recovery"),
    )

    assert settings.origin == expected
    assert settings.rp_id == "approve.example.com"


@pytest.mark.unit
def test_webauthn_rejects_duplicate_recovery_credentials() -> None:
    """Two distinct credentials are required for recovery readiness."""
    with pytest.raises(ValidationError, match="distinct"):
        WebAuthnSettings(
            origin="https://approve.example.com",
            rp_id="approve.example.com",
            credential_ids=("duplicate", "duplicate"),
        )


@pytest.mark.unit
def test_complete_live_config_accepted() -> None:
    """Complete WebAuthn live prerequisites allow Settings construction."""
    settings = load_settings(
        environ={},
        env_file=None,
        trading_mode=TradingMode.LIVE,
        live_trading_enabled=True,
        webauthn=_complete_webauthn(),
    )
    assert settings.is_live_requested is True
    assert settings.webauthn.approval_method is ApprovalMethod.WEBAUTHN
    assert settings.webauthn.approval_scope is ApprovalScope.LIVE


@pytest.mark.unit
def test_live_without_human_approval_rejected() -> None:
    """Live trading cannot disable human approval."""
    with pytest.raises(ConfigError, match="REQUIRE_HUMAN_APPROVAL"):
        load_settings(
            environ={},
            env_file=None,
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=True,
            require_human_approval=False,
            webauthn=_complete_webauthn(),
        )


@pytest.mark.unit
def test_telegram_rejects_deprecated_lists_and_non_int64_recipient_ids() -> None:
    """Only bound positive-int64 recipient pairs are accepted."""
    with pytest.raises(ValidationError):
        TelegramBotSettings.model_validate({"allowed_user_ids": ["notanid"]})
    with pytest.raises(ValidationError):
        TelegramRecipient(user_id=2**63, private_chat_id=1)
    with pytest.raises(ValidationError):
        TelegramRecipient(user_id=1, private_chat_id=-100123)
    with pytest.raises(ValidationError):
        TelegramRecipient(user_id="1001", private_chat_id=2001)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        TelegramRecipient(user_id=True, private_chat_id=2001)
    with pytest.raises(ValidationError, match="unique"):
        TelegramBotSettings(
            allowed_recipients=(
                TelegramRecipient(user_id=1, private_chat_id=2),
                TelegramRecipient(user_id=1, private_chat_id=2),
            )
        )


@pytest.mark.unit
def test_telegram_requires_complete_enabled_config_and_distinct_bot_ids() -> None:
    recipient = TelegramRecipient(user_id=1001, private_chat_id=2001)
    with pytest.raises(ValidationError, match="signed 64-bit"):
        TelegramBotSettings(expected_bot_id=2**63)
    with pytest.raises(ValidationError, match="signed 64-bit"):
        TelegramBotSettings(expected_bot_id=0)
    with pytest.raises(ValidationError, match="integer"):
        TelegramBotSettings(expected_bot_id=True)
    with pytest.raises(ValidationError, match="bot_token"):
        TelegramBotSettings(enabled=True, expected_bot_id=901, allowed_recipients=(recipient,))
    with pytest.raises(ValidationError, match="expected_bot_id"):
        TelegramBotSettings(
            enabled=True,
            bot_token=SecretStr("synthetic-token"),
            allowed_recipients=(recipient,),
        )
    with pytest.raises(ValidationError, match="allowed_recipients"):
        TelegramBotSettings(
            enabled=True,
            bot_token=SecretStr("synthetic-token"),
            expected_bot_id=901,
        )
    with pytest.raises(ValidationError, match="must differ"):
        Settings(
            telegram_staging=TelegramBotSettings(expected_bot_id=901),
            telegram_production=TelegramBotSettings(expected_bot_id=901),
        )


@pytest.mark.unit
def test_telegram_rejects_live_scope_and_webhook() -> None:
    """First-release Telegram is long polling + paper scope only (DEC-005)."""
    with pytest.raises(ValidationError, match="paper"):
        TelegramBotSettings(approval_scope=ApprovalScope.LIVE)
    with pytest.raises(ValidationError, match="long_polling"):
        TelegramBotSettings(transport="webhook")  # type: ignore[arg-type]


@pytest.mark.unit
def test_ai_defaults_and_locked_policy() -> None:
    """AI defaults match DEC-004 and reject unsafe toggles."""
    ai = AISettings()
    assert ai.model == "gpt-5.6-sol"
    assert ai.max_attempts == 2
    with pytest.raises(ValidationError):
        AISettings(store=True)
    with pytest.raises(ValidationError):
        AISettings(builtin_web_search=True)
    with pytest.raises(ValidationError):
        AISettings(model_fallback=True)
    with pytest.raises(ValidationError):
        AISettings(max_attempts=3)
    with pytest.raises(ValidationError):
        AISettings(model="gpt-other")


@pytest.mark.unit
def test_secret_repr_redaction() -> None:
    """Secrets use repr=False / SecretStr and do not appear in repr()."""
    settings = load_settings(
        environ={},
        env_file=None,
        database_password=SecretStr("super-secret-db-password"),
        ai=AISettings(openai_api_key=SecretStr("sk-secret-openai-key")),
        telegram_staging=TelegramBotSettings(
            bot_token=SecretStr("123456789:AASecretTelegramTokenValue_____"),
        ),
    )
    rendered = repr(settings)
    assert "super-secret-db-password" not in rendered
    assert "sk-secret-openai-key" not in rendered
    assert "AASecretTelegramTokenValue" not in rendered


@pytest.mark.unit
def test_secret_not_echoed_in_validation_errors() -> None:
    """Validation errors must not echo secret material."""
    secret = "123456789:AAReallySecretTelegramBotTokenValueXX"
    with pytest.raises(ConfigError) as exc_info:
        load_settings(
            environ={},
            env_file=None,
            # Invalid nested type forces validation while carrying a secret.
            telegram_staging={
                "bot_token": secret,
                "allowed_user_ids": ["not-an-int"],
                "enabled": True,
            },
        )
    message = str(exc_info.value)
    assert secret not in message
    assert "AAReallySecretTelegramBotTokenValueXX" not in message


@pytest.mark.unit
def test_safe_yaml_loader_rejects_executable_and_unsafe_types(tmp_path: Path) -> None:
    """YAML loader rejects eval/lambda markers and non-mapping roots."""
    executable = tmp_path / "evil.yaml"
    executable.write_text("limits:\n  max: 'eval(open(\"x\").read())'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Executable"):
        load_yaml_mapping(executable)

    lambda_file = tmp_path / "lambda.yaml"
    lambda_file.write_text("notes: 'lambda x: x'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Executable"):
        load_yaml_mapping(lambda_file)

    list_root = tmp_path / "list.yaml"
    list_root.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_yaml_mapping(list_root)


@pytest.mark.unit
def test_safe_yaml_loader_rejects_python_objects(tmp_path: Path) -> None:
    """yaml.safe_load path must not construct arbitrary Python objects."""
    tagged = tmp_path / "tagged.yaml"
    # Explicit Python object tag — safe_load should raise YAMLError -> ConfigError.
    tagged.write_text("!!python/object/apply:os.system ['echo pwned']\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_yaml_mapping(tagged)


@pytest.mark.unit
def test_example_yaml_files_load() -> None:
    """Shipped example YAML files parse under the safe loader and document models."""
    assert EXAMPLE_RISK.is_file()
    assert EXAMPLE_STRATEGIES.is_file()
    settings = load_settings(
        environ={},
        env_file=None,
        risk_yaml=EXAMPLE_RISK,
        strategies_yaml=EXAMPLE_STRATEGIES,
    )
    assert settings.trading_mode is TradingMode.PAPER
    assert settings.risk.schema_version == "1"
    assert settings.strategies.strategies
    assert settings.strategies.strategies[0]["enabled"] is False
    # Examples must remain placeholder-only (no live enablement).
    risk_text = EXAMPLE_RISK.read_text(encoding="utf-8")
    assert "PLACEHOLDER_" in risk_text
    assert "eval(" not in risk_text


@pytest.mark.unit
def test_settings_repr_hides_secret_fields_on_model() -> None:
    """Field(repr=False) keeps secret attribute values out of Settings.repr."""
    field = Settings.model_fields["database_password"]
    assert field.repr is False
    ai_key = AISettings.model_fields["openai_api_key"]
    assert ai_key.repr is False
    bot_token = TelegramBotSettings.model_fields["bot_token"]
    assert bot_token.repr is False


@pytest.mark.unit
def test_explicit_telegram_token_file_secrets_load_and_remain_isolated(tmp_path: Path) -> None:
    staging_token = FAKE_STAGING_FILE_TOKEN
    production_token = FAKE_PRODUCTION_FILE_TOKEN
    (tmp_path / "TELEGRAM_STAGING__BOT_TOKEN").write_text(staging_token, encoding="utf-8")
    (tmp_path / "TELEGRAM_PRODUCTION__BOT_TOKEN").write_text(production_token, encoding="utf-8")

    settings = load_settings(environ={}, env_file=None, secrets_dir=tmp_path)

    assert settings.telegram_staging.bot_token is not None
    assert settings.telegram_production.bot_token is not None
    assert settings.telegram_staging.bot_token.get_secret_value() == staging_token
    assert settings.telegram_production.bot_token.get_secret_value() == production_token
    assert staging_token not in repr(settings)
    assert production_token not in repr(settings)


@pytest.mark.unit
def test_environment_and_explicit_values_override_telegram_file_secret(tmp_path: Path) -> None:
    (tmp_path / "TELEGRAM_STAGING__BOT_TOKEN").write_text(FAKE_STAGING_FILE_TOKEN, encoding="utf-8")
    recipient = TelegramRecipient(user_id=1001, private_chat_id=2001)

    from_environment = load_settings(
        environ={"TELEGRAM_STAGING__BOT_TOKEN": "environment-value"},
        env_file=None,
        secrets_dir=tmp_path,
    )
    assert from_environment.telegram_staging.bot_token is not None
    assert from_environment.telegram_staging.bot_token.get_secret_value() == "environment-value"

    explicit = load_settings(
        environ={"TELEGRAM_STAGING__BOT_TOKEN": "environment-value"},
        env_file=None,
        secrets_dir=tmp_path,
        telegram_staging=TelegramBotSettings(
            bot_token=SecretStr("explicit-value"),
            expected_bot_id=901,
            allowed_recipients=(recipient,),
        ),
    )
    assert explicit.telegram_staging.bot_token is not None
    assert explicit.telegram_staging.bot_token.get_secret_value() == "explicit-value"


@pytest.mark.unit
def test_dotenv_overrides_telegram_file_secret(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "TELEGRAM_PRODUCTION__BOT_TOKEN").write_text(
        FAKE_PRODUCTION_FILE_TOKEN, encoding="utf-8"
    )
    dotenv = tmp_path / ".env"
    dotenv.write_text("TELEGRAM_PRODUCTION__BOT_TOKEN=dotenv-value\n", encoding="utf-8")

    settings = load_settings(environ={}, env_file=dotenv, secrets_dir=secrets_dir)

    assert settings.telegram_production.bot_token is not None
    assert settings.telegram_production.bot_token.get_secret_value() == "dotenv-value"


@pytest.mark.unit
def test_no_implicit_secret_path_and_missing_directory_are_safe(tmp_path: Path) -> None:
    implicit = tmp_path / "TELEGRAM_STAGING__BOT_TOKEN"
    implicit.write_text("must-not-load", encoding="utf-8")

    settings = load_settings(environ={}, env_file=None)
    with pytest.warns(UserWarning, match="does not exist"):
        missing = load_settings(
            environ={},
            env_file=None,
            secrets_dir=tmp_path / "does-not-exist",
        )

    assert settings.telegram_staging.bot_token is None
    assert missing.telegram_staging.bot_token is None


@pytest.mark.unit
def test_file_secret_path_that_is_not_a_directory_is_sanitized(tmp_path: Path) -> None:
    not_a_directory = tmp_path / "secret-path"
    not_a_directory.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ConfigError, match="file-secret") as exc_info:
        load_settings(environ={}, env_file=None, secrets_dir=not_a_directory)

    assert exc_info.value.code == "CONFIG_INVALID"
    assert "not a directory" not in str(exc_info.value)


@pytest.mark.unit
def test_stock_file_secret_behavior_is_preserved(tmp_path: Path) -> None:
    (tmp_path / "DATABASE_PASSWORD").write_text("synthetic-database-secret", encoding="utf-8")

    settings = load_settings(environ={}, env_file=None, secrets_dir=tmp_path)

    assert settings.database_password is not None
    assert settings.database_password.get_secret_value() == "synthetic-database-secret"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("environment", "filename"),
    [
        ("telegram_staging", "TELEGRAM_STAGING"),
        ("telegram_staging", "telegram_staging"),
        ("telegram_staging", "TeLeGrAm_StAgInG"),
        ("telegram_staging", "telegram_staging__bot_token"),
        ("telegram_staging", "TELEGRAM_STAGING__BOT_TOKEN.txt"),
        ("telegram_staging", "TELEGRAM_STAGING__BOT_TOKEN_BACKUP"),
        ("telegram_production", "TELEGRAM_PRODUCTION"),
        ("telegram_production", "telegram_production"),
        ("telegram_production", "TeLeGrAm_PrOdUcTiOn"),
        ("telegram_production", "telegram_production__bot_token"),
        ("telegram_production", "TELEGRAM_PRODUCTION__BOT_TOKEN.txt"),
        ("telegram_production", "TELEGRAM_PRODUCTION__BOT_TOKEN_BACKUP"),
    ],
)
def test_unapproved_file_secret_names_cannot_inject_telegram_token(
    tmp_path: Path, environment: str, filename: str
) -> None:
    (tmp_path / filename).write_text(
        '{"bot_token":"bypass-token","expected_bot_id":900000001}',
        encoding="utf-8",
    )

    settings = load_settings(environ={}, env_file=None, secrets_dir=tmp_path)

    selected = getattr(settings, environment)
    assert selected.bot_token is None
    assert selected.expected_bot_id is None


@pytest.mark.unit
def test_exact_telegram_token_file_wins_without_stock_json_bypass(tmp_path: Path) -> None:
    (tmp_path / "TELEGRAM_STAGING__BOT_TOKEN").write_text(FAKE_STAGING_FILE_TOKEN, encoding="utf-8")
    (tmp_path / "TELEGRAM_STAGING").write_text(
        '{"bot_token":"bypass-token","expected_bot_id":900000999}',
        encoding="utf-8",
    )

    settings = load_settings(environ={}, env_file=None, secrets_dir=tmp_path)

    assert settings.telegram_staging.bot_token is not None
    assert settings.telegram_staging.bot_token.get_secret_value() == FAKE_STAGING_FILE_TOKEN
    assert settings.telegram_staging.expected_bot_id is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        b"\xff\xfe",
        b"x" * 257,
        b"not-a-telegram-token",
        b"",
        b" " + FAKE_STAGING_FILE_TOKEN.encode("ascii"),
        FAKE_STAGING_FILE_TOKEN.encode("ascii") + b" ",
        b"\t" + FAKE_STAGING_FILE_TOKEN.encode("ascii") + b"\t",
        FAKE_STAGING_FILE_TOKEN.encode("ascii") + b"\r\n",
        FAKE_STAGING_FILE_TOKEN.encode("ascii") + b"\n\n",
    ],
    ids=(
        "invalid_utf8",
        "oversize",
        "invalid_grammar",
        "empty",
        "leading_space",
        "trailing_space",
        "surrounding_tabs",
        "crlf",
        "repeated_lf",
    ),
)
def test_malformed_exact_telegram_token_file_is_stable_and_redacted(
    tmp_path: Path, raw: bytes
) -> None:
    (tmp_path / "TELEGRAM_STAGING__BOT_TOKEN").write_bytes(raw)

    with pytest.raises(ConfigError) as exc_info:
        load_settings(environ={}, env_file=None, secrets_dir=tmp_path)

    assert exc_info.value.code == "CONFIG_INVALID"
    assert str(exc_info.value) == "Unable to load file-secret configuration"
    assert "not-a-telegram-token" not in str(exc_info.value)


@pytest.mark.unit
@pytest.mark.parametrize("terminal", [b"", b"\n"], ids=("exact", "one_terminal_lf"))
def test_exact_telegram_token_file_accepts_at_most_one_terminal_lf(
    tmp_path: Path, terminal: bytes
) -> None:
    token_path = tmp_path / "TELEGRAM_STAGING__BOT_TOKEN"
    token_path.write_bytes(FAKE_STAGING_FILE_TOKEN.encode("ascii") + terminal)

    settings = load_settings(environ={}, env_file=None, secrets_dir=tmp_path)

    assert settings.telegram_staging.bot_token is not None
    assert settings.telegram_staging.bot_token.get_secret_value() == FAKE_STAGING_FILE_TOKEN


@pytest.mark.unit
def test_unreadable_exact_telegram_token_file_is_stable_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_path = tmp_path / "TELEGRAM_STAGING__BOT_TOKEN"
    token_path.write_text(FAKE_STAGING_FILE_TOKEN, encoding="utf-8")
    original_open: Any = Path.open

    def deny_token_read(path: Path, *args: object, **kwargs: object) -> Any:
        if path == token_path:
            raise PermissionError("provider detail containing a secret")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_token_read)

    with pytest.raises(ConfigError) as exc_info:
        load_settings(environ={}, env_file=None, secrets_dir=tmp_path)

    assert str(exc_info.value) == "Unable to load file-secret configuration"
    assert "provider detail" not in str(exc_info.value)


@pytest.mark.unit
def test_disabled_canonical_env_example_loads_without_secrets() -> None:
    settings = load_settings(environ={}, env_file=REPO_ROOT / ".env.example")

    assert settings.telegram_staging.enabled is False
    assert settings.telegram_production.enabled is False
    assert settings.telegram_staging.bot_token is None
    assert settings.telegram_production.bot_token is None
    assert settings.telegram_staging.allowed_recipients == (
        TelegramRecipient(user_id=900000101, private_chat_id=900000201),
    )


@pytest.mark.unit
@pytest.mark.parametrize("terminal", [b"", b"\n"], ids=("exact", "one_lf"))
@pytest.mark.parametrize("length", [1, 128])
def test_robinhood_account_exact_file_accepts_visible_ascii_boundaries(
    tmp_path: Path,
    terminal: bytes,
    length: int,
) -> None:
    raw = b"A" * length
    (tmp_path / "ROBINHOOD_READ_ACCOUNT_NUMBER").write_bytes(raw + terminal)

    account = load_robinhood_read_account_number(environ={}, env_file=None, secrets_dir=tmp_path)

    assert account is not None
    assert account.get_secret_value() == raw.decode("ascii")
    assert "A" * length not in repr(account)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"A" * 129,
        b"value\r\n",
        b"value\n\n",
        b" leading",
        b"trailing ",
        b"value\tpart",
        b"value\npart",
        b"value\x00part",
        "non-ascii-é".encode(),
        b"\xff",
    ],
    ids=(
        "empty",
        "129_bytes",
        "crlf",
        "repeated_lf",
        "leading_space",
        "trailing_space",
        "tab",
        "embedded_lf",
        "control",
        "non_ascii",
        "invalid_utf8",
    ),
)
def test_robinhood_account_exact_file_rejects_every_invalid_shape(
    tmp_path: Path,
    raw: bytes,
) -> None:
    (tmp_path / "ROBINHOOD_READ_ACCOUNT_NUMBER").write_bytes(raw)

    with pytest.raises(RobinhoodAccountSecretInvalid) as caught:
        load_robinhood_read_account_number(environ={}, env_file=None, secrets_dir=tmp_path)

    assert str(caught.value) == ""
    assert "non-ascii" not in str(caught.value)


@pytest.mark.unit
def test_robinhood_account_file_rejects_symlink_and_stock_alias_bypass(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.write_text(FAKE_ACCOUNT_REFERENCE, encoding="utf-8")
    canonical = tmp_path / "ROBINHOOD_READ_ACCOUNT_NUMBER"
    canonical.symlink_to(target)
    with pytest.raises(RobinhoodAccountSecretInvalid):
        load_robinhood_read_account_number(environ={}, env_file=None, secrets_dir=tmp_path)

    canonical.unlink()
    (tmp_path / "robinhood_read_account_number").write_text(
        FAKE_ACCOUNT_REFERENCE,
        encoding="utf-8",
    )
    account = load_robinhood_read_account_number(environ={}, env_file=None, secrets_dir=tmp_path)
    assert account is None


@pytest.mark.unit
def test_robinhood_account_source_precedence_is_exact(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "ROBINHOOD_READ_ACCOUNT_NUMBER").write_text(
        "file-value",
        encoding="utf-8",
    )
    dotenv = tmp_path / ".env"
    dotenv.write_text("ROBINHOOD_READ_ACCOUNT_NUMBER=dotenv-value\n", encoding="utf-8")

    from_yaml = load_robinhood_read_account_number(
        environ={}, env_file=None, yaml_value="yaml-value"
    )
    from_file = load_robinhood_read_account_number(
        environ={}, env_file=None, secrets_dir=secrets_dir, yaml_value="yaml-value"
    )
    from_dotenv = load_robinhood_read_account_number(
        environ={}, env_file=dotenv, secrets_dir=secrets_dir, yaml_value="yaml-value"
    )
    from_env = load_robinhood_read_account_number(
        environ={"ROBINHOOD_READ_ACCOUNT_NUMBER": "environment-value"},
        env_file=dotenv,
        secrets_dir=secrets_dir,
        yaml_value="yaml-value",
    )
    explicit = load_robinhood_read_account_number(
        environ={"ROBINHOOD_READ_ACCOUNT_NUMBER": "environment-value"},
        env_file=dotenv,
        secrets_dir=secrets_dir,
        yaml_value="yaml-value",
        explicit=SecretStr("explicit-value"),
    )

    assert from_yaml is not None and from_yaml.get_secret_value() == "yaml-value"
    assert from_file is not None and from_file.get_secret_value() == "file-value"
    assert from_dotenv is not None and from_dotenv.get_secret_value() == "dotenv-value"
    assert from_env is not None and from_env.get_secret_value() == "environment-value"
    assert explicit is not None and explicit.get_secret_value() == "explicit-value"


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    ["", " leading", "trailing ", "value\tpart", "value\npart", "é", "A" * 129],
)
def test_robinhood_account_environment_value_is_never_trimmed_or_normalized(
    value: str,
) -> None:
    with pytest.raises(RobinhoodAccountSecretInvalid):
        load_robinhood_read_account_number(
            environ={"ROBINHOOD_READ_ACCOUNT_NUMBER": value},
            env_file=None,
        )


@pytest.mark.unit
def test_robinhood_account_empty_dotenv_is_invalid_and_cannot_fall_back(
    tmp_path: Path,
) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "ROBINHOOD_READ_ACCOUNT_NUMBER").write_text(
        FAKE_ACCOUNT_REFERENCE,
        encoding="utf-8",
    )
    dotenv = tmp_path / ".env"
    dotenv.write_text("ROBINHOOD_READ_ACCOUNT_NUMBER=\n", encoding="utf-8")

    with pytest.raises(RobinhoodAccountSecretInvalid):
        load_robinhood_read_account_number(environ={}, env_file=dotenv, secrets_dir=secrets_dir)


@pytest.mark.unit
def test_invalid_account_sources_do_not_block_global_settings(tmp_path: Path) -> None:
    invalid_file = tmp_path / "secrets"
    invalid_file.mkdir()
    (invalid_file / "ROBINHOOD_READ_ACCOUNT_NUMBER").write_text(
        " invalid",
        encoding="utf-8",
    )
    invalid_dotenv = tmp_path / ".env"
    invalid_dotenv.write_text("ROBINHOOD_READ_ACCOUNT_NUMBER= invalid\n", encoding="utf-8")

    assert load_settings(environ={}, env_file=None, secrets_dir=invalid_file).trading_mode is (
        TradingMode.PAPER
    )
    assert load_settings(environ={}, env_file=invalid_dotenv).trading_mode is TradingMode.PAPER
    assert (
        load_settings(
            environ={"ROBINHOOD_READ_ACCOUNT_NUMBER": " invalid"}, env_file=None
        ).trading_mode
        is TradingMode.PAPER
    )


@pytest.mark.unit
def test_unrelated_empty_environment_and_dotenv_values_keep_stock_ignore_behavior(
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("DATABASE_PASSWORD=\n", encoding="utf-8")

    from_env = load_settings(environ={"DATABASE_PASSWORD": ""}, env_file=None)
    from_dotenv = load_settings(environ={}, env_file=dotenv)

    assert from_env.database_password is None
    assert from_dotenv.database_password is None


@pytest.mark.unit
def test_robinhood_account_never_uses_implicit_secret_path(tmp_path: Path) -> None:
    (tmp_path / "ROBINHOOD_READ_ACCOUNT_NUMBER").write_text(
        FAKE_ACCOUNT_REFERENCE,
        encoding="utf-8",
    )
    account = load_robinhood_read_account_number(environ={}, env_file=None)
    assert account is None
