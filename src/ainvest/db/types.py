"""SQLAlchemy column types that stay SQLite/PostgreSQL compatible.

Money and quantities are stored as canonical fixed-point decimal *strings*,
never binary floats. Timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import String, TypeDecorator
from sqlalchemy.types import DateTime

from ainvest.schemas.common import (
    enforce_bounded_decimal,
    format_canonical_decimal,
    parse_decimal,
)


class UtcDateTime(TypeDecorator[datetime]):
    """Persist timezone-aware UTC datetimes; reject naive values."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not allowed; use timezone-aware UTC")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class DecimalString(TypeDecorator[Decimal]):
    """Store bounded canonical decimals as fixed-point text (never FLOAT)."""

    impl = String(64)
    cache_ok = True

    def process_bind_param(self, value: Decimal | str | int | None, dialect: Any) -> str | None:
        del dialect
        if value is None:
            return None
        if isinstance(value, float):
            raise ValueError("binary floats are not allowed; use decimal strings")
        return format_canonical_decimal(parse_decimal(value))

    def process_result_value(self, value: str | None, dialect: Any) -> Decimal | None:
        del dialect
        if value is None:
            return None
        return enforce_bounded_decimal(parse_decimal(value))


__all__ = ["DecimalString", "UtcDateTime"]
