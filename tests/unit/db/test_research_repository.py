"""Quota-bound append and audit are one transaction on synthetic SQLite."""

import pytest
from research_builder_fixtures import assembly_inputs
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ainvest.agents.research_archive import ResearchStorageQuota
from ainvest.agents.research_builder import build_research_archive
from ainvest.db.errors import ConflictError, PersistenceError
from ainvest.db.models import AuditEventRow, ResearchPacketRow, ResearchRunRow
from ainvest.db.uow import UnitOfWork


def quota(records: int = 2, record_bytes: int = 1_000_000) -> ResearchStorageQuota:
    return ResearchStorageQuota(max_records=records, max_record_bytes=record_bytes)


def test_repository_idempotent_domain_roundtrip_and_audit(
    session_factory: sessionmaker[Session],
) -> None:
    with assembly_inputs() as (request, result, tools):
        archive = build_research_archive(request, result, tools=tools)
    with UnitOfWork(session_factory) as uow:
        repo = uow.research_repository(quota())
        assert repo.append(archive) is True
        assert repo.append(archive) is False
        assert repo.get(request.scope.run_id) == archive
    with UnitOfWork(session_factory) as uow:
        assert uow.research_repository(quota()).get(request.scope.run_id) == archive
        assert uow.session is not None
        assert len(uow.session.scalars(select(AuditEventRow)).all()) == 1


def test_outer_rollback_removes_run_packet_and_audit(
    session_factory: sessionmaker[Session],
) -> None:
    with assembly_inputs() as (request, result, tools):
        archive = build_research_archive(request, result, tools=tools)
    with pytest.raises(RuntimeError), UnitOfWork(session_factory) as uow:
        uow.research_repository(quota()).append(archive)
        raise RuntimeError("synthetic rollback")
    with UnitOfWork(session_factory) as uow:
        assert uow.research_repository(quota()).get(request.scope.run_id) is None
        assert uow.session is not None
        assert not uow.session.scalars(select(AuditEventRow)).all()
        assert not uow.session.scalars(select(ResearchPacketRow)).all()


def test_size_and_record_quota_never_overwrite_or_delete(
    session_factory: sessionmaker[Session],
) -> None:
    with assembly_inputs() as (request, result, tools):
        archive = build_research_archive(request, result, tools=tools)
    with pytest.raises(PersistenceError), UnitOfWork(session_factory) as uow:
        uow.research_repository(quota(record_bytes=100)).append(archive)
    with UnitOfWork(session_factory) as uow:
        assert uow.research_repository(quota(records=1)).append(archive)
    with UnitOfWork(session_factory) as uow:
        repo = uow.research_repository(quota(records=1))
        assert repo.append(archive) is False
        assert repo.get(request.scope.run_id) == archive
        assert not hasattr(repo, "delete") and not hasattr(repo, "update")
    with assembly_inputs("research_second_002") as (second, result, tools):
        other = build_research_archive(second, result, tools=tools)
    with pytest.raises(PersistenceError), UnitOfWork(session_factory) as uow:
        uow.research_repository(quota(records=1)).append(other)
    with UnitOfWork(session_factory) as uow:
        assert uow.research_repository(quota(records=1)).get(second.scope.run_id) is None
        assert uow.research_repository(quota(records=1)).get(request.scope.run_id) == archive


def test_conflicting_same_run_and_tamper_are_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    with assembly_inputs() as (request, result, tools):
        archive = build_research_archive(request, result, tools=tools)
        conflict = build_research_archive(
            request.model_copy(update={"config_version": "different-config"}), result, tools=tools
        )
    with UnitOfWork(session_factory) as uow:
        uow.research_repository(quota()).append(archive)
    with pytest.raises(ConflictError), UnitOfWork(session_factory) as uow:
        uow.research_repository(quota()).append(conflict)
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        row = uow.session.scalar(select(ResearchRunRow))
        assert row is not None
        payload = dict(row.payload_json)
        payload["digest"] = "sha256:" + "0" * 64
        row.payload_json = payload
    with pytest.raises(PersistenceError), UnitOfWork(session_factory) as uow:
        uow.research_repository(quota()).get(request.scope.run_id)


@pytest.mark.parametrize("target", ["packet", "audit"])
def test_mirrored_packet_and_audit_checkpoint_tamper_rejected(
    session_factory: sessionmaker[Session],
    target: str,
) -> None:
    with assembly_inputs() as (request, result, tools):
        archive = build_research_archive(request, result, tools=tools)
    with UnitOfWork(session_factory) as uow:
        uow.research_repository(quota()).append(archive)
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        if target == "packet":
            packet = uow.session.scalar(select(ResearchPacketRow))
            assert packet is not None
            packet.config_version = "tampered-config"
        else:
            audit = uow.session.scalar(select(AuditEventRow))
            assert audit is not None
            audit.output_digest = "sha256:" + "0" * 64
    with pytest.raises(PersistenceError), UnitOfWork(session_factory) as uow:
        uow.research_repository(quota()).get(request.scope.run_id)


def test_failed_assembly_is_archived_without_packet(session_factory: sessionmaker[Session]) -> None:
    with assembly_inputs(include_context=False, age_seconds=121) as (request, result, tools):
        archive = build_research_archive(request, result, tools=tools)
    assert archive.payload.status == "error"
    with UnitOfWork(session_factory) as uow:
        assert uow.research_repository(quota()).append(archive)
    with UnitOfWork(session_factory) as uow:
        assert uow.research_repository(quota()).get(request.scope.run_id) == archive
        assert uow.session is not None
        assert not uow.session.scalars(select(ResearchPacketRow)).all()
