"""Offline security tests for P05-T11 account binding."""

from __future__ import annotations

import asyncio
import io
import os
from collections.abc import Coroutine
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Never

import pytest

import ainvest.orchestrator.robinhood_account_provisioning as provisioning_module
from ainvest.approval.telegram import TelegramEnvironment
from ainvest.approval.telegram_maintenance import TelegramMaintenanceLeasePolicy
from ainvest.db import TelegramUpdateRepository, create_all_tables, create_db_engine
from ainvest.db.session import create_session_factory
from ainvest.execution.robinhood import (
    GatewayReadError,
    GatewayReadErrorCode,
    GatewayReadResult,
)
from ainvest.orchestrator.robinhood_account_provisioning import (
    AccountProvisioningDependencies,
    AccountProvisioningFailure,
    AccountProvisioningRequest,
    _account_secret_from_result,
    build_parser,
    execute,
    main,
)

ACCOUNT = "SYNTHETIC-AGENTIC-ACCOUNT"


def _result(accounts: object) -> GatewayReadResult:
    return GatewayReadResult(
        capability="get_accounts",
        manifest_version="2026.08.22",
        manifest_digest="sha256:" + "1" * 64,
        schema_digest="sha256:" + "2" * 64,
        result_digest="sha256:" + "3" * 64,
        observed_at="2026-08-26T00:00:00Z",
        payload={"accounts": accounts},
        warnings=(),
    )


def _agentic(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "account_number": ACCOUNT,
        "agentic_allowed": True,
        "state": "active",
        "deactivated": False,
        "permanently_deactivated": False,
    }
    row.update(overrides)
    return row


@dataclass
class FakeClient:
    result: GatewayReadResult
    calls: int = 0

    async def read_accounts(self) -> GatewayReadResult:
        self.calls += 1
        return self.result


@dataclass
class FakeGateway:
    client: FakeClient


@dataclass
class FakeOpener:
    result: GatewayReadResult
    enters: int = 0
    exits: int = 0
    client: FakeClient = field(init=False)

    def __post_init__(self) -> None:
        self.client = FakeClient(self.result)

    def __call__(self) -> FakeOpener:
        return self

    async def __aenter__(self) -> FakeGateway:
        self.enters += 1
        return FakeGateway(self.client)

    async def __aexit__(self, *args: object) -> None:
        self.exits += 1


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "state.sqlite3"
    engine = create_db_engine(f"sqlite+pysqlite:///{path}")
    create_all_tables(engine)
    engine.dispose()
    return path


def _request(tmp_path: Path, command: str) -> AccountProvisioningRequest:
    env_file = tmp_path / ".env"
    env_file.write_text("REGULAR_TRADING_HOURS_ONLY=true\n", encoding="utf-8")
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    return AccountProvisioningRequest(
        command=command,
        environment=TelegramEnvironment.STAGING,
        env_file=env_file,
        secrets_dir=secrets_dir,
        database=None if command == "validate" else _database(tmp_path),
        confirm_poller_stopped=command != "validate",
    )


def _dependencies(opener: FakeOpener) -> AccountProvisioningDependencies:
    return AccountProvisioningDependencies(
        gateway_opener=opener,
        lease_policy=TelegramMaintenanceLeasePolicy(wait_seconds=0),
    )


def _install(request: AccountProvisioningRequest, value: str = ACCOUNT, mode: int = 0o600) -> Path:
    target = request.secrets_dir / "ROBINHOOD_READ_ACCOUNT_NUMBER"
    target.write_text(value + "\n", encoding="ascii")
    os.chmod(target, mode)
    return target


def test_parser_has_exact_commands_and_no_account_input(tmp_path: Path) -> None:
    parser = build_parser()
    base = [
        "--environment",
        "staging",
        "--env-file",
        str(tmp_path / ".env"),
        "--secrets-dir",
        str(tmp_path),
    ]
    assert parser.parse_args(["validate", *base]).command == "validate"
    for command in ("provision", "disable"):
        parsed = parser.parse_args(
            [command, *base, "--database", str(tmp_path / "db"), "--confirm-poller-stopped"]
        )
        assert parsed.command == command
    for forbidden in ("--account-number", "--capability", "--token", "--value"):
        with pytest.raises(AccountProvisioningFailure, match="invalid_cli_input"):
            parser.parse_args(["validate", *base, forbidden, ACCOUNT])
    with pytest.raises(AccountProvisioningFailure, match="invalid_cli_input"):
        parser.parse_args(["rotate", *base])


@pytest.mark.parametrize(
    ("accounts", "code"),
    [
        ([], "account_candidate_missing"),
        ([{"agentic_allowed": False}], "account_candidate_missing"),
        ([_agentic(), _agentic(account_number="OTHER")], "account_candidate_ambiguous"),
        ([_agentic(agentic_allowed="true")], "account_result_invalid"),
        ([_agentic(state="inactive")], "account_candidate_invalid"),
        ([_agentic(deactivated=True)], "account_candidate_invalid"),
        ([_agentic(permanently_deactivated=True)], "account_candidate_invalid"),
        ([_agentic(account_number="")], "account_candidate_invalid"),
        ([_agentic(account_number="A" * 129)], "account_candidate_invalid"),
        ([_agentic(account_number="has space")], "account_candidate_invalid"),
        ([_agentic(account_number="é")], "account_candidate_invalid"),
        ({"not": "an array"}, "account_result_invalid"),
        (["not-an-object"], "account_result_invalid"),
    ],
)
def test_candidate_selection_fails_closed(accounts: object, code: str) -> None:
    with pytest.raises(AccountProvisioningFailure, match=code) as caught:
        _account_secret_from_result(_result(accounts))
    rendered = repr(caught.value) + str(caught.value)
    assert ACCOUNT not in rendered
    assert "OTHER" not in rendered


def test_provision_uses_one_named_read_and_installs_exact_owner_file(tmp_path: Path) -> None:
    request = _request(tmp_path, "provision")
    opener = FakeOpener(_result([_agentic(), {"agentic_allowed": False}]))

    result = asyncio.run(execute(request, _dependencies(opener)))

    assert result.as_json() == '{"command":"provision","environment":"staging","status":"ok"}'
    assert opener.enters == opener.exits == opener.client.calls == 1
    target = request.secrets_dir / "ROBINHOOD_READ_ACCOUNT_NUMBER"
    assert target.read_bytes() == ACCOUNT.encode("ascii") + b"\n"
    assert stat_mode(target) == 0o600
    assert target.stat().st_uid == os.getuid()
    assert request.database is not None
    assert ACCOUNT.encode("ascii") not in request.database.read_bytes()


def test_existing_target_refuses_before_gateway_even_when_equal(tmp_path: Path) -> None:
    request = _request(tmp_path, "provision")
    target = _install(request)
    opener = FakeOpener(_result([_agentic()]))

    with pytest.raises(AccountProvisioningFailure, match="account_secret_exists"):
        asyncio.run(execute(request, _dependencies(opener)))

    assert opener.enters == opener.client.calls == 0
    assert target.read_bytes() == ACCOUNT.encode() + b"\n"


def test_atomic_install_race_never_overwrites_competing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(tmp_path, "provision")
    opener = FakeOpener(_result([_agentic()]))
    real_link = os.link

    def race(*args: Any, **kwargs: Any) -> None:
        target = request.secrets_dir / "ROBINHOOD_READ_ACCOUNT_NUMBER"
        target.write_text("COMPETING", encoding="ascii")
        os.chmod(target, 0o600)
        real_link(*args, **kwargs)

    monkeypatch.setattr("ainvest.orchestrator.robinhood_account_provisioning.os.link", race)
    with pytest.raises(AccountProvisioningFailure, match="account_secret_exists"):
        asyncio.run(execute(request, _dependencies(opener)))
    assert (request.secrets_dir / "ROBINHOOD_READ_ACCOUNT_NUMBER").read_text() == "COMPETING"
    assert ACCOUNT not in "".join(path.name for path in request.secrets_dir.iterdir())


def test_crash_after_link_leaves_safe_file_and_retry_refuses_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(tmp_path, "provision")
    opener = FakeOpener(_result([_agentic()]))
    real_fsync = os.fsync
    calls = 0

    def fail_directory_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError
        real_fsync(descriptor)

    monkeypatch.setattr(
        "ainvest.orchestrator.robinhood_account_provisioning.os.fsync",
        fail_directory_fsync,
    )
    with pytest.raises(AccountProvisioningFailure, match="atomic_write_failed"):
        asyncio.run(execute(request, _dependencies(opener)))
    target = request.secrets_dir / "ROBINHOOD_READ_ACCOUNT_NUMBER"
    assert target.read_bytes() == ACCOUNT.encode() + b"\n"
    assert stat_mode(target) == 0o600

    monkeypatch.setattr("ainvest.orchestrator.robinhood_account_provisioning.os.fsync", real_fsync)
    with pytest.raises(AccountProvisioningFailure, match="account_secret_exists"):
        asyncio.run(execute(request, _dependencies(opener)))
    assert opener.client.calls == 1


def test_validate_is_read_only_idempotent_and_detects_mismatch(tmp_path: Path) -> None:
    request = _request(tmp_path, "validate")
    target = _install(request)
    before = target.stat()
    opener = FakeOpener(_result([_agentic()]))

    first = asyncio.run(execute(request, _dependencies(opener)))
    second = asyncio.run(execute(request, _dependencies(opener)))

    assert first == second
    assert opener.client.calls == opener.enters == opener.exits == 2
    assert target.read_bytes() == ACCOUNT.encode() + b"\n"
    assert target.stat().st_ino == before.st_ino

    mismatch = FakeOpener(_result([_agentic(account_number="DIFFERENT")]))
    with pytest.raises(AccountProvisioningFailure, match="account_binding_mismatch") as caught:
        asyncio.run(execute(request, _dependencies(mismatch)))
    assert "DIFFERENT" not in repr(caught.value)


def test_disable_removes_only_exact_target_and_absence_is_idempotent(tmp_path: Path) -> None:
    request = _request(tmp_path, "disable")
    target = _install(request)
    unrelated = request.secrets_dir / "keep"
    unrelated.write_text("unchanged", encoding="utf-8")
    opener = FakeOpener(_result([_agentic()]))

    asyncio.run(execute(request, _dependencies(opener)))
    asyncio.run(execute(request, _dependencies(opener)))

    assert not target.exists()
    assert unrelated.read_text(encoding="utf-8") == "unchanged"
    assert opener.enters == opener.client.calls == 0


@pytest.mark.parametrize("mode", [0o400, 0o640, 0o644, 0o660])
def test_validate_and_disable_reject_wrong_target_mode(tmp_path: Path, mode: int) -> None:
    validate = _request(tmp_path, "validate")
    _install(validate, mode=mode)
    opener = FakeOpener(_result([_agentic()]))
    with pytest.raises(AccountProvisioningFailure, match="account_secret_invalid"):
        asyncio.run(execute(validate, _dependencies(opener)))
    assert opener.enters == 0

    disable = replace(
        validate,
        command="disable",
        database=_database(tmp_path),
        confirm_poller_stopped=True,
    )
    with pytest.raises(AccountProvisioningFailure, match="account_secret_target_unsafe"):
        asyncio.run(execute(disable, _dependencies(opener)))


def test_symlink_fifo_and_case_variant_never_bypass_exact_target(tmp_path: Path) -> None:
    for kind in ("symlink", "fifo", "case"):
        case_root = tmp_path / kind
        case_root.mkdir()
        request = _request(case_root, "provision")
        target = request.secrets_dir / "ROBINHOOD_READ_ACCOUNT_NUMBER"
        if kind == "symlink":
            backing = case_root / "backing"
            backing.write_text(ACCOUNT, encoding="ascii")
            target.symlink_to(backing)
        elif kind == "fifo":
            os.mkfifo(target, 0o600)
        else:
            (request.secrets_dir / "robinhood_read_account_number").write_text(
                ACCOUNT, encoding="ascii"
            )
        opener = FakeOpener(_result([_agentic()]))
        with pytest.raises(AccountProvisioningFailure):
            asyncio.run(execute(request, _dependencies(opener)))
        assert opener.enters == 0


@pytest.mark.parametrize(
    "key",
    [
        "ROBINHOOD_READ_ACCOUNT_NUMBER",
        "robinhood_read_account_number",
        "RobinHood_Read_Account_Number",
    ],
)
def test_environment_and_dotenv_value_routes_fail_before_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    request = _request(tmp_path, "provision")
    opener = FakeOpener(_result([_agentic()]))
    monkeypatch.setenv(key, ACCOUNT)
    with pytest.raises(AccountProvisioningFailure, match="account_configuration_invalid"):
        asyncio.run(execute(request, _dependencies(opener)))
    monkeypatch.delenv(key)
    request.env_file.write_text(f"{key}={ACCOUNT}\n", encoding="utf-8")
    with pytest.raises(AccountProvisioningFailure, match="account_configuration_invalid"):
        asyncio.run(execute(request, _dependencies(opener)))
    assert opener.enters == 0


def test_missing_acknowledgement_and_busy_lease_write_nothing(tmp_path: Path) -> None:
    request = _request(tmp_path, "provision")
    opener = FakeOpener(_result([_agentic()]))
    with pytest.raises(AccountProvisioningFailure, match="acknowledgement"):
        asyncio.run(execute(replace(request, confirm_poller_stopped=False), _dependencies(opener)))
    assert list(request.secrets_dir.iterdir()) == []
    assert opener.enters == 0


def test_main_invalid_input_has_fixed_json_and_zero_secret_leak(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        ["validate", "--account-number", ACCOUNT, "--env-file", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )
    assert code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == '{"code":"invalid_cli_input","status":"error"}\n'
    assert ACCOUNT not in stderr.getvalue()


def test_main_keyboard_interrupt_has_fixed_json_and_zero_context_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    env_file = tmp_path / "SENSITIVE-ENV-PATH"
    secrets_dir = tmp_path / "SENSITIVE-SECRETS-PATH"

    def interrupt_run(coroutine: Coroutine[Any, Any, Any]) -> Never:
        coroutine.close()
        raise KeyboardInterrupt(f"{ACCOUNT}:{env_file}:{secrets_dir}")

    monkeypatch.setattr(asyncio, "run", interrupt_run)
    code = main(
        [
            "validate",
            "--environment",
            "staging",
            "--env-file",
            str(env_file),
            "--secrets-dir",
            str(secrets_dir),
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 130
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == '{"code":"operation_cancelled","status":"error"}\n'
    rendered = stdout.getvalue() + stderr.getvalue()
    assert ACCOUNT not in rendered
    assert str(env_file) not in rendered
    assert str(secrets_dir) not in rendered


def test_main_preserves_system_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    def exit_run(coroutine: Coroutine[Any, Any, Any]) -> Never:
        coroutine.close()
        raise SystemExit(23)

    monkeypatch.setattr(asyncio, "run", exit_run)
    with pytest.raises(SystemExit, match="23"):
        main(
            [
                "validate",
                "--environment",
                "staging",
                "--env-file",
                str(tmp_path / ".env"),
                "--secrets-dir",
                str(tmp_path / "secrets"),
            ],
            stdout=stdout,
            stderr=stderr,
        )
    assert stdout.getvalue() == stderr.getvalue() == ""


@pytest.mark.parametrize("failure", ["cancelled", "provider"])
def test_provision_failure_closes_gateway_releases_lease_and_writes_no_file(
    tmp_path: Path, failure: str
) -> None:
    request = _request(tmp_path, "provision")

    @dataclass
    class LifecycleClient:
        started: asyncio.Event

        async def read_accounts(self) -> GatewayReadResult:
            self.started.set()
            if failure == "provider":
                raise GatewayReadError(GatewayReadErrorCode.PROVIDER_UNAVAILABLE)
            await asyncio.Event().wait()
            raise AssertionError

    @dataclass
    class LifecycleGateway:
        client: LifecycleClient

    @dataclass
    class LifecycleOpener:
        client: LifecycleClient
        enters: int = 0
        exits: int = 0

        def __call__(self) -> LifecycleOpener:
            return self

        async def __aenter__(self) -> LifecycleGateway:
            self.enters += 1
            return LifecycleGateway(self.client)

        async def __aexit__(self, *args: object) -> None:
            self.exits += 1

    async def run() -> LifecycleOpener:
        started = asyncio.Event()
        opener = LifecycleOpener(LifecycleClient(started))
        task = asyncio.create_task(execute(request, _dependencies(opener)))  # type: ignore[arg-type]
        await started.wait()
        if failure == "cancelled":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(AccountProvisioningFailure, match="gateway_failed"):
                await task
        return opener

    opener = asyncio.run(run())
    assert opener.enters == opener.exits == 1
    assert list(request.secrets_dir.iterdir()) == []
    assert request.database is not None
    engine = create_db_engine(f"sqlite+pysqlite:///{request.database}")
    factory = create_session_factory(engine)
    with factory() as session:
        state = TelegramUpdateRepository(session).get_state("staging")
        assert state is not None
        assert state.lease_owner is None
    engine.dispose()


def test_gateway_timeout_closes_once_and_returns_only_sanitized_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(tmp_path, "validate")
    _install(request)

    class SlowClient:
        async def read_accounts(self) -> GatewayReadResult:
            await asyncio.sleep(60)
            raise AssertionError

    @dataclass
    class SlowOpener:
        exits: int = 0

        def __call__(self) -> SlowOpener:
            return self

        async def __aenter__(self) -> FakeGateway:
            return FakeGateway(SlowClient())  # type: ignore[arg-type]

        async def __aexit__(self, *args: object) -> None:
            self.exits += 1

    opener = SlowOpener()
    monkeypatch.setattr(provisioning_module, "_GATEWAY_DEADLINE_SECONDS", 0.001)
    dependencies = AccountProvisioningDependencies(gateway_opener=opener)
    with pytest.raises(AccountProvisioningFailure, match="gateway_timeout") as caught:
        asyncio.run(execute(request, dependencies))
    assert opener.exits == 1
    assert ACCOUNT not in repr(caught.value)


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777
