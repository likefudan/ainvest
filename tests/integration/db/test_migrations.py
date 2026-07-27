"""Integration tests: Alembic upgrade/downgrade and SQLite persistence."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text

from ainvest.db.models import (
    OrderProposalRow,
    ResearchPacketRow,
    ResearchRunRow,
    RiskDecisionRow,
    TradeSignalRow,
)
from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory
from ainvest.db.types import DecimalString

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.integration
def test_alembic_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    url = f"sqlite:///{db_path}"
    env = {**os.environ, "ALEMBIC_DATABASE_URL": url}

    def run(*args: str) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0

    run("upgrade", "head")
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    expected = {
        "research_runs",
        "research_packets",
        "strategy_runs",
        "trade_signals",
        "risk_decisions",
        "order_proposals",
        "approval_challenges",
        "approval_events",
        "broker_orders",
        "broker_fills",
        "cancel_commands",
        "operator_actions",
        "portfolio_snapshots",
        "audit_events",
        "alembic_version",
    }
    assert expected <= tables

    run("downgrade", "base")
    engine = create_engine(url)
    remaining = set(inspect(engine).get_table_names())
    engine.dispose()
    assert "order_proposals" not in remaining

    run("upgrade", "head")
    engine = create_engine(url)
    restored = set(inspect(engine).get_table_names())
    engine.dispose()
    assert expected <= restored


@pytest.mark.integration
def test_sqlite_domain_round_trip(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'domain.db'}")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    as_of = datetime(2026, 7, 24, 18, 30, 0, tzinfo=UTC)

    with factory() as session:
        session.add(
            ResearchRunRow(
                run_id="rrun_01HZYTEST0000001",
                symbol="AAPL",
                status="COMPLETED",
                started_at=as_of,
                completed_at=as_of,
                payload_json={},
            )
        )
        session.add(
            ResearchPacketRow(
                research_id="res_01HZYTEST0000001",
                run_id="rrun_01HZYTEST0000001",
                symbol="AAPL",
                as_of=as_of,
                payload_json={"schema_version": "1.0"},
            )
        )
        session.add(
            TradeSignalRow(
                signal_id="sig_01HZYTEST0000001",
                research_id="res_01HZYTEST0000001",
                strategy="sma_crossover",
                strategy_version="1.2.0",
                symbol="AAPL",
                intent="BUY",
                strength=Decimal("0.73"),
                target_weight=Decimal("0.10"),
                generated_at=as_of,
                expires_at=as_of.replace(minute=45),
                payload_json={"schema_version": "1.0"},
            )
        )
        session.add(
            RiskDecisionRow(
                risk_decision_id="risk_01HZYTEST0000001",
                candidate_id="cand_01HZYTEST0000001",
                proposal_id="ordp_01HZYTEST0000001",
                outcome="APPROVED",
                decided_at=as_of,
                rule_set_version="1.0.0",
                reason_code="WITHIN_LIMITS",
                payload_json={"schema_version": "1.0"},
            )
        )
        session.add(
            OrderProposalRow(
                proposal_id="ordp_01HZYTEST0000001",
                signal_id="sig_01HZYTEST0000001",
                risk_decision_id="risk_01HZYTEST0000001",
                account_scope="paper",
                instrument_id="rh_inst_aapl_xnas",
                symbol="AAPL",
                side="BUY",
                quantity=Decimal("2"),
                limit_price=Decimal("214.50"),
                strategy="sma_crossover",
                strategy_version="1.2.0",
                order_hash="sha256:" + ("ab" * 32),
                status="PENDING_APPROVAL",
                proposal_created_at=as_of,
                expires_at=as_of.replace(minute=32),
                payload_json={"schema_version": "1.0"},
                version=1,
            )
        )
        session.commit()

    with factory() as session:
        proposal = session.scalar(
            select(OrderProposalRow).where(OrderProposalRow.proposal_id == "ordp_01HZYTEST0000001")
        )
        assert proposal is not None
        assert proposal.limit_price == Decimal("214.5")
        signal = session.scalar(
            select(TradeSignalRow).where(TradeSignalRow.signal_id == "sig_01HZYTEST0000001")
        )
        assert signal is not None
        assert signal.strength == Decimal("0.73")

    # PostgreSQL-compatible type surface: no FLOAT affinity for money columns.
    with engine.connect() as connection:
        rows = connection.execute(text("PRAGMA table_info(order_proposals)")).mappings().all()
    money_cols = {
        row["name"]: row["type"].upper()
        for row in rows
        if row["name"] in {"quantity", "limit_price"}
    }
    assert all(
        "FLOAT" not in col_type and "REAL" not in col_type for col_type in money_cols.values()
    )
    assert DecimalString().impl.length == 64
    engine.dispose()
