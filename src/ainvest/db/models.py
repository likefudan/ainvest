"""SQLAlchemy ORM models for ainvest domain persistence (P02-T6).

Indexed query columns are separate from JSON payloads. Money uses
:class:`~ainvest.db.types.DecimalString`. Timestamps are UTC-aware.
Schemas must never import this module.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from ainvest.db.base import (
    Base,
    CodeConfigVersionMixin,
    SchemaVersionMixin,
    TimestampMixin,
    VersionMixin,
)
from ainvest.db.types import DecimalString, UtcDateTime


class ResearchRun(Base, TimestampMixin, SchemaVersionMixin, CodeConfigVersionMixin):
    """One research agent evaluation run."""

    __tablename__ = "research_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="COMPLETED")
    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    packets: Mapped[list[ResearchPacketRow]] = relationship(back_populates="run")


class ResearchPacketRow(Base, TimestampMixin, SchemaVersionMixin, CodeConfigVersionMixin):
    """Persisted ResearchPacket with indexed identity columns."""

    __tablename__ = "research_packets"
    __table_args__ = (UniqueConstraint("research_id", name="uq_research_packets_research_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    research_id: Mapped[str] = mapped_column(String(160), nullable=False)
    run_id: Mapped[str | None] = mapped_column(
        String(160),
        ForeignKey("research_runs.run_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    as_of: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    run: Mapped[ResearchRun | None] = relationship(back_populates="packets")


class StrategyRun(Base, TimestampMixin, SchemaVersionMixin, CodeConfigVersionMixin):
    """One strategy evaluation against a research/portfolio context."""

    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    strategy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    research_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    as_of: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="COMPLETED")
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    signals: Mapped[list[TradeSignalRow]] = relationship(back_populates="strategy_run")


class TradeSignalRow(Base, TimestampMixin, SchemaVersionMixin, CodeConfigVersionMixin):
    """Persisted TradeSignal with uniqueness on signal_id."""

    __tablename__ = "trade_signals"
    __table_args__ = (UniqueConstraint("signal_id", name="uq_trade_signals_signal_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(160), nullable=False)
    research_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    strategy_run_id: Mapped[str | None] = mapped_column(
        String(160),
        ForeignKey("strategy_runs.run_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    strategy: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    strategy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    intent: Mapped[str] = mapped_column(String(16), nullable=False)
    strength: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    target_weight: Mapped[Decimal | None] = mapped_column(DecimalString(), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    strategy_run: Mapped[StrategyRun | None] = relationship(back_populates="signals")


class RiskDecisionRow(Base, TimestampMixin, SchemaVersionMixin, CodeConfigVersionMixin):
    """Persisted RiskDecision bound to a candidate and/or proposal."""

    __tablename__ = "risk_decisions"
    __table_args__ = (
        UniqueConstraint("risk_decision_id", name="uq_risk_decisions_risk_decision_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    risk_decision_id: Mapped[str] = mapped_column(String(160), nullable=False)
    candidate_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    proposal_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    decided_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    rule_set_version: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class OrderProposalRow(
    Base, TimestampMixin, SchemaVersionMixin, CodeConfigVersionMixin, VersionMixin
):
    """Persisted OrderProposal with optimistic concurrency versioning."""

    __tablename__ = "order_proposals"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_order_proposals_proposal_id"),
        UniqueConstraint("order_hash", name="uq_order_proposals_order_hash"),
        Index("ix_order_proposals_status_expires", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[str] = mapped_column(String(160), nullable=False)
    signal_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    risk_decision_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    account_scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    instrument_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    limit_price: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    order_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_APPROVAL")
    proposal_created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    challenges: Mapped[list[ApprovalChallengeRow]] = relationship(back_populates="proposal")
    approval_events: Mapped[list[ApprovalEventRow]] = relationship(back_populates="proposal")
    broker_orders: Mapped[list[BrokerOrderRow]] = relationship(back_populates="proposal")


class ApprovalChallengeRow(
    Base, TimestampMixin, SchemaVersionMixin, CodeConfigVersionMixin, VersionMixin
):
    """One-time approval challenge. Stores token *hash* only, never raw tokens."""

    __tablename__ = "approval_challenges"
    __table_args__ = (
        UniqueConstraint("challenge_id", name="uq_approval_challenges_challenge_id"),
        UniqueConstraint("token_hash", name="uq_approval_challenges_token_hash"),
        Index("ix_approval_challenges_status_expires", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    challenge_id: Mapped[str] = mapped_column(String(160), nullable=False)
    proposal_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("order_proposals.proposal_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    order_hash: Mapped[str] = mapped_column(String(71), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    challenge_created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    proposal: Mapped[OrderProposalRow] = relationship(back_populates="challenges")
    events: Mapped[list[ApprovalEventRow]] = relationship(back_populates="challenge")


class ApprovalEventRow(Base, TimestampMixin, SchemaVersionMixin, CodeConfigVersionMixin):
    """Immutable approval outcome. Append-only business semantics."""

    __tablename__ = "approval_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_approval_events_event_id"),
        UniqueConstraint(
            "challenge_id",
            "outcome",
            name="uq_approval_events_challenge_outcome",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    challenge_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("approval_challenges.challenge_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    proposal_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("order_proposals.proposal_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    order_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    approver_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    challenge: Mapped[ApprovalChallengeRow] = relationship(back_populates="events")
    proposal: Mapped[OrderProposalRow] = relationship(back_populates="approval_events")


class BrokerOrderRow(
    Base, TimestampMixin, SchemaVersionMixin, CodeConfigVersionMixin, VersionMixin
):
    """Broker working-order snapshot keyed by client and broker order IDs."""

    __tablename__ = "broker_orders"
    __table_args__ = (
        UniqueConstraint("client_order_id", name="uq_broker_orders_client_order_id"),
        UniqueConstraint("broker_order_id", name="uq_broker_orders_broker_order_id"),
        Index("ix_broker_orders_proposal_status", "proposal_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broker_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    proposal_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("order_proposals.proposal_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    order_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    account_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    submitted_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    broker_updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    proposal: Mapped[OrderProposalRow] = relationship(back_populates="broker_orders")
    fills: Mapped[list[BrokerFillRow]] = relationship(back_populates="broker_order")


class BrokerFillRow(Base, TimestampMixin, SchemaVersionMixin, CodeConfigVersionMixin):
    """Individual fill against a broker order."""

    __tablename__ = "broker_fills"
    __table_args__ = (UniqueConstraint("fill_id", name="uq_broker_fills_fill_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fill_id: Mapped[str] = mapped_column(String(128), nullable=False)
    broker_order_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("broker_orders.broker_order_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    price: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    filled_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    broker_order: Mapped[BrokerOrderRow] = relationship(back_populates="fills")


class CancelCommandRow(
    Base, TimestampMixin, SchemaVersionMixin, CodeConfigVersionMixin, VersionMixin
):
    """Separate cancel command path (design.md §8 / §9)."""

    __tablename__ = "cancel_commands"
    __table_args__ = (
        UniqueConstraint("cancel_id", name="uq_cancel_commands_cancel_id"),
        UniqueConstraint("idempotency_key", name="uq_cancel_commands_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cancel_id: Mapped[str] = mapped_column(String(160), nullable=False)
    proposal_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    broker_order_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    order_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    account_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="REQUESTED")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class OperatorActionRow(Base, TimestampMixin, SchemaVersionMixin, CodeConfigVersionMixin):
    """Privileged operator control-plane actions (design.md §9)."""

    __tablename__ = "operator_actions"
    __table_args__ = (
        UniqueConstraint("action_id", name="uq_operator_actions_action_id"),
        UniqueConstraint("idempotency_key", name="uq_operator_actions_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[str] = mapped_column(String(160), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    acted_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class PortfolioSnapshotRow(Base, TimestampMixin, SchemaVersionMixin, CodeConfigVersionMixin):
    """Persisted PortfolioSnapshot with indexed as_of / account scope."""

    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_id", name="uq_portfolio_snapshots_snapshot_id"),
        Index("ix_portfolio_snapshots_scope_as_of", "account_scope", "as_of"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    account_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    cash: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    buying_power: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    equity: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class AuditEventRow(Base):
    """Append-only audit event row. No update/delete API for business code."""

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_audit_events_event_id"),
        Index("ix_audit_events_correlation", "correlation_id"),
        Index("ix_audit_events_subject", "subject_type", "subject_id"),
        Index("ix_audit_events_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    code_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    output_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


__all__ = [
    "ApprovalChallengeRow",
    "ApprovalEventRow",
    "AuditEventRow",
    "BrokerFillRow",
    "BrokerOrderRow",
    "CancelCommandRow",
    "OperatorActionRow",
    "OrderProposalRow",
    "PortfolioSnapshotRow",
    "ResearchPacketRow",
    "ResearchRun",
    "RiskDecisionRow",
    "StrategyRun",
    "TradeSignalRow",
]
