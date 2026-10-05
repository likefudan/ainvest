"""Append-only, quota-bound research persistence behind an ORM-free boundary."""

import json
import sqlite3
from collections.abc import Callable

from pydantic import TypeAdapter
from sqlalchemy import Text, cast, func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ainvest.agents.research_archive import (
    MAX_ARCHIVE_BYTES,
    ResearchArchive,
    ResearchStorageQuota,
)
from ainvest.agents.research_builder import replay_research_archive
from ainvest.data.indicators import digest_bytes
from ainvest.db.errors import ConflictError, PersistenceError
from ainvest.db.models import ResearchPacketRow, ResearchRunRow
from ainvest.db.repositories import AuditRepository
from ainvest.schemas.common import StableId


def _audit_id(archive: ResearchArchive) -> str:
    return (
        "audit_"
        + digest_bytes((archive.payload.request.scope.run_id + archive.digest).encode()).split(":")[
            1
        ]
    )


def _logical_bytes(archive: ResearchArchive) -> int:
    packet = archive.payload.packet
    return len(archive.model_dump_json().encode()) + (
        len(packet.model_dump_json().encode()) if packet is not None else 0
    )


class ResearchRepository:
    """Only immutable domain archives leave this repository; no update/delete API.

    Logical limits cover archive plus duplicate indexed packet JSON, not physical
    database pages/WAL/backup/audit overhead. Retention remains a separate owner
    decision. Use only inside an active UnitOfWork with its write-boundary callback.
    """

    def __init__(
        self,
        session: Session,
        quota: ResearchStorageQuota,
        *,
        begin_write: Callable[[], None],
    ) -> None:
        self._session = session
        self._quota = ResearchStorageQuota.model_validate_json(quota.model_dump_json())
        self._begin_write = begin_write

    def _begin(self) -> None:
        connection = self._session.connection()
        dialect = connection.dialect.name
        if dialect == "sqlite":
            driver = connection.connection.driver_connection
            if not isinstance(driver, sqlite3.Connection):
                raise PersistenceError(
                    "unsupported SQLite driver", code="RESEARCH_DRIVER_UNSUPPORTED"
                )
            if not driver.in_transaction:
                self._begin_write()
        elif dialect == "postgresql":
            self._begin_write()
            # Serialize quota checks and append across cooperating research writers.
            self._session.execute(text("LOCK TABLE research_runs IN SHARE ROW EXCLUSIVE MODE"))
        else:
            raise PersistenceError(
                "unsupported research dialect", code="RESEARCH_DRIVER_UNSUPPORTED"
            )

    def get(self, run_id: str) -> ResearchArchive | None:
        """Bounded lookup, mirrored-row/audit/digest checks and offline replay."""
        TypeAdapter(StableId).validate_python(run_id)
        try:
            size = self._session.scalar(
                select(func.length(cast(ResearchRunRow.payload_json, Text))).where(
                    ResearchRunRow.run_id == run_id
                )
            )
            if size is None:
                return None
            # SQL-side preflight before decoding untrusted stored JSON. Account for
            # database escaping/whitespace; exact logical size is checked below.
            if size > min(MAX_ARCHIVE_BYTES, self._quota.max_record_bytes) * 4 + 4096:
                raise ValueError("stored research exceeds bounds")
            row = self._session.scalar(
                select(ResearchRunRow).where(ResearchRunRow.run_id == run_id)
            )
            if row is None:
                raise ValueError("research row disappeared")
            archive = ResearchArchive.model_validate_json(json.dumps(row.payload_json))
            if archive.model_dump(mode="json") != row.payload_json:
                raise ValueError("noncanonical research payload")
            request = archive.payload.request
            if (
                request.scope.run_id != run_id
                or row.symbol != request.scope.instrument.symbol
                or row.status != archive.payload.status.upper()
                or row.started_at != request.started_at
                or row.completed_at != request.completed_at
                or row.model_version != archive.payload.agent.record.model_id
                or row.prompt_version != archive.payload.agent.record.prompt_version
                or row.code_version != request.code_version
                or row.config_version != request.config_version
                or row.error_code != archive.payload.error_code
                or _logical_bytes(archive) > self._quota.max_record_bytes
            ):
                raise ValueError("research mirrored columns differ")
            packet_sizes = self._session.scalars(
                select(func.length(cast(ResearchPacketRow.payload_json, Text)))
                .where(ResearchPacketRow.run_id == run_id)
                .limit(2)
            ).all()
            if any(
                value > min(MAX_ARCHIVE_BYTES, self._quota.max_record_bytes) * 4 + 4096
                for value in packet_sizes
            ):
                raise ValueError("stored packet exceeds bounds")
            packets = self._session.scalars(
                select(ResearchPacketRow).where(ResearchPacketRow.run_id == run_id).limit(2)
            ).all()
            expected = archive.payload.packet
            if expected is None:
                if packets:
                    raise ValueError("failed research has a packet")
            elif (
                len(packets) != 1
                or packets[0].research_id != expected.research_id
                or packets[0].symbol != expected.symbol
                or packets[0].as_of != expected.as_of
                or packets[0].payload_json != expected.model_dump(mode="json")
                or packets[0].code_version != request.code_version
                or packets[0].config_version != request.config_version
            ):
                raise ValueError("research packet row differs")
            audit = AuditRepository(self._session).get(_audit_id(archive))
            if (
                audit is None
                or audit.output_digest != archive.digest
                or audit.input_digest != archive.payload.agent.record.input_digest
                or audit.subject_id != run_id
                or audit.event_type != "RESEARCH_ARCHIVED"
            ):
                raise ValueError("research audit checkpoint missing or inconsistent")
            replay_research_archive(archive)
            return archive
        except (ValueError, SQLAlchemyError):
            raise PersistenceError(
                "research integrity check failed", code="RESEARCH_INTEGRITY_FAILED"
            ) from None

    def append(self, archive: ResearchArchive) -> bool:
        """Atomically append run, packet and audit; return False for an exact replay."""
        try:
            archive = ResearchArchive.model_validate_json(archive.model_dump_json())
            replay_research_archive(archive)
            if _logical_bytes(archive) > self._quota.max_record_bytes:
                raise PersistenceError(
                    "research entry quota exceeded", code="RESEARCH_QUOTA_EXCEEDED"
                )
            self._begin()
            run_id = archive.payload.request.scope.run_id
            existing = self.get(run_id)
            if existing is not None:
                if existing != archive:
                    raise ConflictError("immutable research run conflict", code="RESEARCH_CONFLICT")
                return False
            count = self._session.scalar(select(func.count()).select_from(ResearchRunRow))
            if count is None or count >= self._quota.max_records:
                raise PersistenceError(
                    "research record quota exceeded", code="RESEARCH_QUOTA_EXCEEDED"
                )
            try:
                with self._session.begin_nested():
                    self._insert(archive)
            except IntegrityError:
                existing = self.get(run_id)
                if existing != archive:
                    raise ConflictError(
                        "immutable research conflict", code="RESEARCH_CONFLICT"
                    ) from None
                return False
            return True
        except (ValueError, SQLAlchemyError):
            raise PersistenceError(
                "research append failed", code="RESEARCH_APPEND_FAILED"
            ) from None

    def _insert(self, archive: ResearchArchive) -> None:
        # Lazy audit imports avoid the existing audit-package/UoW import cycle.
        from ainvest.audit.envelope import ActorType, AuditEventEnvelope
        from ainvest.audit.service import AuditService

        payload = archive.payload
        request, record = payload.request, payload.agent.record
        run_id = request.scope.run_id
        self._session.add(
            ResearchRunRow(
                run_id=run_id,
                symbol=request.scope.instrument.symbol,
                status=payload.status.upper(),
                started_at=request.started_at,
                completed_at=request.completed_at,
                model_version=record.model_id,
                prompt_version=record.prompt_version,
                code_version=request.code_version,
                config_version=request.config_version,
                error_code=payload.error_code,
                payload_json=archive.model_dump(mode="json"),
            )
        )
        self._session.flush()
        if packet := payload.packet:
            self._session.add(
                ResearchPacketRow(
                    research_id=packet.research_id,
                    run_id=run_id,
                    symbol=packet.symbol,
                    as_of=packet.as_of,
                    payload_json=packet.model_dump(mode="json"),
                    code_version=request.code_version,
                    config_version=request.config_version,
                )
            )
            self._session.flush()
        AuditService(AuditRepository(self._session)).append(
            AuditEventEnvelope(
                event_id=_audit_id(archive),
                event_type="RESEARCH_ARCHIVED",
                occurred_at=request.completed_at,
                actor_type=ActorType.SYSTEM,
                actor_id="ainvest.research",
                subject_type="research_run",
                subject_id=run_id,
                correlation_id="research_" + digest_bytes(run_id.encode()).split(":")[1],
                code_version=request.code_version,
                config_version=request.config_version,
                input_digest=record.input_digest,
                output_digest=archive.digest,
                after_state={"status": payload.status},
                error_code=payload.error_code,
                retry_count=max(0, record.attempts - 1),
                payload={
                    "model_id": record.model_id,
                    "prompt_version": record.prompt_version,
                    "requests": record.requests,
                    "input_tokens": record.input_tokens,
                    "output_tokens": record.output_tokens,
                    "usage_complete": record.usage_complete,
                },
            )
        )
