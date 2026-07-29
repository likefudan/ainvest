"""Role-scoped secret access with provider-neutral references.

This module deliberately does not select or configure a production secret
manager.  It defines the least-privilege boundary that provider integrations
must implement.  Callers receive a :class:`SecretValue` only after the logical
secret identifier is authorized for their service role.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Protocol, Self, SupportsIndex

from pydantic import SecretStr

from ainvest.audit.redact import REDACTED

_REFERENCE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$")


class ServiceRole(StrEnum):
    """Runtime identities with distinct secret scopes."""

    RESEARCH = "research"
    APPROVAL = "approval"
    READ_BROKER = "read_broker"
    WRITE_BROKER = "write_broker"


class SecretId(StrEnum):
    """Stable logical identifiers; none of these values is secret material."""

    OPENAI_API_KEY = "openai_api_key"
    DATA_PROVIDER_CREDENTIAL = "data_provider_credential"
    RESEARCH_DATABASE_CREDENTIAL = "research_database_credential"
    TELEGRAM_BOT_TOKEN = "telegram_bot_token"
    TELEGRAM_WEBHOOK_SECRET = "telegram_webhook_secret"
    WEBAUTHN_SERVER_SECRET = "webauthn_server_secret"
    APPROVAL_DATABASE_CREDENTIAL = "approval_database_credential"
    ROBINHOOD_READ_CREDENTIAL = "robinhood_read_credential"
    ROBINHOOD_WRITE_CREDENTIAL = "robinhood_write_credential"


DEFAULT_ROLE_GRANTS: Final[Mapping[ServiceRole, frozenset[SecretId]]] = MappingProxyType(
    {
        ServiceRole.RESEARCH: frozenset(
            {
                SecretId.OPENAI_API_KEY,
                SecretId.DATA_PROVIDER_CREDENTIAL,
                SecretId.RESEARCH_DATABASE_CREDENTIAL,
            }
        ),
        ServiceRole.APPROVAL: frozenset(
            {
                SecretId.TELEGRAM_BOT_TOKEN,
                SecretId.TELEGRAM_WEBHOOK_SECRET,
                SecretId.WEBAUTHN_SERVER_SECRET,
                SecretId.APPROVAL_DATABASE_CREDENTIAL,
            }
        ),
        ServiceRole.READ_BROKER: frozenset({SecretId.ROBINHOOD_READ_CREDENTIAL}),
        ServiceRole.WRITE_BROKER: frozenset({SecretId.ROBINHOOD_WRITE_CREDENTIAL}),
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class SecretRef:
    """Provider-neutral location for one logical secret.

    The provider reference is configuration, not secret material, but it is
    still hidden from representations to avoid disclosing infrastructure
    naming in logs and status responses.
    """

    secret_id: SecretId
    provider_reference: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.provider_reference, str) or not _REFERENCE_PATTERN.fullmatch(
            self.provider_reference
        ):
            raise ValueError("secret provider reference must be a non-secret opaque identifier")

    def __repr__(self) -> str:
        return f"SecretRef(secret_id={self.secret_id.value!r}, provider_reference={REDACTED})"

    def __str__(self) -> str:
        return f"{self.secret_id.value}:{REDACTED}"


class SecretValue:
    """Redacted wrapper whose plaintext requires an explicit ``reveal`` call.

    Secret values cannot be copied or pickled, and their string
    representations are always redacted.  Python cannot provide a perfect
    in-process secrecy boundary, so only authorized service code should retain
    an instance and it should do so for the shortest practical duration.
    """

    __slots__ = ("__secret",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("secret value must be a non-empty string")
        self.__secret = SecretStr(value)

    def reveal(self) -> str:
        """Return plaintext to the already-authorized service caller."""
        return self.__secret.get_secret_value()

    def __repr__(self) -> str:
        return f"SecretValue({REDACTED})"

    def __str__(self) -> str:
        return REDACTED

    def __copy__(self) -> Self:
        raise TypeError("SecretValue cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        del memo
        raise TypeError("SecretValue cannot be copied")

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("SecretValue cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> str | tuple[Any, ...]:
        del protocol
        raise TypeError("SecretValue cannot be serialized")

    def __getstate__(self) -> object:
        raise TypeError("SecretValue cannot be serialized")


class ProviderSecretStatus(StrEnum):
    """Value-free result returned by a provider presence probe."""

    PRESENT = "present"
    MISSING = "missing"
    PERMISSION_DENIED = "permission_denied"
    UNAVAILABLE = "unavailable"


class SecretProviderError(RuntimeError):
    """Sanitized provider failure with no provider exception or secret value."""

    def __init__(self, status: ProviderSecretStatus) -> None:
        self.status = status
        super().__init__(f"secret provider access failed: {status.value}")


class SecretProvider(Protocol):
    """Provider boundary implemented by development fakes and future adapters."""

    def probe(self, reference: SecretRef) -> ProviderSecretStatus:
        """Return presence/permission status without reading secret material."""

    def read(self, reference: SecretRef) -> SecretValue:
        """Read a secret or raise a sanitized :class:`SecretProviderError`."""


class MemorySecretProvider:
    """Deterministic mutable provider for tests and offline development."""

    __slots__ = ("_allowed", "_values")

    def __init__(
        self,
        values: Mapping[str, str] | None = None,
        *,
        allowed_references: Iterable[str] | None = None,
    ) -> None:
        self._values = dict(values or {})
        self._allowed = frozenset(
            self._values if allowed_references is None else allowed_references
        )

    def probe(self, reference: SecretRef) -> ProviderSecretStatus:
        provider_reference = reference.provider_reference
        if provider_reference not in self._allowed:
            return ProviderSecretStatus.PERMISSION_DENIED
        if not self._values.get(provider_reference):
            return ProviderSecretStatus.MISSING
        return ProviderSecretStatus.PRESENT

    def read(self, reference: SecretRef) -> SecretValue:
        status = self.probe(reference)
        if status is not ProviderSecretStatus.PRESENT:
            raise SecretProviderError(status)
        return SecretValue(self._values[reference.provider_reference])

    def rotate(self, provider_reference: str, value: str) -> None:
        """Replace material behind an existing reference without code changes."""
        if provider_reference not in self._allowed:
            raise SecretProviderError(ProviderSecretStatus.PERMISSION_DENIED)
        if not value:
            raise ValueError("secret value must be a non-empty string")
        self._values[provider_reference] = value


class DevelopmentEnvironmentSecretProvider:
    """Explicit development-only adapter over a supplied environment mapping.

    This provider never reads ``os.environ`` implicitly.  A caller that
    intentionally wants process-environment access must use
    :meth:`from_process_environment` and state ``environment="development"``.
    Existing configuration code remains responsible for explicit ``.env``
    parsing and precedence.
    """

    __slots__ = ("_bindings", "_environment")

    def __init__(
        self,
        environment: Mapping[str, str],
        *,
        bindings: Mapping[str, str],
        deployment_environment: str,
    ) -> None:
        if deployment_environment != "development":
            raise ValueError("development secret provider is unavailable outside development")
        self._environment = environment
        self._bindings = MappingProxyType(dict(bindings))

    @classmethod
    def from_process_environment(
        cls,
        *,
        bindings: Mapping[str, str],
        deployment_environment: str,
    ) -> DevelopmentEnvironmentSecretProvider:
        """Explicitly opt in to reading the development process environment."""
        return cls(
            os.environ,
            bindings=bindings,
            deployment_environment=deployment_environment,
        )

    def probe(self, reference: SecretRef) -> ProviderSecretStatus:
        environment_key = self._bindings.get(reference.provider_reference)
        if environment_key is None:
            return ProviderSecretStatus.PERMISSION_DENIED
        if not self._environment.get(environment_key):
            return ProviderSecretStatus.MISSING
        return ProviderSecretStatus.PRESENT

    def read(self, reference: SecretRef) -> SecretValue:
        status = self.probe(reference)
        if status is not ProviderSecretStatus.PRESENT:
            raise SecretProviderError(status)
        environment_key = self._bindings[reference.provider_reference]
        return SecretValue(self._environment[environment_key])


class UnavailableProductionSecretProvider:
    """Fail-closed placeholder until an approved production provider exists."""

    __slots__ = ()

    def probe(self, reference: SecretRef) -> ProviderSecretStatus:
        del reference
        return ProviderSecretStatus.UNAVAILABLE

    def read(self, reference: SecretRef) -> SecretValue:
        del reference
        raise SecretProviderError(ProviderSecretStatus.UNAVAILABLE)


class SecretAccessStatus(StrEnum):
    """Metadata-only authorization and availability state."""

    AVAILABLE = "available"
    DENIED = "denied"
    UNKNOWN_ROLE = "unknown_role"
    UNKNOWN_SECRET = "unknown_secret"
    REFERENCE_UNCONFIGURED = "reference_unconfigured"
    MISSING = "missing"
    PROVIDER_PERMISSION_DENIED = "provider_permission_denied"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True, slots=True)
class SecretAccessProbe:
    """Safe startup/access result containing no reference or secret value."""

    role: str
    secret_id: str
    status: SecretAccessStatus

    @property
    def is_available(self) -> bool:
        return self.status is SecretAccessStatus.AVAILABLE

    @property
    def is_permitted(self) -> bool:
        return self.status not in {
            SecretAccessStatus.DENIED,
            SecretAccessStatus.UNKNOWN_ROLE,
            SecretAccessStatus.UNKNOWN_SECRET,
        }


class SecretAccessError(PermissionError):
    """Safe access failure that exposes metadata only."""

    def __init__(self, probe: SecretAccessProbe) -> None:
        self.probe = probe
        super().__init__(
            "secret access failed "
            f"(role={probe.role}, secret_id={probe.secret_id}, status={probe.status.value})"
        )


def _known_role(value: ServiceRole | str) -> ServiceRole | None:
    try:
        return ServiceRole(value)
    except (TypeError, ValueError):
        return None


def _known_secret(value: SecretId | str) -> SecretId | None:
    try:
        return SecretId(value)
    except (TypeError, ValueError):
        return None


class SecretAccessService:
    """Default-deny role authorization in front of a secret provider."""

    __slots__ = ("_provider", "_references")

    def __init__(
        self,
        provider: SecretProvider,
        references: Mapping[SecretId, SecretRef],
    ) -> None:
        for secret_id, reference in references.items():
            if reference.secret_id is not secret_id:
                raise ValueError("secret reference identifier does not match registry key")
        self._provider = provider
        self._references = MappingProxyType(dict(references))

    def _authorize(
        self,
        role: ServiceRole | str,
        secret_id: SecretId | str,
    ) -> tuple[ServiceRole | None, SecretId | None, SecretAccessProbe | None]:
        known_role = _known_role(role)
        if known_role is None:
            return (
                None,
                None,
                SecretAccessProbe(
                    role="unknown",
                    secret_id="unknown",
                    status=SecretAccessStatus.UNKNOWN_ROLE,
                ),
            )
        known_secret = _known_secret(secret_id)
        if known_secret is None:
            return (
                known_role,
                None,
                SecretAccessProbe(
                    role=known_role.value,
                    secret_id="unknown",
                    status=SecretAccessStatus.UNKNOWN_SECRET,
                ),
            )
        if known_secret not in DEFAULT_ROLE_GRANTS[known_role]:
            return (
                known_role,
                known_secret,
                SecretAccessProbe(
                    role=known_role.value,
                    secret_id=known_secret.value,
                    status=SecretAccessStatus.DENIED,
                ),
            )
        if known_secret not in self._references:
            return (
                known_role,
                known_secret,
                SecretAccessProbe(
                    role=known_role.value,
                    secret_id=known_secret.value,
                    status=SecretAccessStatus.REFERENCE_UNCONFIGURED,
                ),
            )
        return known_role, known_secret, None

    def probe(
        self,
        role: ServiceRole | str,
        secret_id: SecretId | str,
    ) -> SecretAccessProbe:
        """Check permission and presence without returning secret material."""
        known_role, known_secret, denied = self._authorize(role, secret_id)
        if denied is not None:
            return denied
        assert known_role is not None
        assert known_secret is not None
        reference = self._references[known_secret]
        try:
            status = self._provider.probe(reference)
        except Exception:
            status = None
        if status is None:
            translated = SecretAccessStatus.PROVIDER_ERROR
        else:
            translated = {
                ProviderSecretStatus.PRESENT: SecretAccessStatus.AVAILABLE,
                ProviderSecretStatus.MISSING: SecretAccessStatus.MISSING,
                ProviderSecretStatus.PERMISSION_DENIED: (
                    SecretAccessStatus.PROVIDER_PERMISSION_DENIED
                ),
                ProviderSecretStatus.UNAVAILABLE: SecretAccessStatus.PROVIDER_UNAVAILABLE,
            }.get(status, SecretAccessStatus.PROVIDER_ERROR)
        return SecretAccessProbe(
            role=known_role.value,
            secret_id=known_secret.value,
            status=translated,
        )

    def get(
        self,
        role: ServiceRole | str,
        secret_id: SecretId | str,
    ) -> SecretValue:
        """Return a value only after the role policy authorizes the logical ID."""
        known_role, known_secret, denied = self._authorize(role, secret_id)
        if denied is not None:
            raise SecretAccessError(denied)
        assert known_role is not None
        assert known_secret is not None
        reference = self._references[known_secret]
        try:
            value = self._provider.read(reference)
        except SecretProviderError as exc:
            status = {
                ProviderSecretStatus.MISSING: SecretAccessStatus.MISSING,
                ProviderSecretStatus.PERMISSION_DENIED: (
                    SecretAccessStatus.PROVIDER_PERMISSION_DENIED
                ),
                ProviderSecretStatus.UNAVAILABLE: SecretAccessStatus.PROVIDER_UNAVAILABLE,
            }.get(exc.status, SecretAccessStatus.PROVIDER_ERROR)
            raise SecretAccessError(
                SecretAccessProbe(known_role.value, known_secret.value, status)
            ) from None
        except Exception:
            raise SecretAccessError(
                SecretAccessProbe(
                    known_role.value,
                    known_secret.value,
                    SecretAccessStatus.PROVIDER_ERROR,
                )
            ) from None
        if not isinstance(value, SecretValue):
            raise SecretAccessError(
                SecretAccessProbe(
                    known_role.value,
                    known_secret.value,
                    SecretAccessStatus.PROVIDER_ERROR,
                )
            )
        return value


__all__ = [
    "DEFAULT_ROLE_GRANTS",
    "DevelopmentEnvironmentSecretProvider",
    "MemorySecretProvider",
    "ProviderSecretStatus",
    "SecretAccessError",
    "SecretAccessProbe",
    "SecretAccessService",
    "SecretAccessStatus",
    "SecretId",
    "SecretProvider",
    "SecretProviderError",
    "SecretRef",
    "SecretValue",
    "ServiceRole",
    "UnavailableProductionSecretProvider",
]
