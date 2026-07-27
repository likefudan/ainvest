"""Reuse the in-memory SQLite session_factory from db unit tests."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

_DB_DIR = Path(__file__).resolve().parents[1] / "db"
if str(_DB_DIR) not in sys.path:
    sys.path.insert(0, str(_DB_DIR))

from memory_sqlite import iter_memory_session_factory  # noqa: E402


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    yield from iter_memory_session_factory()
