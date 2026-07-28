"""Minimal repositories for proposals, approvals, broker orders, and audit.

Business code must use these repositories (and :class:`~ainvest.db.uow.UnitOfWork`)
rather than touching ORM sessions directly. Idempotent inserts catch
:class:`~sqlalchemy.exc.IntegrityError` and re-read by unique key — they never
parse database error text.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ainvest.db.errors import (
    ConcurrentModificationError,
    NotFoundError,
    PersistenceError,
)
from ainvest.db.models import (
    ApprovalChallengeRow,
    ApprovalEventRow,
    AuditEventRow,
    BrokerFillRow,
    BrokerOrderRow,
    OrderProposalRow,
    RiskDecisionRow,
)


def _insert_idempotent[T](
    session: Session,
    row: T,
    *,
    lookup: Callable[[], T | None],
    conflict_message: str,
) -> tuple[T, bool]:
    """Insert using a savepoint; on conflict, read and return the existing row.

    Returns ``(row, created)`` where ``created`` is False on idempotent hit.
    Never parses database error text — only the IntegrityError type is used.
    """
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
        return row, True
    except IntegrityError:
        existing = lookup()
        if existing is None:
            raise PersistenceError(conflict_message) from None
        return existing, False


class RiskDecisionRepository:
    """Persistence helpers for immutable risk decisions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, risk_decision_id: str) -> RiskDecisionRow | None:
        return self._session.scalar(
            select(RiskDecisionRow).where(RiskDecisionRow.risk_decision_id == risk_decision_id)
        )

    def add_fields(self, fields: dict[str, Any]) -> RiskDecisionRow:
        row = RiskDecisionRow(**fields)
        self._session.add(row)
        return row


class ProposalRepository:
    """Persistence helpers for order proposals."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_proposal_id(self, proposal_id: str) -> OrderProposalRow | None:
        return self._session.scalar(
            select(OrderProposalRow).where(OrderProposalRow.proposal_id == proposal_id)
        )

    def get_by_idempotency_key(self, idempotency_key: str) -> OrderProposalRow | None:
        return self._session.scalar(
            select(OrderProposalRow).where(OrderProposalRow.idempotency_key == idempotency_key)
        )

    def add(self, row: OrderProposalRow) -> OrderProposalRow:
        self._session.add(row)
        return row

    def add_fields(self, fields: dict[str, Any]) -> OrderProposalRow:
        """Construct and persist a proposal without leaking ORM models."""
        return self.add(OrderProposalRow(**fields))

    def create_idempotent(
        self,
        row: OrderProposalRow,
        *,
        find_existing: Callable[[], OrderProposalRow | None],
    ) -> tuple[OrderProposalRow, bool]:
        """Insert using a savepoint; on conflict, read and return the existing row.

        Returns ``(row, created)`` where ``created`` is False on idempotent hit.
        Never parses database error text — only the IntegrityError type is used.
        """
        return _insert_idempotent(
            self._session,
            row,
            lookup=find_existing,
            conflict_message="proposal conflict without existing row",
        )

    def update_status_if_version(
        self,
        proposal_id: str,
        *,
        expected_version: int,
        new_status: str,
        extra_values: dict[str, Any] | None = None,
    ) -> OrderProposalRow:
        """Conditionally update status when ``version`` matches; bump version."""
        values: dict[str, Any] = {
            "status": new_status,
            "version": expected_version + 1,
        }
        if extra_values:
            values.update(extra_values)
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(OrderProposalRow)
                .where(
                    OrderProposalRow.proposal_id == proposal_id,
                    OrderProposalRow.version == expected_version,
                )
                .values(**values)
            ),
        )

        if result.rowcount != 1:
            raise ConcurrentModificationError(
                f"proposal {proposal_id} version {expected_version} lost race"
            )
        self._session.flush()
        row = self.get_by_proposal_id(proposal_id)
        if row is None:
            raise NotFoundError(f"proposal {proposal_id} missing after update")
        return row


class ApprovalRepository:
    """Persistence helpers for approval challenges and events."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_challenge(self, challenge_id: str) -> ApprovalChallengeRow | None:
        return self._session.scalar(
            select(ApprovalChallengeRow).where(ApprovalChallengeRow.challenge_id == challenge_id)
        )

    def get_challenge_by_token_hash(self, token_hash: str) -> ApprovalChallengeRow | None:
        return self._session.scalar(
            select(ApprovalChallengeRow).where(ApprovalChallengeRow.token_hash == token_hash)
        )

    def list_challenges_for_proposal(self, proposal_id: str) -> Sequence[ApprovalChallengeRow]:
        return self._session.scalars(
            select(ApprovalChallengeRow)
            .where(ApprovalChallengeRow.proposal_id == proposal_id)
            .order_by(ApprovalChallengeRow.challenge_created_at.asc())
        ).all()

    def add_challenge(self, row: ApprovalChallengeRow) -> ApprovalChallengeRow:
        self._session.add(row)
        return row

    def add_challenge_fields(self, fields: dict[str, Any]) -> ApprovalChallengeRow:
        """Construct and persist a challenge without leaking ORM models."""
        return self.add_challenge(ApprovalChallengeRow(**fields))

    def create_challenge_idempotent(
        self,
        row: ApprovalChallengeRow,
        *,
        find_existing: Callable[[], ApprovalChallengeRow | None],
    ) -> tuple[ApprovalChallengeRow, bool]:
        return _insert_idempotent(
            self._session,
            row,
            lookup=find_existing,
            conflict_message="challenge conflict without existing row",
        )

    def consume_challenge_once(
        self,
        challenge_id: str,
        *,
        expected_version: int,
        expected_status: str = "PENDING",
        new_status: str = "CONSUMED",
        extra_values: dict[str, Any] | None = None,
    ) -> ApprovalChallengeRow:
        """Atomically transition a challenge when status+version match.

        Concurrent callers: only one succeeds. Losers raise
        :class:`~ainvest.db.errors.ConcurrentModificationError`.
        """
        values: dict[str, Any] = {
            "status": new_status,
            "version": expected_version + 1,
        }
        if extra_values:
            values.update(extra_values)
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(ApprovalChallengeRow)
                .where(
                    ApprovalChallengeRow.challenge_id == challenge_id,
                    ApprovalChallengeRow.version == expected_version,
                    ApprovalChallengeRow.status == expected_status,
                )
                .values(**values)
            ),
        )

        if result.rowcount != 1:
            raise ConcurrentModificationError(
                f"challenge {challenge_id} already consumed or version mismatch"
            )
        self._session.flush()
        row = self.get_challenge(challenge_id)
        if row is None:
            raise NotFoundError(f"challenge {challenge_id} missing after consume")
        return row

    def add_event_fields(self, fields: dict[str, Any]) -> ApprovalEventRow:
        """Construct and persist an approval event without leaking ORM models."""
        row = ApprovalEventRow(**fields)
        self._session.add(row)
        self._session.flush()
        return row

    def get_event(self, event_id: str) -> ApprovalEventRow | None:
        return self._session.scalar(
            select(ApprovalEventRow).where(ApprovalEventRow.event_id == event_id)
        )

    def get_event_by_idempotency_key(self, idempotency_key: str) -> ApprovalEventRow | None:
        return self._session.scalar(
            select(ApprovalEventRow).where(ApprovalEventRow.idempotency_key == idempotency_key)
        )

    def list_events_for_proposal(self, proposal_id: str) -> Sequence[ApprovalEventRow]:
        return self._session.scalars(
            select(ApprovalEventRow)
            .where(ApprovalEventRow.proposal_id == proposal_id)
            .order_by(ApprovalEventRow.approved_at.asc())
        ).all()

    def create_event_idempotent(
        self,
        row: ApprovalEventRow,
        *,
        find_existing: Callable[[], ApprovalEventRow | None],
    ) -> tuple[ApprovalEventRow, bool]:
        return _insert_idempotent(
            self._session,
            row,
            lookup=find_existing,
            conflict_message="approval event conflict without existing row",
        )


class BrokerOrderRepository:
    """Persistence helpers for broker orders and fills."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_client_order_id(self, client_order_id: str) -> BrokerOrderRow | None:
        return self._session.scalar(
            select(BrokerOrderRow).where(BrokerOrderRow.client_order_id == client_order_id)
        )

    def get_by_broker_order_id(self, broker_order_id: str) -> BrokerOrderRow | None:
        return self._session.scalar(
            select(BrokerOrderRow).where(BrokerOrderRow.broker_order_id == broker_order_id)
        )

    def get_by_idempotency_key(self, idempotency_key: str) -> BrokerOrderRow | None:
        return self._session.scalar(
            select(BrokerOrderRow).where(BrokerOrderRow.idempotency_key == idempotency_key)
        )

    def list_for_proposal(self, proposal_id: str) -> Sequence[BrokerOrderRow]:
        return self._session.scalars(
            select(BrokerOrderRow)
            .where(BrokerOrderRow.proposal_id == proposal_id)
            .order_by(BrokerOrderRow.submitted_at.asc())
        ).all()

    def create_idempotent(
        self,
        row: BrokerOrderRow,
        *,
        find_existing: Callable[[], BrokerOrderRow | None],
    ) -> tuple[BrokerOrderRow, bool]:
        return _insert_idempotent(
            self._session,
            row,
            lookup=find_existing,
            conflict_message="broker order conflict without existing row",
        )

    def update_status_if_version(
        self,
        client_order_id: str,
        *,
        expected_version: int,
        new_status: str,
        broker_updated_at: datetime,
    ) -> BrokerOrderRow:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(BrokerOrderRow)
                .where(
                    BrokerOrderRow.client_order_id == client_order_id,
                    BrokerOrderRow.version == expected_version,
                )
                .values(
                    status=new_status,
                    version=expected_version + 1,
                    broker_updated_at=broker_updated_at,
                )
            ),
        )

        if result.rowcount != 1:
            raise ConcurrentModificationError(
                f"broker order {client_order_id} version {expected_version} lost race"
            )
        self._session.flush()
        row = self.get_by_client_order_id(client_order_id)
        if row is None:
            raise NotFoundError(f"broker order {client_order_id} missing after update")
        return row

    def add_fill_idempotent(
        self,
        row: BrokerFillRow,
        *,
        find_existing: Callable[[], BrokerFillRow | None],
    ) -> tuple[BrokerFillRow, bool]:
        return _insert_idempotent(
            self._session,
            row,
            lookup=find_existing,
            conflict_message="broker fill conflict without existing row",
        )

    def get_fill(self, fill_id: str) -> BrokerFillRow | None:
        return self._session.scalar(select(BrokerFillRow).where(BrokerFillRow.fill_id == fill_id))


class AuditRepository:
    """Append-only audit event persistence. No update/delete methods."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, row: AuditEventRow) -> AuditEventRow:
        """Insert an audit event. Business code has no update/delete API."""
        self._session.add(row)
        self._session.flush()
        return row

    def append_fields(self, fields: dict[str, Any]) -> AuditEventRow:
        """Construct and insert an audit row from column fields (no ORM leak)."""
        row = AuditEventRow(**fields)
        return self.append(row)

    def append_idempotent(
        self,
        row: AuditEventRow,
        *,
        find_existing: Callable[[], AuditEventRow | None],
    ) -> tuple[AuditEventRow, bool]:
        return _insert_idempotent(
            self._session,
            row,
            lookup=find_existing,
            conflict_message="audit event conflict without existing row",
        )

    def append_fields_idempotent(
        self,
        fields: dict[str, Any],
        *,
        event_id: str,
    ) -> tuple[AuditEventRow, bool]:
        row = AuditEventRow(**fields)
        return self.append_idempotent(row, find_existing=lambda: self.get(event_id))

    def get(self, event_id: str) -> AuditEventRow | None:
        return self._session.scalar(select(AuditEventRow).where(AuditEventRow.event_id == event_id))

    def list_for_subject(
        self,
        subject_type: str,
        subject_id: str,
    ) -> Sequence[AuditEventRow]:
        return self._session.scalars(
            select(AuditEventRow)
            .where(
                AuditEventRow.subject_type == subject_type,
                AuditEventRow.subject_id == subject_id,
            )
            .order_by(AuditEventRow.occurred_at.asc(), AuditEventRow.id.asc())
        ).all()

    def list_by_correlation(self, correlation_id: str) -> Sequence[AuditEventRow]:
        return self._session.scalars(
            select(AuditEventRow)
            .where(AuditEventRow.correlation_id == correlation_id)
            .order_by(AuditEventRow.occurred_at.asc(), AuditEventRow.id.asc())
        ).all()


__all__ = [
    "ApprovalRepository",
    "AuditRepository",
    "BrokerOrderRepository",
    "ProposalRepository",
    "RiskDecisionRepository",
]
