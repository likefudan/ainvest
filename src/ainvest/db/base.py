"""SQLAlchemy declarative base and shared mixins for ainvest persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Integer, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ainvest.db.types import UtcDateTime

# Stable naming convention keeps Alembic diffs deterministic across dialects.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all ainvest ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """UTC created/updated timestamps shared by mutable domain rows."""

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(),
        nullable=False,
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )


class VersionMixin:
    """Optimistic concurrency version column (starts at 1)."""

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SchemaVersionMixin:
    """Wire schema version retained on every persisted domain row."""

    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")


class CodeConfigVersionMixin:
    """Code and configuration versions for replay (design.md §3.6 / §9)."""

    code_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_version: Mapped[str | None] = mapped_column(String(64), nullable=True)


def utc_now() -> datetime:
    """Return the current timezone-aware UTC instant."""
    return datetime.now(UTC)


def require_mapping(payload: Any, *, label: str = "payload") -> dict[str, Any]:
    """Reject non-mapping JSON payloads at the persistence boundary."""
    if not isinstance(payload, dict):
        raise TypeError(f"{label} must be a JSON object mapping")
    return payload


__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "CodeConfigVersionMixin",
    "SchemaVersionMixin",
    "TimestampMixin",
    "VersionMixin",
    "require_mapping",
    "utc_now",
]
