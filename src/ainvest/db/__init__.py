"""Persistence package for ainvest (SQLAlchemy models, repositories, UoW).

ORM models live here only. Domain Pydantic schemas must not import this package.
Boundary packages may use repository / UnitOfWork interfaces but must not import
``ainvest.db.models`` or ``ainvest.db.orm``.
"""

from __future__ import annotations

from ainvest.db.errors import (
    ConcurrentModificationError,
    ConflictError,
    NotFoundError,
    PersistenceError,
)
from ainvest.db.session import (
    DEFAULT_SQLITE_URL,
    create_all_tables,
    create_db_engine,
    create_session_factory,
    drop_all_tables,
    session_scope,
)
from ainvest.db.uow import UnitOfWork, unit_of_work

__all__ = [
    "DEFAULT_SQLITE_URL",
    "ConcurrentModificationError",
    "ConflictError",
    "NotFoundError",
    "PersistenceError",
    "UnitOfWork",
    "create_all_tables",
    "create_db_engine",
    "create_session_factory",
    "drop_all_tables",
    "session_scope",
    "unit_of_work",
]
