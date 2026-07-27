"""Unit tests for order/cancel state machines (P02-T9)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ainvest.execution.state_machine import (
    CANCEL_EDGES,
    CANCEL_TERMINAL,
    ORDER_EDGES,
    ORDER_TERMINAL,
    CancelCommandState,
    IllegalTransitionError,
    InMemoryStatePersistence,
    OrderLifecycleState,
    PersistenceError,
    StaleStateError,
    is_cancel_transition_allowed,
    is_order_transition_allowed,
    legal_cancel_targets,
    legal_order_targets,
    transition_cancel,
    transition_order,
)

AS_OF = datetime(2026, 7, 26, 18, 30, 0, tzinfo=UTC)
SUBJECT = "ordp_01HZYSTATEMACHINE01"


@pytest.mark.unit
def test_every_declared_order_edge_is_allowed() -> None:
    for source, target in ORDER_EDGES:
        assert is_order_transition_allowed(source, target)
        assert target in legal_order_targets(source)


@pytest.mark.unit
def test_every_declared_cancel_edge_is_allowed() -> None:
    for source, target in CANCEL_EDGES:
        assert is_cancel_transition_allowed(source, target)
        assert target in legal_cancel_targets(source)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OrderLifecycleState.SIGNAL_CREATED, OrderLifecycleState.APPROVED),
        (OrderLifecycleState.SUBMITTED, OrderLifecycleState.SUBMITTING),
        (OrderLifecycleState.SUBMIT_UNKNOWN, OrderLifecycleState.SUBMITTED),
        (OrderLifecycleState.SUBMIT_UNKNOWN, OrderLifecycleState.SUBMITTING),
        (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED),
        (OrderLifecycleState.RECONCILING, OrderLifecycleState.REJECTED),
        (OrderLifecycleState.MANUAL_REVIEW, OrderLifecycleState.SUBMITTED),
    ],
)
def test_illegal_order_edges_rejected(
    current: OrderLifecycleState, target: OrderLifecycleState
) -> None:
    assert not is_order_transition_allowed(current, target)
    store = InMemoryStatePersistence()
    with pytest.raises(IllegalTransitionError):
        transition_order(
            current=current,
            expected_current=current,
            target=target,
            subject_id=SUBJECT,
            event_id="evt_illegal_order_1",
            persistence=store,
            occurred_at=AS_OF,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("current", "target"),
    [
        (CancelCommandState.CANCEL_UNKNOWN, CancelCommandState.CANCEL_CONFIRMED),
        (CancelCommandState.CANCEL_UNKNOWN, CancelCommandState.CANCEL_REQUESTED),
        (CancelCommandState.CANCEL_CONFIRMED, CancelCommandState.CANCEL_REQUESTED),
        (CancelCommandState.CANCEL_REQUESTED, CancelCommandState.CANCEL_RECONCILING),
    ],
)
def test_illegal_cancel_edges_rejected(
    current: CancelCommandState, target: CancelCommandState
) -> None:
    assert not is_cancel_transition_allowed(current, target)
    store = InMemoryStatePersistence()
    with pytest.raises(IllegalTransitionError):
        transition_cancel(
            current=current,
            expected_current=current,
            target=target,
            subject_id=SUBJECT,
            event_id="evt_illegal_cancel_1",
            persistence=store,
            occurred_at=AS_OF,
        )


@pytest.mark.unit
def test_terminal_order_states_have_no_targets() -> None:
    for state in ORDER_TERMINAL:
        assert legal_order_targets(state) == frozenset()
        assert not is_order_transition_allowed(state, OrderLifecycleState.SUBMITTED)


@pytest.mark.unit
def test_terminal_cancel_states_have_no_targets() -> None:
    for state in CANCEL_TERMINAL:
        assert legal_cancel_targets(state) == frozenset()


@pytest.mark.unit
def test_submit_unknown_only_allows_reconciling() -> None:
    assert legal_order_targets(OrderLifecycleState.SUBMIT_UNKNOWN) == frozenset(
        {OrderLifecycleState.RECONCILING}
    )


@pytest.mark.unit
def test_cancel_unknown_only_allows_reconciling() -> None:
    assert legal_cancel_targets(CancelCommandState.CANCEL_UNKNOWN) == frozenset(
        {CancelCommandState.CANCEL_RECONCILING}
    )


@pytest.mark.unit
def test_happy_path_order_transitions_persist() -> None:
    store = InMemoryStatePersistence()
    path = [
        (OrderLifecycleState.SIGNAL_CREATED, OrderLifecycleState.PROPOSAL_CREATED),
        (OrderLifecycleState.PROPOSAL_CREATED, OrderLifecycleState.APPROVAL_PENDING),
        (OrderLifecycleState.APPROVAL_PENDING, OrderLifecycleState.APPROVED),
        (OrderLifecycleState.APPROVED, OrderLifecycleState.SUBMITTING),
        (OrderLifecycleState.SUBMITTING, OrderLifecycleState.SUBMITTED),
        (OrderLifecycleState.SUBMITTED, OrderLifecycleState.FILLED),
    ]
    current = OrderLifecycleState.SIGNAL_CREATED
    for index, (expected, target) in enumerate(path):
        result = transition_order(
            current=current,
            expected_current=expected,
            target=target,
            subject_id=SUBJECT,
            event_id=f"evt_order_path_{index}",
            persistence=store,
            occurred_at=AS_OF,
            correlation_id="corr_order_1",
        )
        assert result.idempotent is False
        assert result.after == target.value
        current = target
    assert [row["after"] for row in store.records] == [edge[1].value for edge in path]


@pytest.mark.unit
def test_submit_unknown_recovery_path() -> None:
    store = InMemoryStatePersistence()
    transition_order(
        current=OrderLifecycleState.SUBMIT_UNKNOWN,
        expected_current=OrderLifecycleState.SUBMIT_UNKNOWN,
        target=OrderLifecycleState.RECONCILING,
        subject_id=SUBJECT,
        event_id="evt_recon_1",
        persistence=store,
        occurred_at=AS_OF,
    )
    result = transition_order(
        current=OrderLifecycleState.RECONCILING,
        expected_current=OrderLifecycleState.RECONCILING,
        target=OrderLifecycleState.SUBMITTED,
        subject_id=SUBJECT,
        event_id="evt_recon_2",
        persistence=store,
        occurred_at=AS_OF,
    )
    assert result.after == OrderLifecycleState.SUBMITTED.value


@pytest.mark.unit
def test_cancel_recovery_path() -> None:
    store = InMemoryStatePersistence()
    transition_cancel(
        current=CancelCommandState.CANCEL_REQUESTED,
        expected_current=CancelCommandState.CANCEL_REQUESTED,
        target=CancelCommandState.CANCEL_UNKNOWN,
        subject_id=SUBJECT,
        event_id="evt_cancel_unk",
        persistence=store,
        occurred_at=AS_OF,
    )
    transition_cancel(
        current=CancelCommandState.CANCEL_UNKNOWN,
        expected_current=CancelCommandState.CANCEL_UNKNOWN,
        target=CancelCommandState.CANCEL_RECONCILING,
        subject_id=SUBJECT,
        event_id="evt_cancel_recon",
        persistence=store,
        occurred_at=AS_OF,
    )
    result = transition_cancel(
        current=CancelCommandState.CANCEL_RECONCILING,
        expected_current=CancelCommandState.CANCEL_RECONCILING,
        target=CancelCommandState.CANCEL_NOT_APPLIED,
        subject_id=SUBJECT,
        event_id="evt_cancel_na",
        persistence=store,
        occurred_at=AS_OF,
    )
    assert result.after == CancelCommandState.CANCEL_NOT_APPLIED.value


@pytest.mark.unit
def test_stale_expected_current_rejected() -> None:
    store = InMemoryStatePersistence()
    with pytest.raises(StaleStateError, match="stale"):
        transition_order(
            current=OrderLifecycleState.APPROVED,
            expected_current=OrderLifecycleState.APPROVAL_PENDING,
            target=OrderLifecycleState.SUBMITTING,
            subject_id=SUBJECT,
            event_id="evt_stale",
            persistence=store,
            occurred_at=AS_OF,
        )
    assert store.records == []


@pytest.mark.unit
def test_duplicate_event_id_is_idempotent() -> None:
    store = InMemoryStatePersistence()
    first = transition_order(
        current=OrderLifecycleState.APPROVED,
        expected_current=OrderLifecycleState.APPROVED,
        target=OrderLifecycleState.SUBMITTING,
        subject_id=SUBJECT,
        event_id="evt_dup_1",
        persistence=store,
        occurred_at=AS_OF,
    )
    second = transition_order(
        current=OrderLifecycleState.SUBMITTING,
        expected_current=OrderLifecycleState.APPROVED,  # stale presentation
        target=OrderLifecycleState.SUBMITTING,
        subject_id=SUBJECT,
        event_id="evt_dup_1",
        persistence=store,
        occurred_at=AS_OF,
    )
    assert first.idempotent is False
    assert second.idempotent is True
    assert len(store.records) == 1


@pytest.mark.unit
def test_same_state_target_is_idempotent_without_persist() -> None:
    store = InMemoryStatePersistence()
    result = transition_order(
        current=OrderLifecycleState.SUBMITTED,
        expected_current=OrderLifecycleState.SUBMITTED,
        target=OrderLifecycleState.SUBMITTED,
        subject_id=SUBJECT,
        event_id="evt_noop",
        persistence=store,
        occurred_at=AS_OF,
    )
    assert result.idempotent is True
    assert store.records == []


@pytest.mark.unit
def test_persistence_failure_does_not_report_success() -> None:
    store = InMemoryStatePersistence()
    store.fail_next = True
    with pytest.raises(PersistenceError):
        transition_order(
            current=OrderLifecycleState.APPROVED,
            expected_current=OrderLifecycleState.APPROVED,
            target=OrderLifecycleState.SUBMITTING,
            subject_id=SUBJECT,
            event_id="evt_fail",
            persistence=store,
            occurred_at=AS_OF,
        )
    assert store.records == []
    assert store.has_event("evt_fail") is False


@pytest.mark.unit
def test_naive_occurred_at_rejected() -> None:
    store = InMemoryStatePersistence()
    with pytest.raises(ValueError, match="timezone-aware"):
        transition_order(
            current=OrderLifecycleState.APPROVED,
            expected_current=OrderLifecycleState.APPROVED,
            target=OrderLifecycleState.SUBMITTING,
            subject_id=SUBJECT,
            event_id="evt_naive",
            persistence=store,
            occurred_at=datetime(2026, 7, 26, 18, 30, 0),
        )


@pytest.mark.unit
def test_all_order_states_appear_in_graph() -> None:
    touched = {s for edge in ORDER_EDGES for s in edge}
    # SIGNAL_CREATED is only a source; terminals only targets — all enum members
    # must appear in at least one edge except none should be orphaned.
    assert touched == set(OrderLifecycleState)


@pytest.mark.unit
def test_all_cancel_states_appear_in_graph() -> None:
    touched = {s for edge in CANCEL_EDGES for s in edge}
    assert touched == set(CancelCommandState)
