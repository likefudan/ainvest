"""telegram paper approval binding and outbox

Revision ID: 5ce8169131f2
Revises: bf42c70e30d1
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5ce8169131f2"
down_revision: str | None = "bf42c70e30d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_approval_bindings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("challenge_id", sa.String(length=160), nullable=False),
        sa.Column("proposal_id", sa.String(length=160), nullable=False),
        sa.Column("order_hash", sa.String(length=71), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name=op.f("ck_telegram_approval_bindings_environment"),
        ),
        sa.CheckConstraint(
            "user_id > 0 AND user_id <= 9223372036854775807",
            name=op.f("ck_telegram_approval_bindings_user_id_range"),
        ),
        sa.CheckConstraint(
            "chat_id > 0 AND chat_id <= 9223372036854775807",
            name=op.f("ck_telegram_approval_bindings_chat_id_range"),
        ),
        sa.CheckConstraint(
            "message_id > 0 AND message_id <= 9223372036854775807",
            name=op.f("ck_telegram_approval_bindings_message_id_range"),
        ),
        sa.ForeignKeyConstraint(
            ["challenge_id"],
            ["approval_challenges.challenge_id"],
            name=op.f("fk_telegram_approval_bindings_challenge_id_approval_challenges"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["order_proposals.proposal_id"],
            name=op.f("fk_telegram_approval_bindings_proposal_id_order_proposals"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_approval_bindings")),
        sa.UniqueConstraint("challenge_id", name="uq_telegram_approval_bindings_challenge_id"),
        sa.UniqueConstraint(
            "environment",
            "chat_id",
            "message_id",
            name="uq_telegram_approval_bindings_message",
        ),
    )
    with op.batch_alter_table("telegram_approval_bindings", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_telegram_approval_bindings_proposal_id"),
            ["proposal_id"],
            unique=False,
        )

    op.create_table(
        "approval_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("outbox_id", sa.String(length=160), nullable=False),
        sa.Column("approval_event_id", sa.String(length=160), nullable=False),
        sa.Column("proposal_id", sa.String(length=160), nullable=False),
        sa.Column("order_hash", sa.String(length=71), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CONSUMED')",
            name=op.f("ck_approval_outbox_status"),
        ),
        sa.ForeignKeyConstraint(
            ["approval_event_id"],
            ["approval_events.event_id"],
            name=op.f("fk_approval_outbox_approval_event_id_approval_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["order_proposals.proposal_id"],
            name=op.f("fk_approval_outbox_proposal_id_order_proposals"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_outbox")),
        sa.UniqueConstraint("approval_event_id", name="uq_approval_outbox_approval_event_id"),
        sa.UniqueConstraint("outbox_id", name="uq_approval_outbox_outbox_id"),
    )
    with op.batch_alter_table("approval_outbox", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_approval_outbox_proposal_id"),
            ["proposal_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_approval_outbox_status_created", ["status", "created_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("approval_outbox", schema=None) as batch_op:
        batch_op.drop_index("ix_approval_outbox_status_created")
        batch_op.drop_index(batch_op.f("ix_approval_outbox_proposal_id"))
    op.drop_table("approval_outbox")
    with op.batch_alter_table("telegram_approval_bindings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_telegram_approval_bindings_proposal_id"))
    op.drop_table("telegram_approval_bindings")
