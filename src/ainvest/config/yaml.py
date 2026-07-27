"""Safe YAML loading and secret-redaction helpers for configuration."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import ValidationError

from ainvest.config.errors import ConfigError

_EXECUTABLE_YAML_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:\beval\s*\(|\blambda\b|\b__import__\b|\bexec\s*\()",
    re.IGNORECASE,
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
