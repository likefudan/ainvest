"""Unit tests for Decimal/UTC column types."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ainvest.db.models import PortfolioSnapshotRow
from ainvest.db.types import DecimalString, UtcDateTime


@pytest.mark.unit
def test_decimal_string_rejects_float_bind() -> None:
    column = DecimalString()
    with pytest.raises(ValueError, match="binary floats"):
        column.process_bind_param(1.25, dialect=None)  # type: ignore[arg-type]


@pytest.mark.unit
def test_decimal_string_round_trip_canonical() -> None:
    column = DecimalString()
    bound = column.process_bind_param(Decimal("2.50"), dialect=None)
    assert bound == "2.5"
    restored = column.process_result_value(bound, dialect=None)
    assert restored == Decimal("2.5")


@pytest.mark.unit
def test_utc_datetime_rejects_naive() -> None:
    column = UtcDateTime()
    with pytest.raises(ValueError, match="naive"):
        column.process_bind_param(datetime(2026, 7, 24, 18, 30, 0), dialect=None)


@pytest.mark.unit
def test_portfolio_snapshot_persists_decimal_strings(
    session_factory: sessionmaker[Session],
) -> None:
    as_of = datetime(2026, 7, 24, 18, 30, 0, tzinfo=UTC)
    with session_factory() as session:
        session.add(
            PortfolioSnapshotRow(
                snapshot_id="psnap_01HZYTEST0000001",
                account_scope="paper",
                as_of=as_of,
                currency="USD",
                cash=Decimal("1000.00"),
                buying_power=Decimal("1000.00"),
                equity=Decimal("1000.00"),
                payload_json={"schema_version": "1.0"},
            )
        )
        session.commit()

    with session_factory() as session:
        row = session.scalar(
            select(PortfolioSnapshotRow).where(
                PortfolioSnapshotRow.snapshot_id == "psnap_01HZYTEST0000001"
            )
        )
        assert row is not None
        assert row.cash == Decimal("1000")
        assert isinstance(row.cash, Decimal)
        assert row.as_of.tzinfo is not None
