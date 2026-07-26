"""Database session and engine helpers.

Connection URLs are supplied by callers (tests, workers, CLI). This module does
not read secrets from the environment so credentials stay out of fixtures/logs.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ainvest.db.base import Base

DEFAULT_SQLITE_URL = "sqlite+pysqlite:///:memory:"


def create_db_engine(
    url: str = DEFAULT_SQLITE_URL,
    *,
    echo: bool = False,
    **engine_kwargs: Any,
) -> Engine:
    """Create a SQLAlchemy engine with SQLite foreign-key enforcement."""
    connect_args: dict[str, Any] = dict(engine_kwargs.pop("connect_args", {}) or {})
    if url.startswith("sqlite"):
        connect_args.setdefault("check_same_thread", False)

    engine = create_engine(url, echo=echo, connect_args=connect_args, **engine_kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def create_all_tables(engine: Engine) -> None:
    """Create all mapped tables (tests / bootstrap only; prefer Alembic)."""
    import ainvest.db.models  # noqa: F401

    Base.metadata.create_all(engine)


def drop_all_tables(engine: Engine) -> None:
    """Drop all mapped tables (tests only)."""
    import ainvest.db.models  # noqa: F401

    Base.metadata.drop_all(engine)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "DEFAULT_SQLITE_URL",
    "create_all_tables",
    "create_db_engine",
    "create_session_factory",
    "drop_all_tables",
    "session_scope",
]
