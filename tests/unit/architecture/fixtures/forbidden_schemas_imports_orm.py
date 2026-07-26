"""Intentionally invalid fixture: schemas must not import SQLAlchemy ORM.

Parsed by architecture unit tests only; never imported by production code.
"""

from sqlalchemy.orm import DeclarativeBase as _ForbiddenDeclarativeBase

_FIXTURE_SENTINEL = _ForbiddenDeclarativeBase
