"""telegram update ingress

Revision ID: bf42c70e30d1
Revises: ec71aaa3381a
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bf42c70e30d1"
down_revision: str | None = "ec71aaa3381a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_poll_states",
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("next_offset", sa.BigInteger(), nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name=op.f("ck_telegram_poll_states_environment"),
        ),
        sa.CheckConstraint(
            "next_offset >= 0 AND next_offset <= 9223372036854775807",
            name=op.f("ck_telegram_poll_states_next_offset_range"),
        ),
        sa.CheckConstraint(
            "lease_epoch >= 0 AND lease_epoch <= 9223372036854775807",
            name=op.f("ck_telegram_poll_states_lease_epoch_range"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_telegram_poll_states_version_positive")),
        sa.CheckConstraint(
            "lease_owner IS NULL OR (length(lease_owner) >= 1 AND length(lease_owner) <= 64)",
            name=op.f("ck_telegram_poll_states_lease_owner_length"),
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name=op.f("ck_telegram_poll_states_lease_fields_together"),
        ),
        sa.PrimaryKeyConstraint("environment", name=op.f("pk_telegram_poll_states")),
    )
    op.create_table(
        "telegram_processed_updates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("callback_query_digest", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name=op.f("ck_telegram_processed_updates_environment"),
        ),
        sa.CheckConstraint(
            "update_id >= 0 AND update_id <= 9223372036854775806",
            name=op.f("ck_telegram_processed_updates_update_id_range"),
        ),
        sa.CheckConstraint(
            "kind IN ('callback', 'text', 'ignored')",
            name=op.f("ck_telegram_processed_updates_kind"),
        ),
        sa.CheckConstraint(
            "disposition IN ('handled', 'ignored', 'duplicate_callback')",
            name=op.f("ck_telegram_processed_updates_disposition"),
        ),
        sa.CheckConstraint(
            "callback_query_digest IS NULL OR length(callback_query_digest) = 64",
            name=op.f("ck_telegram_processed_updates_callback_digest_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_processed_updates")),
        sa.UniqueConstraint(
            "environment",
            "callback_query_digest",
            name="uq_telegram_update_environment_callback_digest",
        ),
        sa.UniqueConstraint("environment", "update_id", name="uq_telegram_update_environment_id"),
    )


def downgrade() -> None:
    op.drop_table("telegram_processed_updates")
    op.drop_table("telegram_poll_states")
