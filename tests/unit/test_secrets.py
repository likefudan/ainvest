"""Unit tests for the role-scoped secret provider boundary."""

from __future__ import annotations

import copy
import json
import pickle
from collections.abc import Mapping
from io import StringIO
from typing import Any

import pytest

from ainvest.audit import REDACTED, assert_no_plaintext_secrets, redact
from ainvest.observability import configure_logging, get_logger
from ainvest.secrets import (
    DEFAULT_ROLE_GRANTS,
    DevelopmentEnvironmentSecretProvider,
    MemorySecretProvider,
    ProviderSecretStatus,
    SecretAccessError,
    SecretAccessService,
    SecretAccessStatus,
    SecretId,
    SecretProviderError,
    SecretRef,
    SecretValue,
    ServiceRole,
    UnavailableProductionSecretProvider,
)

_REF_NAMES: Mapping[SecretId, str] = {
    secret_id: f"ainvest/test/{secret_id.value}" for secret_id in SecretId
}
_REFERENCES: Mapping[SecretId, SecretRef] = {
    secret_id: SecretRef(secret_id, provider_reference)
    for secret_id, provider_reference in _REF_NAMES.items()
}


def _sentinel(secret_id: SecretId, generation: int = 1) -> str:
    return f"synthetic-sentinel::{secret_id.value}::generation-{generation}"


def _provider_for_all() -> MemorySecretProvider:
    return MemorySecretProvider(
        {
            reference.provider_reference: _sentinel(secret_id)
            for secret_id, reference in _REFERENCES.items()
        },
        allowed_references=_REF_NAMES.values(),
    )


def _service(provider: object | None = None) -> SecretAccessService:
    return SecretAccessService(
        _provider_for_all() if provider is None else provider,  # type: ignore[arg-type]
        _REFERENCES,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("role", "secret_id"),
    [
        (role, secret_id)
        for role in ServiceRole
        for secret_id in SecretId
        if secret_id in DEFAULT_ROLE_GRANTS[role]
    ],
)
def test_every_granted_role_can_read_only_its_secret(
    role: ServiceRole,
    secret_id: SecretId,
) -> None:
    value = _service().get(role, secret_id)

    assert value.reveal() == _sentinel(secret_id)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("role", "secret_id"),
    [
        (role, secret_id)
        for role in ServiceRole
        for secret_id in SecretId
        if secret_id not in DEFAULT_ROLE_GRANTS[role]
    ],
)
def test_every_cross_role_access_is_denied_before_provider_call(
    role: ServiceRole,
    secret_id: SecretId,
) -> None:
    class ProviderMustNotRun:
        def probe(self, reference: SecretRef) -> ProviderSecretStatus:
            raise AssertionError(reference)

        def read(self, reference: SecretRef) -> SecretValue:
            raise AssertionError(reference)

    service = _service(ProviderMustNotRun())

    probe = service.probe(role, secret_id)
    assert probe.status is SecretAccessStatus.DENIED
    assert not probe.is_permitted
    with pytest.raises(SecretAccessError) as caught:
        service.get(role, secret_id)
    assert caught.value.probe.status is SecretAccessStatus.DENIED


@pytest.mark.unit
def test_research_alone_can_read_openai_and_read_broker_cannot_read_write_secret() -> None:
    service = _service()

    assert (
        service.probe(ServiceRole.RESEARCH, SecretId.OPENAI_API_KEY).status
        is SecretAccessStatus.AVAILABLE
    )
    for role in set(ServiceRole) - {ServiceRole.RESEARCH}:
        assert service.probe(role, SecretId.OPENAI_API_KEY).status is SecretAccessStatus.DENIED
    assert (
        service.probe(ServiceRole.READ_BROKER, SecretId.ROBINHOOD_READ_CREDENTIAL).status
        is SecretAccessStatus.AVAILABLE
    )
    assert (
        service.probe(ServiceRole.READ_BROKER, SecretId.ROBINHOOD_WRITE_CREDENTIAL).status
        is SecretAccessStatus.DENIED
    )


@pytest.mark.unit
def test_unknown_roles_and_secret_ids_are_value_free_and_default_deny() -> None:
    secret_like_unknown = "synthetic-unknown-material-that-must-not-echo"
    service = _service()

    role_probe = service.probe(secret_like_unknown, SecretId.OPENAI_API_KEY)
    secret_probe = service.probe(ServiceRole.RESEARCH, secret_like_unknown)

    assert role_probe.status is SecretAccessStatus.UNKNOWN_ROLE
    assert role_probe.role == "unknown"
    assert role_probe.secret_id == "unknown"
    assert secret_probe.status is SecretAccessStatus.UNKNOWN_SECRET
    assert secret_probe.secret_id == "unknown"
    with pytest.raises(SecretAccessError) as caught:
        service.get(secret_like_unknown, SecretId.OPENAI_API_KEY)
    assert secret_like_unknown not in str(caught.value)


@pytest.mark.unit
def test_unconfigured_reference_fails_before_provider_call() -> None:
    service = SecretAccessService(
        _provider_for_all(),
        {
            secret_id: reference
            for secret_id, reference in _REFERENCES.items()
            if secret_id is not SecretId.OPENAI_API_KEY
        },
    )

    assert (
        service.probe(ServiceRole.RESEARCH, SecretId.OPENAI_API_KEY).status
        is SecretAccessStatus.REFERENCE_UNCONFIGURED
    )
    with pytest.raises(SecretAccessError, match="reference_unconfigured"):
        service.get(ServiceRole.RESEARCH, SecretId.OPENAI_API_KEY)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (
            MemorySecretProvider(
                {},
                allowed_references={_REF_NAMES[SecretId.OPENAI_API_KEY]},
            ),
            SecretAccessStatus.MISSING,
        ),
        (MemorySecretProvider({}), SecretAccessStatus.PROVIDER_PERMISSION_DENIED),
        (UnavailableProductionSecretProvider(), SecretAccessStatus.PROVIDER_UNAVAILABLE),
    ],
)
def test_missing_permission_denied_and_unavailable_providers_fail_closed(
    provider: object,
    expected: SecretAccessStatus,
) -> None:
    service = _service(provider)

    assert service.probe(ServiceRole.RESEARCH, SecretId.OPENAI_API_KEY).status is expected
    with pytest.raises(SecretAccessError) as caught:
        service.get(ServiceRole.RESEARCH, SecretId.OPENAI_API_KEY)
    assert caught.value.probe.status is expected


@pytest.mark.unit
def test_reference_based_rotation_changes_value_without_service_or_reference_change() -> None:
    reference = _REFERENCES[SecretId.ROBINHOOD_READ_CREDENTIAL]
    provider = MemorySecretProvider(
        {reference.provider_reference: _sentinel(reference.secret_id, 1)},
        allowed_references={reference.provider_reference},
    )
    service = _service(provider)

    before = service.get(ServiceRole.READ_BROKER, reference.secret_id).reveal()
    provider.rotate(reference.provider_reference, _sentinel(reference.secret_id, 2))
    after = service.get(ServiceRole.READ_BROKER, reference.secret_id).reveal()

    assert before != after
    assert before.endswith("generation-1")
    assert after.endswith("generation-2")


@pytest.mark.unit
def test_secret_value_and_reference_never_render_plaintext_or_provider_location() -> None:
    plaintext = _sentinel(SecretId.OPENAI_API_KEY)
    provider_location = _REF_NAMES[SecretId.OPENAI_API_KEY]
    value = SecretValue(plaintext)
    reference = _REFERENCES[SecretId.OPENAI_API_KEY]

    rendered = (str(value), repr(value), str(reference), repr(reference))
    assert_no_plaintext_secrets(rendered, [plaintext, provider_location])
    assert all("REDACTED" in item for item in rendered)


@pytest.mark.unit
def test_secret_value_blocks_json_pickle_and_copy_serialization() -> None:
    value = SecretValue(_sentinel(SecretId.OPENAI_API_KEY))

    with pytest.raises(TypeError):
        json.dumps(value)
    with pytest.raises(TypeError, match="serialized"):
        pickle.dumps(value)
    with pytest.raises(TypeError, match="copied"):
        copy.copy(value)
    with pytest.raises(TypeError, match="copied"):
        copy.deepcopy(value)


@pytest.mark.unit
def test_audit_and_structured_logging_redact_secret_values() -> None:
    plaintext = _sentinel(SecretId.TELEGRAM_BOT_TOKEN)
    value = SecretValue(plaintext)

    audit_payload = redact({"bot_token": value, "safe": "status-only"})
    assert audit_payload["bot_token"] == REDACTED
    assert_no_plaintext_secrets(audit_payload, [plaintext])

    stream = StringIO()
    configure_logging(stream=stream, environment="test")
    get_logger("secret-test").info(
        "secret_access_checked",
        secret=value,
        access_status=SecretAccessStatus.AVAILABLE.value,
    )
    rendered = stream.getvalue()
    assert plaintext not in rendered
    assert "secret_access_checked" in rendered
    assert SecretAccessStatus.AVAILABLE.value in rendered


@pytest.mark.unit
def test_provider_failure_messages_and_values_are_not_propagated() -> None:
    plaintext = _sentinel(SecretId.OPENAI_API_KEY)

    class LeakyProvider:
        def probe(self, reference: SecretRef) -> ProviderSecretStatus:
            del reference
            raise RuntimeError(plaintext)

        def read(self, reference: SecretRef) -> SecretValue:
            del reference
            raise RuntimeError(plaintext)

    service = _service(LeakyProvider())

    probe = service.probe(ServiceRole.RESEARCH, SecretId.OPENAI_API_KEY)
    assert probe.status is SecretAccessStatus.PROVIDER_ERROR
    with pytest.raises(SecretAccessError) as caught:
        service.get(ServiceRole.RESEARCH, SecretId.OPENAI_API_KEY)
    assert caught.value.probe.status is SecretAccessStatus.PROVIDER_ERROR
    assert plaintext not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.unit
def test_provider_returning_raw_value_fails_closed() -> None:
    class InvalidProvider:
        def probe(self, reference: SecretRef) -> ProviderSecretStatus:
            del reference
            return ProviderSecretStatus.PRESENT

        def read(self, reference: SecretRef) -> Any:
            del reference
            return _sentinel(SecretId.OPENAI_API_KEY)

    with pytest.raises(SecretAccessError) as caught:
        _service(InvalidProvider()).get(ServiceRole.RESEARCH, SecretId.OPENAI_API_KEY)
    assert caught.value.probe.status is SecretAccessStatus.PROVIDER_ERROR


@pytest.mark.unit
def test_development_environment_provider_requires_explicit_safe_environment() -> None:
    provider_reference = _REF_NAMES[SecretId.OPENAI_API_KEY]
    environment_key = "AINVEST_TEST_RESEARCH_VALUE"
    environment = {environment_key: _sentinel(SecretId.OPENAI_API_KEY)}

    provider = DevelopmentEnvironmentSecretProvider(
        environment,
        bindings={provider_reference: environment_key},
        deployment_environment="development",
    )
    service = _service(provider)

    assert (
        service.get(ServiceRole.RESEARCH, SecretId.OPENAI_API_KEY).reveal()
        == environment[environment_key]
    )
    with pytest.raises(ValueError, match="outside development"):
        DevelopmentEnvironmentSecretProvider(
            environment,
            bindings={provider_reference: environment_key},
            deployment_environment="production",
        )


@pytest.mark.unit
def test_process_environment_access_requires_explicit_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_reference = _REF_NAMES[SecretId.OPENAI_API_KEY]
    environment_key = "AINVEST_TEST_EXPLICIT_DEVELOPMENT_VALUE"
    plaintext = _sentinel(SecretId.OPENAI_API_KEY)
    monkeypatch.setenv(environment_key, plaintext)

    provider = DevelopmentEnvironmentSecretProvider.from_process_environment(
        bindings={provider_reference: environment_key},
        deployment_environment="development",
    )

    assert (
        _service(provider).get(ServiceRole.RESEARCH, SecretId.OPENAI_API_KEY).reveal() == plaintext
    )


@pytest.mark.unit
def test_reference_registry_rejects_mismatched_logical_id() -> None:
    with pytest.raises(ValueError, match="does not match"):
        SecretAccessService(
            _provider_for_all(),
            {
                SecretId.OPENAI_API_KEY: SecretRef(
                    SecretId.TELEGRAM_BOT_TOKEN,
                    _REF_NAMES[SecretId.OPENAI_API_KEY],
                )
            },
        )


@pytest.mark.unit
def test_provider_errors_are_sanitized() -> None:
    error = SecretProviderError(ProviderSecretStatus.PERMISSION_DENIED)

    assert str(error) == "secret provider access failed: permission_denied"
    assert not error.__dict__.get("value")
