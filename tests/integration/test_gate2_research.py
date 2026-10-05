"""Gate 2 offline evidence is not real-model qualification or release approval."""

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from gate2_fixtures import Gate2Report, build_gate2_archive, run_gate2

from ainvest.agents.research_archive import ResearchStorageQuota
from ainvest.agents.research_builder import replay_research_archive
from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.uow import UnitOfWork


def test_research_to_paper_numeric_trace_replay_and_closed_owner_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("unexpected network")

    monkeypatch.setattr(socket, "create_connection", denied)
    first = run_gate2()
    second = run_gate2()
    assert first == second
    assert first.offline_checks_passed
    assert first.pending_terminal == "APPROVAL_PENDING"
    assert first.explicit_test_approval_terminal == "FILLED"
    assert first.test_ledger_conservation
    assert first.recorded_read_trading_eligible is False
    assert (
        not first.full_gate_accepted
        and not first.real_ai_eligible
        and not first.scheduled_paper_eligible
    )
    assert first.owner_action_required == "DEC009_PROJECT_BUDGET_SECRET_REFERENCE"
    assert first == Gate2Report.model_validate_json(first.model_dump_json())


@pytest.mark.parametrize("mode", ["stale", "timeout", "unsupported", "forged"])
def test_failed_research_withholds_packet_and_never_enters_paper(mode: str) -> None:
    archive, _, _ = build_gate2_archive(mode)
    assert archive.payload.status == "error"
    assert archive.payload.packet is None
    assert archive.payload.error_code is not None
    assert replay_research_archive(archive) is None
    assert "private_canary" not in archive.model_dump_json()


def test_temporary_quota_bound_storage_and_offline_replay(tmp_path: Path) -> None:
    archive, _, _ = build_gate2_archive()
    engine = create_db_engine("sqlite+pysqlite:///" + str(tmp_path / "gate2.sqlite3"))
    create_all_tables(engine)
    factory = create_session_factory(engine)
    quota = ResearchStorageQuota(max_records=1, max_record_bytes=1_000_000)
    try:
        with UnitOfWork(factory) as uow:
            repo = uow.research_repository(quota)
            assert repo.append(archive)
            assert not repo.append(archive)
        with UnitOfWork(factory) as uow:
            retained = uow.research_repository(quota).get(archive.payload.request.scope.run_id)
        assert retained == archive
        assert retained is not None and replay_research_archive(retained) == archive.payload.packet
    finally:
        engine.dispose()


def test_cli_prints_only_offline_qualification_and_no_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("OPENAI_API_KEY", "private_canary_not_a_credential")
    completed = subprocess.run(
        [sys.executable, "-B", str(root / "scripts/run_gate2_research.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "private_canary" not in completed.stdout + completed.stderr
    report = Gate2Report.model_validate(json.loads(completed.stdout.splitlines()[-1]))
    assert report.offline_checks_passed and not report.full_gate_accepted
