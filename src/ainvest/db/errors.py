"""Persistence error types that avoid leaking database error text."""

from __future__ import annotations


class PersistenceError(Exception):
    """Base class for persistence failures."""

    def __init__(self, message: str, *, code: str = "PERSISTENCE_ERROR") -> None:
        self.code = code
        super().__init__(message)


class ConflictError(PersistenceError):
    """Raised when a unique/idempotency conflict cannot be resolved by re-read."""

    def __init__(self, message: str = "persistence conflict", *, code: str = "CONFLICT") -> None:
        super().__init__(message, code=code)


class ConcurrentModificationError(PersistenceError):
    """Raised when an optimistic version / conditional update races and loses."""

    def __init__(
        self,
        message: str = "concurrent modification",
        *,
        code: str = "CONCURRENT_MODIFICATION",
    ) -> None:
        super().__init__(message, code=code)


class NotFoundError(PersistenceError):
    """Raised when an expected row is missing."""

    def __init__(self, message: str = "not found", *, code: str = "NOT_FOUND") -> None:
        super().__init__(message, code=code)


__all__ = [
    "ConcurrentModificationError",
    "ConflictError",
    "NotFoundError",
    "PersistenceError",
]
