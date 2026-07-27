"""Shared fixtures for db unit tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from db.memory_sqlite import iter_memory_session_factory


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    yield from iter_memory_session_factory()
