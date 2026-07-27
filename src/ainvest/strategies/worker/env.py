"""Environment scrubbing for strategy worker child processes.

Workers must never inherit broker, OpenAI, Telegram, database, or Passkey
credentials from the host process environment.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Final

_ALLOWED_EXACT: Final[frozenset[str]] = frozenset(
    {
        "PATH",
        "PWD",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "TZ",
        "TERM",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONNOUSERSITE",
        "PYTHONSAFEPATH",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
        "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT",
        "UV_PYTHON",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
    }
)

_ALLOWED_PREFIXES: Final[tuple[str, ...]] = (
    "AINVEST_WORKER_",
    "LC_",
)

_DENIED_SUBSTRINGS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"SECRET",
        r"TOKEN",
        r"PASSWORD",
        r"PASSWD",
        r"PASSPHRASE",
        r"PASSKEY",
        r"API[_-]?KEY",
        r"ACCESS[_-]?KEY",
        r"PRIVATE[_-]?KEY",
        r"CREDENTIAL",
        r"AUTHORIZATION",
        r"BEARER",
        r"DATABASE_URL",
        r"DB_URL",
        r"DSN",
        r"OPENAI",
        r"TELEGRAM",
        r"ROBINHOOD",
        r"BROKER",
        r"WEBHOOK",
        r"AWS_",
        r"GCP_",
        r"AZURE_",
        r"SSH_",
    )
)

_SENSITIVE_VALUE_HINTS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"sk-[a-z0-9]{10,}",
        r"ghp_[a-zA-Z0-9]{20,}",
        r"xox[baprs]-",
        r"BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY",
    )
)


def is_denied_secret_key(key: str) -> bool:
    """Return True when a key matches an explicit secret/credential pattern."""
    return any(pattern.search(key) for pattern in _DENIED_SUBSTRINGS)


def is_sensitive_env_key(key: str) -> bool:
    """Return True when an environment key must not enter a worker.

    Keys matching explicit secret patterns are always removed. Other keys are
    kept only when they appear on the allowlist (exact or prefix).
    """
    if is_denied_secret_key(key):
        return True
    upper = key.upper()
    if upper in _ALLOWED_EXACT:
        return False
    return not any(upper.startswith(prefix) for prefix in _ALLOWED_PREFIXES)


def scrub_environ(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a minimal environment with secrets and credentials removed."""
    raw = dict(os.environ if source is None else source)
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if is_sensitive_env_key(key):
            continue
        if any(pattern.search(value) for pattern in _SENSITIVE_VALUE_HINTS):
            continue
        cleaned[key] = value
    cleaned.pop("PYTHONSTARTUP", None)
    return cleaned


def assert_no_secrets_in_environ(environ: Mapping[str, str] | None = None) -> None:
    """Fail closed when denied secret/credential keys are still present."""
    raw = os.environ if environ is None else environ
    leaked = sorted(key for key in raw if is_denied_secret_key(key))
    if leaked:
        raise RuntimeError(f"sensitive environment keys present in worker: {leaked!r}")


__all__ = [
    "assert_no_secrets_in_environ",
    "is_denied_secret_key",
    "is_sensitive_env_key",
    "scrub_environ",
]
