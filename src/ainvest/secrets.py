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
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Protocol, Self, SupportsIndex, final

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


class _NonSerializable:
    """Block common accidental copies and serialized snapshots of secret holders."""

    __slots__ = ()

    def __copy__(self) -> Self:
        raise TypeError(f"{type(self).__name__} cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        del memo
        raise TypeError(f"{type(self).__name__} cannot be copied")

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError(f"{type(self).__name__} cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> str | tuple[Any, ...]:
        del protocol
        raise TypeError(f"{type(self).__name__} cannot be serialized")

    def __getstate__(self) -> object:
        raise TypeError(f"{type(self).__name__} cannot be serialized")


@final
class SecretRef(_NonSerializable):
    """Provider-neutral location for one logical secret.

    The provider reference is configuration, not secret material, but it is
    still hidden from representations to avoid disclosing infrastructure
    naming in logs and status responses.
    """

    __slots__ = ("__provider_reference", "__secret_id")
    __provider_reference: str
    __secret_id: SecretId

    def __init__(self, secret_id: SecretId, provider_reference: str) -> None:
        if not isinstance(secret_id, SecretId):
            raise ValueError("secret reference requires a known logical identifier")
        if not isinstance(provider_reference, str) or not _REFERENCE_PATTERN.fullmatch(
            provider_reference
        ):
            raise ValueError("secret provider reference must be a non-secret opaque identifier")
        object.__setattr__(self, "_SecretRef__secret_id", secret_id)
        object.__setattr__(self, "_SecretRef__provider_reference", provider_reference)

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("SecretRef is immutable")

    @property
    def secret_id(self) -> SecretId:
        return self.__secret_id

    @property
    def provider_reference(self) -> str:
        """Return the opaque reference to provider implementations only."""
        return self.__provider_reference

    def __repr__(self) -> str:
        return f"SecretRef(secret_id={self.secret_id.value!r}, provider_reference={REDACTED})"

    def __str__(self) -> str:
        return f"{self.secret_id.value}:{REDACTED}"


def _exact_secret_ref(value: object) -> SecretRef | None:
    """Return only the exact runtime reference type, never a subclass or duck type."""
    if type(value) is SecretRef:
        return value
    return None


@final
class SecretValue(_NonSerializable):
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


class ProviderSecretStatus(StrEnum):
    """Value-free result returned by a provider presence probe."""

    PRESENT = "present"
    MISSING = "missing"
    PERMISSION_DENIED = "permission_denied"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class SecretProviderError(RuntimeError):
    """Sanitized provider failure with no provider exception or secret value."""

    __slots__ = ("_status",)

    def __init__(self, status: object) -> None:
        safe_status = status if type(status) is ProviderSecretStatus else ProviderSecretStatus.ERROR
        self._status = safe_status
        super().__init__(f"secret provider access failed: {safe_status.value}")

    @property
    def status(self) -> ProviderSecretStatus:
        return self._status


class SecretProvider(Protocol):
    """Provider boundary implemented by development fakes and future adapters."""

    def probe(self, reference: SecretRef) -> ProviderSecretStatus:
        """Return presence/permission status without reading secret material."""

    def read(self, reference: SecretRef) -> SecretValue:
        """Read a secret or raise a sanitized :class:`SecretProviderError`."""


class MemorySecretProvider(_NonSerializable):
    """Deterministic mutable provider for tests and offline development."""

    __slots__ = ("_allowed", "_present", "_values")

    def __init__(
        self,
        values: Mapping[str, str] | None = None,
        *,
        allowed_references: Iterable[str] | None = None,
    ) -> None:
        self._values = {
            provider_reference: SecretValue(value)
            for provider_reference, value in (values or {}).items()
        }
        self._present = set(self._values)
        self._allowed = frozenset(
            self._values if allowed_references is None else allowed_references
        )

    def probe(self, reference: SecretRef) -> ProviderSecretStatus:
        if _exact_secret_ref(reference) is None:
            return ProviderSecretStatus.ERROR
        provider_reference = reference.provider_reference
        if provider_reference not in self._allowed:
            return ProviderSecretStatus.PERMISSION_DENIED
        if provider_reference not in self._present:
            return ProviderSecretStatus.MISSING
        return ProviderSecretStatus.PRESENT

    def read(self, reference: SecretRef) -> SecretValue:
        if _exact_secret_ref(reference) is None:
            raise SecretProviderError(ProviderSecretStatus.ERROR)
        status = self.probe(reference)
        if status is not ProviderSecretStatus.PRESENT:
            raise SecretProviderError(status)
        try:
            stored = self._values[reference.provider_reference]
            return SecretValue(stored.reveal())
        except Exception:
            raise SecretProviderError(ProviderSecretStatus.ERROR) from None

    def rotate(self, provider_reference: str, value: str) -> None:
        """Replace material behind an existing reference without code changes."""
        if provider_reference not in self._allowed:
            raise SecretProviderError(ProviderSecretStatus.PERMISSION_DENIED)
        self._values[provider_reference] = SecretValue(value)
        self._present.add(provider_reference)


class DevelopmentEnvironmentSecretProvider(_NonSerializable):
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
        if _exact_secret_ref(reference) is None:
            return ProviderSecretStatus.ERROR
        environment_key = self._bindings.get(reference.provider_reference)
        if environment_key is None:
            return ProviderSecretStatus.PERMISSION_DENIED
        if not any(candidate == environment_key for candidate in self._environment):
            return ProviderSecretStatus.MISSING
        return ProviderSecretStatus.PRESENT

    def read(self, reference: SecretRef) -> SecretValue:
        if _exact_secret_ref(reference) is None:
            raise SecretProviderError(ProviderSecretStatus.ERROR)
        status = self.probe(reference)
        if status is not ProviderSecretStatus.PRESENT:
            raise SecretProviderError(status)
        environment_key = self._bindings[reference.provider_reference]
        try:
            return SecretValue(self._environment[environment_key])
        except KeyError:
            raise SecretProviderError(ProviderSecretStatus.MISSING) from None
        except Exception:
            raise SecretProviderError(ProviderSecretStatus.ERROR) from None


class UnavailableProductionSecretProvider(_NonSerializable):
    """Fail-closed placeholder until an approved production provider exists."""

    __slots__ = ()

    def probe(self, reference: SecretRef) -> ProviderSecretStatus:
        if _exact_secret_ref(reference) is None:
            return ProviderSecretStatus.ERROR
        return ProviderSecretStatus.UNAVAILABLE

    def read(self, reference: SecretRef) -> SecretValue:
        if _exact_secret_ref(reference) is None:
            raise SecretProviderError(ProviderSecretStatus.ERROR)
        raise SecretProviderError(ProviderSecretStatus.UNAVAILABLE)


class SecretAccessStatus(StrEnum):
    """Metadata-only authorization and availability state."""

    AVAILABLE = "available"
    DENIED = "denied"
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


def _translate_provider_status(status: object) -> SecretAccessStatus:
    """Translate only exact known status singletons; hostile objects fail closed."""
    if status is ProviderSecretStatus.PRESENT:
        return SecretAccessStatus.AVAILABLE
    if status is ProviderSecretStatus.MISSING:
        return SecretAccessStatus.MISSING
    if status is ProviderSecretStatus.PERMISSION_DENIED:
        return SecretAccessStatus.PROVIDER_PERMISSION_DENIED
    if status is ProviderSecretStatus.UNAVAILABLE:
        return SecretAccessStatus.PROVIDER_UNAVAILABLE
    return SecretAccessStatus.PROVIDER_ERROR


def _translate_provider_exception(error: Exception) -> SecretAccessStatus:
    """Read an exact provider error defensively without retaining it."""
    if type(error) is not SecretProviderError:
        return SecretAccessStatus.PROVIDER_ERROR
    try:
        status: object = error.status
    except Exception:
        return SecretAccessStatus.PROVIDER_ERROR
    translated = _translate_provider_status(status)
    if translated is SecretAccessStatus.AVAILABLE:
        return SecretAccessStatus.PROVIDER_ERROR
    return translated


@final
class SecretAccessService(_NonSerializable):
    """Default-deny provider boundary permanently bound to one service role."""

    __slots__ = ("_provider", "_references", "_role")
    _provider: SecretProvider
    _references: Mapping[SecretId, SecretRef]
    _role: ServiceRole

    def __init__(
        self,
        role: ServiceRole | str,
        provider: SecretProvider,
        references: Mapping[SecretId, SecretRef],
    ) -> None:
        known_role = _known_role(role)
        if known_role is None:
            raise ValueError("secret access service requires a known role")
        for secret_id, reference in references.items():
            if _exact_secret_ref(reference) is None:
                raise ValueError("secret reference registry requires exact SecretRef values")
            if reference.secret_id is not secret_id:
                raise ValueError("secret reference identifier does not match registry key")
        allowed = DEFAULT_ROLE_GRANTS[known_role]
        object.__setattr__(self, "_role", known_role)
        object.__setattr__(self, "_provider", provider)
        object.__setattr__(
            self,
            "_references",
            MappingProxyType(
                {
                    secret_id: reference
                    for secret_id, reference in references.items()
                    if secret_id in allowed
                }
            ),
        )

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("SecretAccessService is immutable")

    @property
    def role(self) -> ServiceRole:
        """The immutable identity this boundary was constructed for."""
        return self._role

    def _authorize(
        self,
        secret_id: SecretId | str,
    ) -> tuple[SecretId | None, SecretAccessProbe | None]:
        known_secret = _known_secret(secret_id)
        if known_secret is None:
            return (
                None,
                SecretAccessProbe(
                    role=self._role.value,
                    secret_id="unknown",
                    status=SecretAccessStatus.UNKNOWN_SECRET,
                ),
            )
        if known_secret not in DEFAULT_ROLE_GRANTS[self._role]:
            return (
                known_secret,
                SecretAccessProbe(
                    role=self._role.value,
                    secret_id=known_secret.value,
                    status=SecretAccessStatus.DENIED,
                ),
            )
        if known_secret not in self._references:
            return (
                known_secret,
                SecretAccessProbe(
                    role=self._role.value,
                    secret_id=known_secret.value,
                    status=SecretAccessStatus.REFERENCE_UNCONFIGURED,
                ),
            )
        return known_secret, None

    def probe(self, secret_id: SecretId | str) -> SecretAccessProbe:
        """Check permission and presence without returning secret material."""
        known_secret, denied = self._authorize(secret_id)
        if denied is not None:
            return denied
        assert known_secret is not None
        reference = self._references[known_secret]
        try:
            status = self._provider.probe(reference)
        except Exception:
            translated = SecretAccessStatus.PROVIDER_ERROR
        else:
            translated = _translate_provider_status(status)
        return SecretAccessProbe(
            role=self._role.value,
            secret_id=known_secret.value,
            status=translated,
        )

    def get(self, secret_id: SecretId | str) -> SecretValue:
        """Return a value only after this boundary's role authorizes its logical ID."""
        known_secret, denied = self._authorize(secret_id)
        if denied is not None:
            raise SecretAccessError(denied)
        assert known_secret is not None
        reference = self._references[known_secret]
        failure: SecretAccessProbe | None = None
        value: object | None = None
        try:
            value = self._provider.read(reference)
        except Exception as provider_error:
            failure = SecretAccessProbe(
                self._role.value,
                known_secret.value,
                _translate_provider_exception(provider_error),
            )
        if failure is not None:
            # Raise outside the provider exception handler: no leaky provider
            # exception remains in ``__cause__`` or ``__context__``.
            raise SecretAccessError(failure)
        if type(value) is not SecretValue:
            raise SecretAccessError(
                SecretAccessProbe(
                    self._role.value,
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
