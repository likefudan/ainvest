"""Offline build/store/reload and cooperating concurrent quota writers."""

import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from research_builder_fixtures import assembly_inputs

from ainvest.agents.research_archive import ResearchArchive, ResearchStorageQuota
from ainvest.agents.research_builder import build_research_archive, replay_research_archive
from ainvest.db.errors import PersistenceError
from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.uow import UnitOfWork


def test_offline_archive_survives_new_context_without_model_or_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("provider network forbidden")

    monkeypatch.setattr(socket, "create_connection", denied)
    with assembly_inputs() as (request, result, tools):
        archive = build_research_archive(request, result, tools=tools)
    engine = create_db_engine("sqlite+pysqlite:///" + str(tmp_path / "research.sqlite3"))
    create_all_tables(engine)
    factory = create_session_factory(engine)
    capacity = ResearchStorageQuota(max_records=2, max_record_bytes=1_000_000)
    try:
        with UnitOfWork(factory) as uow:
            assert uow.research_repository(capacity).append(archive)
        with UnitOfWork(factory) as uow:
            saved = uow.research_repository(capacity).get(request.scope.run_id)
            assert saved == archive
        assert saved is not None
        assert replay_research_archive(saved) == archive.payload.packet
    finally:
        engine.dispose()


def test_concurrent_capacity_one_accepts_exactly_one_different_run(tmp_path: Path) -> None:
    archives: list[ResearchArchive] = []
    for run_id in ("research_race_001", "research_race_002"):
        with assembly_inputs(run_id) as (request, result, tools):
            archives.append(build_research_archive(request, result, tools=tools))
    engine = create_db_engine("sqlite+pysqlite:///" + str(tmp_path / "race.sqlite3"))
    create_all_tables(engine)
    factory = create_session_factory(engine)
    capacity = ResearchStorageQuota(max_records=1, max_record_bytes=1_000_000)

    def append(archive: ResearchArchive) -> str:
        try:
            with UnitOfWork(factory) as uow:
                uow.research_repository(capacity).append(archive)
            return "stored"
        except PersistenceError as error:
            return error.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = tuple(pool.map(append, archives))
        assert sorted(outcomes) == ["RESEARCH_QUOTA_EXCEEDED", "stored"]
        with UnitOfWork(factory) as uow:
            repo = uow.research_repository(capacity)
            stored = [repo.get(item.payload.request.scope.run_id) for item in archives]
            assert sum(item is not None for item in stored) == 1
    finally:
        engine.dispose()
