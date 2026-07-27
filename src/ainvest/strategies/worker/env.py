"""Environment scrubbing for strategy worker child processes.

Workers must never inherit broker, OpenAI, Telegram, database, or Passkey
credentials from the host process environment. Host ``HOME`` / temp paths are
also withheld so ``Path.home()`` cannot read ``~/.env`` or cloud credential
files; the runner binds those variables to the isolated worker workdir.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

# Exact keys allowed through scrubbing. HOME / PWD / TMP* are intentionally
# omitted: the host values leak secret paths and are rebound to the worker
# workdir by :func:`bind_worker_paths`.
_ALLOWED_EXACT: Final[frozenset[str]] = frozenset(
    {
        "PATH",
        "USER",
        "LOGNAME",
        "SHELL",
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

# Host path variables that must never be inherited; rebound to the workdir.
_HOST_PATH_KEYS: Final[frozenset[str]] = frozenset(
    {
        "HOME",
        "PWD",
        "TMPDIR",
        "TMP",
        "TEMP",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
    }
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


class SecretEnvironmentAccessError(PermissionError):
    """Raised when strategy code probes a denied credential environment key."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"strategy worker denied environment key access: {key}")


def is_denied_secret_key(key: str) -> bool:
    """Return True when a key matches an explicit secret/credential pattern."""
    return any(pattern.search(key) for pattern in _DENIED_SUBSTRINGS)


def is_sensitive_env_key(key: str) -> bool:
    """Return True when an environment key must not enter a worker.

    Keys matching explicit secret patterns are always removed. Host path keys
    (``HOME``, ``TMPDIR``, …) are removed so they can be rebound to the worker
    workdir. Other keys are kept only when they appear on the allowlist.
    """
    if is_denied_secret_key(key):
        return True
    upper = key.upper()
    if upper in _HOST_PATH_KEYS:
        return True
    if upper in _ALLOWED_EXACT:
        return False
    return not any(upper.startswith(prefix) for prefix in _ALLOWED_PREFIXES)


def scrub_environ(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a minimal environment with secrets and host home/temp paths removed."""
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


def bind_worker_paths(environ: dict[str, str], workdir: Path) -> None:
    """Point HOME / PWD / temp vars at the isolated worker workdir tree."""
    home = workdir / "home"
    tmp = workdir / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    root = str(workdir)
    environ["HOME"] = str(home)
    environ["PWD"] = root
    environ["TMPDIR"] = str(tmp)
    environ["TMP"] = str(tmp)
    environ["TEMP"] = str(tmp)


def assert_no_secrets_in_environ(environ: Mapping[str, str] | None = None) -> None:
    """Fail closed when denied secret/credential keys are still present."""
    raw = os.environ if environ is None else environ
    leaked = sorted(key for key in raw if is_denied_secret_key(key))
    if leaked:
        raise RuntimeError(f"sensitive environment keys present in worker: {leaked!r}")


_ORIGINAL_ENVIRON_GETITEM: Any | None = None


def install_secret_env_guard() -> None:
    """Raise :class:`SecretEnvironmentAccessError` on denied env-key lookups.

    Install only in a scrubbed worker child. Ordinary missing non-secret keys
    still raise ``KeyError``. Denied key names always fail closed as secret access.
    """
    global _ORIGINAL_ENVIRON_GETITEM
    environ_type = os.environ.__class__
    if getattr(environ_type, "_ainvest_secret_guard", False):
        return

    original_getitem = environ_type.__getitem__
    _ORIGINAL_ENVIRON_GETITEM = original_getitem

    def _guarded_getitem(self: Any, key: str) -> str:
        if is_denied_secret_key(str(key)):
            raise SecretEnvironmentAccessError(str(key))
        return original_getitem(self, key)

    environ_type.__getitem__ = _guarded_getitem  # type: ignore[method-assign]
    environ_type._ainvest_secret_guard = True  # type: ignore[attr-defined]


def uninstall_secret_env_guard() -> None:
    """Restore the original ``os.environ.__getitem__`` (tests / cleanup)."""
    global _ORIGINAL_ENVIRON_GETITEM
    environ_type = os.environ.__class__
    if not getattr(environ_type, "_ainvest_secret_guard", False):
        return
    if _ORIGINAL_ENVIRON_GETITEM is not None:
        environ_type.__getitem__ = _ORIGINAL_ENVIRON_GETITEM  # type: ignore[method-assign]
    with contextlib.suppress(AttributeError):
        delattr(environ_type, "_ainvest_secret_guard")
    _ORIGINAL_ENVIRON_GETITEM = None


__all__ = [
    "SecretEnvironmentAccessError",
    "assert_no_secrets_in_environ",
    "bind_worker_paths",
    "install_secret_env_guard",
    "is_denied_secret_key",
    "is_sensitive_env_key",
    "scrub_environ",
    "uninstall_secret_env_guard",
]
