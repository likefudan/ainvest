"""Shared fixtures for db unit tests."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from memory_sqlite import iter_memory_session_factory  # noqa: E402


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    yield from iter_memory_session_factory()
