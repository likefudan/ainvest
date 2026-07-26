"""Unit tests for repositories, UoW, concurrency, and idempotency."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from helpers import ORDER_HASH, TOKEN_HASH, later, sample_proposal_kwargs, utc
from sqlalchemy.orm import Session, sessionmaker

from ainvest.db.errors import ConcurrentModificationError
from ainvest.db.models import (
    ApprovalChallengeRow,
    ApprovalEventRow,
    BrokerOrderRow,
    OrderProposalRow,
)
from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.uow import UnitOfWork


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.mark.unit
def test_uow_commits_on_success(session_factory: sessionmaker[Session]) -> None:
    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None
        row = OrderProposalRow(**sample_proposal_kwargs())
        uow.proposals.add(row)

    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None
        found = uow.proposals.get_by_proposal_id("ordp_01HZYTEST0000001")
        assert found is not None
        assert found.quantity == Decimal("2")


@pytest.mark.unit
def test_uow_rollback_on_failure_leaves_no_partial_state(
    session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(RuntimeError, match="boom"), UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None
        uow.proposals.add(OrderProposalRow(**sample_proposal_kwargs()))
        raise RuntimeError("boom")

    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None
        assert uow.proposals.get_by_proposal_id("ordp_01HZYTEST0000001") is None


@pytest.mark.unit
def test_proposal_idempotent_create_returns_existing(
    session_factory: sessionmaker[Session],
) -> None:
    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None
        first, created = uow.proposals.create_idempotent(
            OrderProposalRow(**sample_proposal_kwargs()),
            find_existing=lambda: uow.proposals.get_by_idempotency_key(  # type: ignore[union-attr]
                "idem_proposal_001"
            ),
        )
        assert created is True
        second, created_again = uow.proposals.create_idempotent(
            OrderProposalRow(
                **sample_proposal_kwargs(
                    proposal_id="ordp_01HZYTEST0000002",
                    order_hash="sha256:" + ("ef" * 32),
                )
            ),
            find_existing=lambda: uow.proposals.get_by_idempotency_key(  # type: ignore[union-attr]
                "idem_proposal_001"
            ),
        )
        assert created_again is False
        assert second.proposal_id == first.proposal_id


@pytest.mark.unit
def test_concurrent_challenge_consume_succeeds_once(
    session_factory: sessionmaker[Session],
) -> None:
    created = utc()
    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None and uow.approvals is not None
        uow.proposals.add(OrderProposalRow(**sample_proposal_kwargs()))
        uow.approvals.add_challenge(
            ApprovalChallengeRow(
                challenge_id="chal_01HZYTEST0000001",
                proposal_id="ordp_01HZYTEST0000001",
                order_hash=ORDER_HASH,
                method="telegram",
                scope="paper",
                token_hash=TOKEN_HASH,
                status="PENDING",
                challenge_created_at=created,
                expires_at=later(created, 300),
                payload_json={},
                version=1,
            )
        )

    winners = 0
    losers = 0
    for _ in range(2):
        try:
            with UnitOfWork(session_factory) as uow:
                assert uow.approvals is not None
                challenge = uow.approvals.get_challenge("chal_01HZYTEST0000001")
                assert challenge is not None
                uow.approvals.consume_challenge_once(
                    "chal_01HZYTEST0000001",
                    expected_version=challenge.version,
                )
                uow.approvals.create_event_idempotent(
                    ApprovalEventRow(
                        event_id="apev_01HZYTEST0000001",
                        challenge_id="chal_01HZYTEST0000001",
                        proposal_id="ordp_01HZYTEST0000001",
                        order_hash=ORDER_HASH,
                        method="telegram",
                        scope="paper",
                        outcome="APPROVED",
                        approved_at=datetime.now(UTC),
                        approver_identity="tg:12345",
                        idempotency_key="idem_approval_001",
                        payload_json={},
                    ),
                    find_existing=lambda: uow.approvals.get_event_by_idempotency_key(  # type: ignore[union-attr]
                        "idem_approval_001"
                    ),
                )
            winners += 1
        except ConcurrentModificationError:
            losers += 1

    assert winners == 1
    assert losers == 1

    with UnitOfWork(session_factory) as uow:
        assert uow.approvals is not None
        challenge = uow.approvals.get_challenge("chal_01HZYTEST0000001")
        assert challenge is not None
        assert challenge.status == "CONSUMED"
        events = uow.approvals.list_events_for_proposal("ordp_01HZYTEST0000001")
        assert len(events) == 1


@pytest.mark.unit
def test_broker_order_client_order_id_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    created = utc()
    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None and uow.broker_orders is not None
        uow.proposals.add(OrderProposalRow(**sample_proposal_kwargs()))
        first, created_flag = uow.broker_orders.create_idempotent(
            BrokerOrderRow(
                broker_order_id="brk_order_001",
                client_order_id="client_ord_001",
                proposal_id="ordp_01HZYTEST0000001",
                order_hash=ORDER_HASH,
                account_scope="paper",
                side="BUY",
                status="ACCEPTED",
                submitted_at=created,
                broker_updated_at=created,
                idempotency_key="idem_broker_001",
                payload_json={},
                version=1,
            ),
            find_existing=lambda: uow.broker_orders.get_by_client_order_id(  # type: ignore[union-attr]
                "client_ord_001"
            ),
        )
        assert created_flag is True
        second, created_again = uow.broker_orders.create_idempotent(
            BrokerOrderRow(
                broker_order_id="brk_order_002",
                client_order_id="client_ord_001",
                proposal_id="ordp_01HZYTEST0000001",
                order_hash=ORDER_HASH,
                account_scope="paper",
                side="BUY",
                status="ACCEPTED",
                submitted_at=created,
                broker_updated_at=created,
                idempotency_key="idem_broker_002",
                payload_json={},
                version=1,
            ),
            find_existing=lambda: uow.broker_orders.get_by_client_order_id(  # type: ignore[union-attr]
                "client_ord_001"
            ),
        )
        assert created_again is False
        assert second.broker_order_id == first.broker_order_id
