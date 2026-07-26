"""Versioned Pydantic domain contracts shared across packages.

Schemas are the shared dependency foundation. They must not import other
``ainvest`` boundary packages, and must not import SQLAlchemy ORM APIs.
Domain models stay separate from persistence/ORM models.
"""

__all__: list[str] = []
