"""Unit of Work: one commit per business operation, rollback on failure."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Self

from sqlalchemy.orm import Session, sessionmaker

from ainvest.db.repositories import (
    ApprovalRepository,
    AuditRepository,
    BrokerOrderRepository,
    ProposalRepository,
    RiskDecisionRepository,
)


class UnitOfWork:
    """Transactional boundary exposing domain repositories.

    Entering the context begins a transaction. Exiting without exception commits
    exactly once. Any exception rolls back and re-raises.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self.proposals: ProposalRepository | None = None
        self.risk_decisions: RiskDecisionRepository | None = None
        self.approvals: ApprovalRepository | None = None
        self.broker_orders: BrokerOrderRepository | None = None
        self.audit: AuditRepository | None = None

    def __enter__(self) -> Self:
        self.session = self._session_factory()
        self.proposals = ProposalRepository(self.session)
        self.risk_decisions = RiskDecisionRepository(self.session)
        self.approvals = ApprovalRepository(self.session)
        self.broker_orders = BrokerOrderRepository(self.session)
        self.audit = AuditRepository(self.session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del traceback
        assert self.session is not None
        try:
            if exc_type is None:
                self.session.commit()
            else:
                self.session.rollback()
        finally:
            self.session.close()
            self.session = None
            self.proposals = None
            self.risk_decisions = None
            self.approvals = None
            self.broker_orders = None
            self.audit = None

    def commit(self) -> None:
        """Explicit mid-scope commit (rare; prefer context exit)."""
        if self.session is None:
            raise RuntimeError("UnitOfWork is not active")
        self.session.commit()

    def rollback(self) -> None:
        """Explicit rollback without leaving the context."""
        if self.session is None:
            raise RuntimeError("UnitOfWork is not active")
        self.session.rollback()

    @property
    def proposals_repo(self) -> ProposalRepository:
        if self.proposals is None:
            raise RuntimeError("UnitOfWork is not active")
        return self.proposals

    @property
    def approvals_repo(self) -> ApprovalRepository:
        if self.approvals is None:
            raise RuntimeError("UnitOfWork is not active")
        return self.approvals

    @property
    def risk_decisions_repo(self) -> RiskDecisionRepository:
        if self.risk_decisions is None:
            raise RuntimeError("UnitOfWork is not active")
        return self.risk_decisions

    @property
    def broker_orders_repo(self) -> BrokerOrderRepository:
        if self.broker_orders is None:
            raise RuntimeError("UnitOfWork is not active")
        return self.broker_orders

    @property
    def audit_repo(self) -> AuditRepository:
        if self.audit is None:
            raise RuntimeError("UnitOfWork is not active")
        return self.audit


@contextmanager
def unit_of_work(session_factory: sessionmaker[Session]) -> Iterator[UnitOfWork]:
    """Convenience context manager wrapping :class:`UnitOfWork`."""
    with UnitOfWork(session_factory) as uow:
        yield uow


def nested_savepoint(session: Session) -> AbstractContextManager[Any]:
    """Return a nested transaction/savepoint for idempotent sub-operations."""
    return session.begin_nested()


__all__ = ["UnitOfWork", "nested_savepoint", "unit_of_work"]
