"""Shared in-memory SQLite session factory for unit tests."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session, sessionmaker

from ainvest.db.session import create_all_tables, create_db_engine, create_session_factory


def iter_memory_session_factory() -> Iterator[sessionmaker[Session]]:
    """Yield a session factory backed by a fresh in-memory SQLite database."""
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all_tables(engine)
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()
