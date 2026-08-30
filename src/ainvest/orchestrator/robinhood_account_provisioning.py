"""Secure local provisioning for the Telegram READ_BROKER account binding.

This utility discovers one account through the pinned named ``read_accounts``
projection and stores only that account reference in an owner-only file.  It
has no generic gateway invocation, mutation, trading, approval, or Telegram
query capability.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import secrets
import stat
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Never, Protocol, TextIO, cast

from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from ainvest.approval.telegram import TelegramEnvironment
from ainvest.approval.telegram_maintenance import (
    TelegramMaintenanceLeaseError,
    TelegramMaintenanceLeasePolicy,
    TelegramPollingMaintenanceLease,
)
from ainvest.config import (
    RobinhoodAccountSecretInvalid,
    load_robinhood_read_account_number,
    reject_robinhood_read_account_value_sources,
)
from ainvest.config.settings import ROBINHOOD_READ_ACCOUNT_FILENAME
from ainvest.db import create_db_engine
from ainvest.execution.robinhood import GatewayReadError, GatewayReadResult, open_read_gateway

_GATEWAY_DEADLINE_SECONDS: Final[float] = 20.0
_MAX_ACCOUNTS: Final[int] = 64
_ACCOUNT_PATTERN_MIN: Final[int] = 0x21
_ACCOUNT_PATTERN_MAX: Final[int] = 0x7E


class AccountProvisioningFailure(Exception):
    """Stable value-free operator failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"AccountProvisioningFailure(code={self.code!r})"


class ReadClientPort(Protocol):
    async def read_accounts(self) -> GatewayReadResult: ...


class OpenedReadGateway(Protocol):
    @property
    def client(self) -> ReadClientPort: ...


GatewayOpener = Callable[[], AbstractAsyncContextManager[OpenedReadGateway]]


def _default_gateway_opener() -> AbstractAsyncContextManager[OpenedReadGateway]:
    return cast(AbstractAsyncContextManager[OpenedReadGateway], open_read_gateway())


@dataclass(frozen=True, slots=True)
class AccountProvisioningRequest:
    command: str
    environment: TelegramEnvironment
    env_file: Path
    secrets_dir: Path
    database: Path | None = None
    confirm_poller_stopped: bool = False


@dataclass(frozen=True, slots=True)
class AccountProvisioningResult:
    command: str
    environment: TelegramEnvironment

    def as_json(self) -> str:
        return json.dumps(
            {
                "command": self.command,
                "environment": self.environment.value,
                "status": "ok",
            },
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class AccountProvisioningDependencies:
    gateway_opener: GatewayOpener = _default_gateway_opener
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    lease_policy: TelegramMaintenanceLeasePolicy = field(
        default_factory=TelegramMaintenanceLeasePolicy
    )


def _database_factory(path: Path) -> sessionmaker[Session]:
    try:
        engine = create_db_engine(f"sqlite+pysqlite:///{path.resolve()}")
        return sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    except Exception:
        raise AccountProvisioningFailure("maintenance_database_failed") from None


def _account_secret_from_result(result: GatewayReadResult) -> SecretStr:
    if result.capability != "get_accounts":
        raise AccountProvisioningFailure("account_result_invalid")
    payload = result.payload
    if not isinstance(payload, Mapping) or set(payload) != {"data"}:
        raise AccountProvisioningFailure("account_result_invalid")
    data = payload["data"]
    if not isinstance(data, Mapping) or set(data) != {"accounts"}:
        raise AccountProvisioningFailure("account_result_invalid")
    accounts = data.get("accounts")
    if not isinstance(accounts, list) or len(accounts) > _MAX_ACCOUNTS:
        raise AccountProvisioningFailure("account_result_invalid")

    candidate: SecretStr | None = None
    for row in accounts:
        if not isinstance(row, Mapping):
            raise AccountProvisioningFailure("account_result_invalid")
        allowed = row.get("agentic_allowed")
        if not isinstance(allowed, bool):
            raise AccountProvisioningFailure("account_result_invalid")
        if not allowed:
            continue
        if candidate is not None:
            raise AccountProvisioningFailure("account_candidate_ambiguous")
        if (
            row.get("state") != "active"
            or row.get("deactivated") is not False
            or row.get("permanently_deactivated") is not False
        ):
            raise AccountProvisioningFailure("account_candidate_invalid")
        value = row.get("account_number")
        if not _valid_account_value(value):
            raise AccountProvisioningFailure("account_candidate_invalid")
        candidate = SecretStr(cast(str, value))
    if candidate is None:
        raise AccountProvisioningFailure("account_candidate_missing")
    return candidate


def _valid_account_value(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        return False
    return value.isascii() and all(
        _ACCOUNT_PATTERN_MIN <= ord(character) <= _ACCOUNT_PATTERN_MAX for character in value
    )


async def _discover_account(dependencies: AccountProvisioningDependencies) -> SecretStr:
    try:
        async with asyncio.timeout(_GATEWAY_DEADLINE_SECONDS):
            async with dependencies.gateway_opener() as gateway:
                return _account_secret_from_result(await gateway.client.read_accounts())
    except AccountProvisioningFailure:
        raise
    except TimeoutError:
        raise AccountProvisioningFailure("gateway_timeout") from None
    except GatewayReadError:
        raise AccountProvisioningFailure("gateway_failed") from None
    except asyncio.CancelledError:
        raise
    except Exception:
        raise AccountProvisioningFailure("gateway_failed") from None


def _open_secret_directory(path: Path) -> int:
    descriptor = -1
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise AccountProvisioningFailure("secrets_directory_invalid")
        no_follow = os.O_NOFOLLOW
        directory_only = os.O_DIRECTORY
        descriptor = os.open(path, os.O_RDONLY | no_follow | directory_only)
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or opened.st_uid != os.getuid()
        ):
            raise AccountProvisioningFailure("secrets_directory_invalid")
        return descriptor
    except AccountProvisioningFailure:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except (AttributeError, OSError):
        if descriptor >= 0:
            os.close(descriptor)
        raise AccountProvisioningFailure("secrets_directory_invalid") from None


def _verify_secret_directory(descriptor: int, path: Path) -> None:
    try:
        current = path.lstat()
        opened = os.fstat(descriptor)
    except OSError:
        raise AccountProvisioningFailure("secrets_directory_changed") from None
    if (
        stat.S_ISLNK(current.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
        or opened.st_uid != os.getuid()
    ):
        raise AccountProvisioningFailure("secrets_directory_changed")


def _matching_account_entries(directory_descriptor: int) -> tuple[str, ...]:
    try:
        return tuple(
            entry
            for entry in os.listdir(directory_descriptor)
            if entry.casefold() == ROBINHOOD_READ_ACCOUNT_FILENAME.casefold()
        )
    except OSError:
        raise AccountProvisioningFailure("account_secret_target_unsafe") from None


def _require_target_absent(directory_descriptor: int) -> None:
    entries = _matching_account_entries(directory_descriptor)
    if not entries:
        return
    if entries == (ROBINHOOD_READ_ACCOUNT_FILENAME,):
        raise AccountProvisioningFailure("account_secret_exists")
    raise AccountProvisioningFailure("account_secret_target_unsafe")


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError
        view = view[written:]


def _atomic_install(directory_descriptor: int, secret: SecretStr) -> None:
    temporary = f".{ROBINHOOD_READ_ACCOUNT_FILENAME}.{secrets.token_hex(16)}"
    descriptor = -1
    temporary_present = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600, dir_fd=directory_descriptor)
        temporary_present = True
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, secret.get_secret_value().encode("ascii") + b"\n")
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise OSError
        os.close(descriptor)
        descriptor = -1
        os.link(
            temporary,
            ROBINHOOD_READ_ACCOUNT_FILENAME,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        os.unlink(temporary, dir_fd=directory_descriptor)
        temporary_present = False
        os.fsync(directory_descriptor)
    except FileExistsError:
        raise AccountProvisioningFailure("account_secret_exists") from None
    except (AttributeError, OSError, UnicodeError):
        raise AccountProvisioningFailure("atomic_write_failed") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_present:
            # The primary error remains value-free; a later provision still
            # refuses the final target and never overwrites it.
            with suppress(OSError):
                os.unlink(temporary, dir_fd=directory_descriptor)


def _open_valid_target(directory_descriptor: int) -> tuple[int, os.stat_result] | None:
    entries = _matching_account_entries(directory_descriptor)
    if not entries:
        return None
    if entries != (ROBINHOOD_READ_ACCOUNT_FILENAME,):
        raise AccountProvisioningFailure("account_secret_target_unsafe")
    descriptor = -1
    try:
        descriptor = os.open(
            ROBINHOOD_READ_ACCOUNT_FILENAME,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_descriptor,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
        ):
            raise AccountProvisioningFailure("account_secret_target_unsafe")
        return descriptor, metadata
    except AccountProvisioningFailure:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except (AttributeError, OSError):
        if descriptor >= 0:
            os.close(descriptor)
        raise AccountProvisioningFailure("account_secret_target_unsafe") from None


def _unlink_same_target(
    directory_descriptor: int,
    descriptor: int,
    opened: os.stat_result,
) -> None:
    del descriptor
    try:
        current = os.stat(
            ROBINHOOD_READ_ACCOUNT_FILENAME,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if current.st_dev != opened.st_dev or current.st_ino != opened.st_ino:
            raise AccountProvisioningFailure("account_secret_changed")
        os.unlink(ROBINHOOD_READ_ACCOUNT_FILENAME, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
    except AccountProvisioningFailure:
        raise
    except OSError:
        raise AccountProvisioningFailure("account_secret_changed") from None


def _reject_old_value_sources(request: AccountProvisioningRequest) -> None:
    try:
        reject_robinhood_read_account_value_sources(
            env_file=request.env_file,
            environ=os.environ,
        )
    except RobinhoodAccountSecretInvalid:
        raise AccountProvisioningFailure("account_configuration_invalid") from None


def _load_installed_secret(request: AccountProvisioningRequest) -> SecretStr:
    try:
        secret = load_robinhood_read_account_number(
            env_file=request.env_file,
            secrets_dir=request.secrets_dir,
            environ=os.environ,
        )
    except RobinhoodAccountSecretInvalid:
        raise AccountProvisioningFailure("account_secret_invalid") from None
    if secret is None:
        raise AccountProvisioningFailure("account_secret_missing")
    return secret


def _same_secret(left: SecretStr, right: SecretStr) -> bool:
    return hmac.compare_digest(left.get_secret_value(), right.get_secret_value())


async def execute(
    request: AccountProvisioningRequest,
    dependencies: AccountProvisioningDependencies,
) -> AccountProvisioningResult:
    """Execute one file-only account-binding command."""

    _reject_old_value_sources(request)
    if request.command == "validate":
        installed = _load_installed_secret(request)
        discovered = await _discover_account(dependencies)
        if not _same_secret(installed, discovered):
            raise AccountProvisioningFailure("account_binding_mismatch")
        return AccountProvisioningResult(request.command, request.environment)
    if request.command not in {"provision", "disable"}:
        raise AccountProvisioningFailure("invalid_command")
    if not request.confirm_poller_stopped:
        raise AccountProvisioningFailure("poller_stop_acknowledgement_required")
    if request.database is None:
        raise AccountProvisioningFailure("database_required")

    directory_descriptor = _open_secret_directory(request.secrets_dir)
    try:
        if request.command == "provision":
            _require_target_absent(directory_descriptor)
        factory = _database_factory(request.database)
        try:
            async with TelegramPollingMaintenanceLease(
                factory,
                request.environment,
                clock=dependencies.clock,
                sleep=dependencies.sleep,
                policy=dependencies.lease_policy,
                owner_prefix="account",
            ) as lease:
                if request.command == "provision":
                    discovered = await _discover_account(dependencies)
                    await lease.verify_before_write()
                    _verify_secret_directory(directory_descriptor, request.secrets_dir)
                    _require_target_absent(directory_descriptor)
                    _atomic_install(directory_descriptor, discovered)
                else:
                    opened = _open_valid_target(directory_descriptor)
                    if opened is not None:
                        descriptor, metadata = opened
                        try:
                            await lease.verify_before_write()
                            _verify_secret_directory(directory_descriptor, request.secrets_dir)
                            _unlink_same_target(
                                directory_descriptor,
                                descriptor,
                                metadata,
                            )
                        finally:
                            os.close(descriptor)
        except TelegramMaintenanceLeaseError as exc:
            raise AccountProvisioningFailure(exc.code) from None
    finally:
        os.close(directory_descriptor)
    return AccountProvisioningResult(request.command, request.environment)


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        raise AccountProvisioningFailure("invalid_cli_input")


def build_parser() -> argparse.ArgumentParser:
    parser = _SanitizedArgumentParser(prog="ainvest-robinhood-account", add_help=False)
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_SanitizedArgumentParser,
    )
    for command in ("provision", "validate", "disable"):
        command_parser = subparsers.add_parser(command, add_help=False)
        command_parser.add_argument(
            "--environment",
            required=True,
            choices=tuple(item.value for item in TelegramEnvironment),
        )
        command_parser.add_argument("--env-file", type=Path, required=True)
        command_parser.add_argument("--secrets-dir", type=Path, required=True)
        if command != "validate":
            command_parser.add_argument("--database", type=Path, required=True)
            command_parser.add_argument("--confirm-poller-stopped", action="store_true")
    return parser


def _request_from_namespace(namespace: argparse.Namespace) -> AccountProvisioningRequest:
    return AccountProvisioningRequest(
        command=namespace.command,
        environment=TelegramEnvironment(namespace.environment),
        env_file=namespace.env_file,
        secrets_dir=namespace.secrets_dir,
        database=getattr(namespace, "database", None),
        confirm_poller_stopped=getattr(namespace, "confirm_poller_stopped", False),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = build_parser()
    try:
        request = _request_from_namespace(parser.parse_args(argv))
        result = asyncio.run(execute(request, AccountProvisioningDependencies()))
    except AccountProvisioningFailure as exc:
        stderr.write(
            json.dumps(
                {"code": exc.code, "status": "error"},
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        return 2 if exc.code == "invalid_cli_input" else 1
    except (asyncio.CancelledError, KeyboardInterrupt):
        stderr.write('{"code":"operation_cancelled","status":"error"}\n')
        return 130
    except Exception:
        stderr.write('{"code":"internal_error","status":"error"}\n')
        return 1
    stdout.write(result.as_json() + "\n")
    return 0


__all__ = [
    "AccountProvisioningDependencies",
    "AccountProvisioningFailure",
    "AccountProvisioningRequest",
    "AccountProvisioningResult",
    "GatewayOpener",
    "OpenedReadGateway",
    "ReadClientPort",
    "build_parser",
    "execute",
    "main",
]
