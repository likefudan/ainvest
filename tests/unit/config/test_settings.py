"""Unit tests for fail-closed configuration loading (P01-T4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from ainvest.config import (
    CONFIG_PRECEDENCE,
    AISettings,
    ApprovalMethod,
    ApprovalScope,
    ConfigError,
    Settings,
    TelegramBotSettings,
    TradingMode,
    WebAuthnSettings,
    load_settings,
    load_yaml_mapping,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_RISK = REPO_ROOT / "config" / "risk.example.yaml"
EXAMPLE_STRATEGIES = REPO_ROOT / "config" / "strategies.example.yaml"


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
def test_explicit_overrides_beat_environment(tmp_path: Path) -> None:
    """Explicit load_settings kwargs win over environment values."""
    settings = load_settings(
        environ={"TRADING_MODE": "research"},
        env_file=None,
        trading_mode=TradingMode.PAPER,
    )
    assert settings.trading_mode is TradingMode.PAPER


@pytest.mark.unit
def test_environment_beats_risk_yaml(tmp_path: Path) -> None:
    """Environment values override the lower-priority risk YAML source."""
    risk_yaml = tmp_path / "risk.yaml"
    risk_yaml.write_text(
        "schema_version: '1'\nnotes: yaml-value\n",
        encoding="utf-8",
    )

    settings = load_settings(
        environ={"RISK__NOTES": "environment-value"},
        env_file=None,
        risk_yaml=risk_yaml,
    )

    assert settings.risk.notes == "environment-value"


@pytest.mark.unit
def test_dotenv_beats_risk_yaml(tmp_path: Path) -> None:
    """Dotenv values override the lower-priority risk YAML source."""
    risk_yaml = tmp_path / "risk.yaml"
    risk_yaml.write_text(
        "schema_version: '1'\nnotes: yaml-value\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("RISK__NOTES=dotenv-value\n", encoding="utf-8")

    settings = load_settings(
        environ={},
        env_file=env_file,
        risk_yaml=risk_yaml,
    )

    assert settings.risk.notes == "dotenv-value"


@pytest.mark.unit
def test_environment_beats_dotenv(tmp_path: Path) -> None:
    """Process environment values override the optional dotenv file."""
    env_file = tmp_path / ".env"
    env_file.write_text("TRADING_MODE=paper\n", encoding="utf-8")

    settings = load_settings(
        environ={"TRADING_MODE": "research"},
        env_file=env_file,
    )

    assert settings.trading_mode is TradingMode.RESEARCH


@pytest.mark.unit
def test_explicit_override_beats_environment_and_risk_yaml(tmp_path: Path) -> None:
    """Explicit nested settings remain the highest-priority source."""
    risk_yaml = tmp_path / "risk.yaml"
    risk_yaml.write_text(
        "schema_version: '1'\nnotes: yaml-value\n",
        encoding="utf-8",
    )

    settings = load_settings(
        environ={"RISK__NOTES": "environment-value"},
        env_file=None,
        risk_yaml=risk_yaml,
        risk={"schema_version": "1", "notes": "explicit-value"},
    )

    assert settings.risk.notes == "explicit-value"


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
def test_telegram_rejects_username_and_non_int64() -> None:
    """Telegram allowlists accept only positive 64-bit numeric IDs."""
    with pytest.raises(ValidationError):
        TelegramBotSettings(allowed_user_ids=("notanid",))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        TelegramBotSettings(allowed_user_ids=(2**63,))
    with pytest.raises(ValidationError):
        TelegramBotSettings(allowed_chat_ids=(-100123,))


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
