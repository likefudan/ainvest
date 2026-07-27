"""Unit tests for repositories, UoW, concurrency, and idempotency."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from helpers import (
    ORDER_HASH,
    TOKEN_HASH,
    later,
    sample_broker_order_kwargs,
    sample_fill_kwargs,
    sample_proposal_kwargs,
    utc,
)
from sqlalchemy.orm import Session, sessionmaker

from ainvest.db.errors import ConcurrentModificationError, PersistenceError
from ainvest.db.models import (
    ApprovalChallengeRow,
    ApprovalEventRow,
    BrokerFillRow,
    BrokerOrderRow,
    OrderProposalRow,
)
from ainvest.db.uow import UnitOfWork


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
def test_proposal_update_status_if_version_succeeds(
    session_factory: sessionmaker[Session],
) -> None:
    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None
        uow.proposals.add(OrderProposalRow(**sample_proposal_kwargs()))

    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None
        updated = uow.proposals.update_status_if_version(
            "ordp_01HZYTEST0000001",
            expected_version=1,
            new_status="APPROVED",
            extra_values={"payload_json": {"approved": True}},
        )
        assert updated.status == "APPROVED"
        assert updated.version == 2
        assert updated.payload_json == {"approved": True}


@pytest.mark.unit
def test_proposal_update_status_if_version_conflict_leaves_row_unchanged(
    session_factory: sessionmaker[Session],
) -> None:
    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None
        uow.proposals.add(OrderProposalRow(**sample_proposal_kwargs()))

    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None
        with pytest.raises(ConcurrentModificationError, match="version 99 lost race"):
            uow.proposals.update_status_if_version(
                "ordp_01HZYTEST0000001",
                expected_version=99,
                new_status="APPROVED",
            )
        row = uow.proposals.get_by_proposal_id("ordp_01HZYTEST0000001")
        assert row is not None
        assert row.status == "PENDING_APPROVAL"
        assert row.version == 1


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
    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None and uow.broker_orders is not None
        uow.proposals.add(OrderProposalRow(**sample_proposal_kwargs()))
        first, created_flag = uow.broker_orders.create_idempotent(
            BrokerOrderRow(**sample_broker_order_kwargs()),
            find_existing=lambda: uow.broker_orders.get_by_client_order_id(  # type: ignore[union-attr]
                "client_ord_001"
            ),
        )
        assert created_flag is True
        second, created_again = uow.broker_orders.create_idempotent(
            BrokerOrderRow(
                **sample_broker_order_kwargs(
                    broker_order_id="brk_order_002",
                    idempotency_key="idem_broker_002",
                )
            ),
            find_existing=lambda: uow.broker_orders.get_by_client_order_id(  # type: ignore[union-attr]
                "client_ord_001"
            ),
        )
        assert created_again is False
        assert second.broker_order_id == first.broker_order_id


@pytest.mark.unit
def test_broker_order_allows_null_broker_order_id(
    session_factory: sessionmaker[Session],
) -> None:
    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None and uow.broker_orders is not None
        uow.proposals.add(OrderProposalRow(**sample_proposal_kwargs()))
        row, created_flag = uow.broker_orders.create_idempotent(
            BrokerOrderRow(
                **sample_broker_order_kwargs(
                    broker_order_id=None,
                    client_order_id="client_ord_unknown",
                    status="SUBMIT_UNKNOWN",
                    idempotency_key="idem_broker_unknown",
                )
            ),
            find_existing=lambda: uow.broker_orders.get_by_client_order_id(  # type: ignore[union-attr]
                "client_ord_unknown"
            ),
        )
        assert created_flag is True
        assert row.broker_order_id is None
        assert row.status == "SUBMIT_UNKNOWN"


@pytest.mark.unit
def test_broker_order_update_status_if_version_succeeds(
    session_factory: sessionmaker[Session],
) -> None:
    updated_at = later(utc(), 30)
    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None and uow.broker_orders is not None
        uow.proposals.add(OrderProposalRow(**sample_proposal_kwargs()))
        uow.broker_orders.create_idempotent(
            BrokerOrderRow(**sample_broker_order_kwargs()),
            find_existing=lambda: uow.broker_orders.get_by_client_order_id(  # type: ignore[union-attr]
                "client_ord_001"
            ),
        )

    with UnitOfWork(session_factory) as uow:
        assert uow.broker_orders is not None
        updated = uow.broker_orders.update_status_if_version(
            "client_ord_001",
            expected_version=1,
            new_status="FILLED",
            broker_updated_at=updated_at,
        )
        assert updated.status == "FILLED"
        assert updated.version == 2
        assert updated.broker_updated_at == updated_at


@pytest.mark.unit
def test_broker_order_update_status_if_version_conflict_leaves_row_unchanged(
    session_factory: sessionmaker[Session],
) -> None:
    created = utc()
    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None and uow.broker_orders is not None
        uow.proposals.add(OrderProposalRow(**sample_proposal_kwargs()))
        uow.broker_orders.create_idempotent(
            BrokerOrderRow(**sample_broker_order_kwargs()),
            find_existing=lambda: uow.broker_orders.get_by_client_order_id(  # type: ignore[union-attr]
                "client_ord_001"
            ),
        )

    with UnitOfWork(session_factory) as uow:
        assert uow.broker_orders is not None
        with pytest.raises(ConcurrentModificationError, match="version 99 lost race"):
            uow.broker_orders.update_status_if_version(
                "client_ord_001",
                expected_version=99,
                new_status="FILLED",
                broker_updated_at=later(created, 30),
            )
        row = uow.broker_orders.get_by_client_order_id("client_ord_001")
        assert row is not None
        assert row.status == "ACCEPTED"
        assert row.version == 1
        assert row.broker_updated_at == created


@pytest.mark.unit
def test_broker_fill_add_idempotent_insert_and_replay(
    session_factory: sessionmaker[Session],
) -> None:
    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None and uow.broker_orders is not None
        uow.proposals.add(OrderProposalRow(**sample_proposal_kwargs()))
        uow.broker_orders.create_idempotent(
            BrokerOrderRow(**sample_broker_order_kwargs()),
            find_existing=lambda: uow.broker_orders.get_by_client_order_id(  # type: ignore[union-attr]
                "client_ord_001"
            ),
        )
        first, created = uow.broker_orders.add_fill_idempotent(
            BrokerFillRow(**sample_fill_kwargs()),
            find_existing=lambda: uow.broker_orders.get_fill("fill_01HZYTEST0000001"),  # type: ignore[union-attr]
        )
        assert created is True
        assert first.fill_id == "fill_01HZYTEST0000001"
        assert first.quantity == Decimal("1")

        second, created_again = uow.broker_orders.add_fill_idempotent(
            BrokerFillRow(
                **sample_fill_kwargs(
                    quantity=Decimal("9"),
                    price=Decimal("1.00"),
                )
            ),
            find_existing=lambda: uow.broker_orders.get_fill("fill_01HZYTEST0000001"),  # type: ignore[union-attr]
        )
        assert created_again is False
        assert second.fill_id == first.fill_id
        assert second.quantity == Decimal("1")
        assert second.price == Decimal("214.50")


@pytest.mark.unit
def test_broker_fill_add_idempotent_conflict_without_existing_raises(
    session_factory: sessionmaker[Session],
) -> None:
    with UnitOfWork(session_factory) as uow:
        assert uow.proposals is not None and uow.broker_orders is not None
        uow.proposals.add(OrderProposalRow(**sample_proposal_kwargs()))
        uow.broker_orders.create_idempotent(
            BrokerOrderRow(**sample_broker_order_kwargs()),
            find_existing=lambda: uow.broker_orders.get_by_client_order_id(  # type: ignore[union-attr]
                "client_ord_001"
            ),
        )
        first, created = uow.broker_orders.add_fill_idempotent(
            BrokerFillRow(**sample_fill_kwargs()),
            find_existing=lambda: uow.broker_orders.get_fill("fill_01HZYTEST0000001"),  # type: ignore[union-attr]
        )
        assert created is True

        with pytest.raises(PersistenceError, match="broker fill conflict without existing row"):
            uow.broker_orders.add_fill_idempotent(
                BrokerFillRow(**sample_fill_kwargs(quantity=Decimal("2"))),
                find_existing=lambda: None,
            )
        # Original fill remains; failed insert rolled back via savepoint.
        assert uow.broker_orders.get_fill(first.fill_id) is not None
        assert uow.broker_orders.get_fill(first.fill_id).quantity == Decimal("1")  # type: ignore[union-attr]
