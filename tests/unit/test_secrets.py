"""Unit tests for the role-scoped secret provider boundary."""

from __future__ import annotations

import copy
import json
import pickle
from collections.abc import Iterator, Mapping
from dataclasses import asdict
from io import StringIO
from typing import Any, cast

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


def _service(
    role: ServiceRole = ServiceRole.RESEARCH,
    provider: Any | None = None,
    references: Mapping[SecretId, SecretRef] = _REFERENCES,
) -> SecretAccessService:
    return SecretAccessService(
        role,
        _provider_for_all() if provider is None else provider,
        references,
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
    service = _service(role)

    assert service.role is role
    assert service.get(secret_id).reveal() == _sentinel(secret_id)


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

    service = _service(role, ProviderMustNotRun())

    probe = service.probe(secret_id)
    assert probe.status is SecretAccessStatus.DENIED
    assert not probe.is_permitted
    with pytest.raises(SecretAccessError) as caught:
        service.get(secret_id)
    assert caught.value.probe.status is SecretAccessStatus.DENIED


@pytest.mark.unit
def test_role_is_bound_once_and_cannot_be_impersonated_per_call() -> None:
    service = _service(ServiceRole.READ_BROKER)

    assert service.probe(SecretId.ROBINHOOD_READ_CREDENTIAL).status is SecretAccessStatus.AVAILABLE
    assert service.probe(SecretId.ROBINHOOD_WRITE_CREDENTIAL).status is SecretAccessStatus.DENIED
    with pytest.raises(TypeError):
        service.get(  # type: ignore[call-arg]
            ServiceRole.WRITE_BROKER,
            SecretId.ROBINHOOD_WRITE_CREDENTIAL,
        )
    with pytest.raises(AttributeError, match="immutable"):
        service._role = ServiceRole.WRITE_BROKER
    assert service.role is ServiceRole.READ_BROKER


@pytest.mark.unit
def test_research_alone_can_construct_boundary_that_reads_openai() -> None:
    assert (
        _service(ServiceRole.RESEARCH).probe(SecretId.OPENAI_API_KEY).status
        is SecretAccessStatus.AVAILABLE
    )
    for role in set(ServiceRole) - {ServiceRole.RESEARCH}:
        assert _service(role).probe(SecretId.OPENAI_API_KEY).status is SecretAccessStatus.DENIED


@pytest.mark.unit
def test_unknown_role_and_secret_are_value_free_and_default_deny() -> None:
    secret_like_unknown = "synthetic-unknown-material-that-must-not-echo"

    with pytest.raises(ValueError) as invalid_role:
        _service(secret_like_unknown)  # type: ignore[arg-type]
    assert secret_like_unknown not in str(invalid_role.value)

    service = _service()
    probe = service.probe(secret_like_unknown)
    assert probe.status is SecretAccessStatus.UNKNOWN_SECRET
    assert probe.secret_id == "unknown"
    with pytest.raises(SecretAccessError) as caught:
        service.get(secret_like_unknown)
    assert secret_like_unknown not in str(caught.value)


@pytest.mark.unit
def test_unconfigured_role_reference_fails_before_provider_call() -> None:
    references: dict[SecretId, SecretRef] = {
        secret_id: reference
        for secret_id, reference in _REFERENCES.items()
        if secret_id is not SecretId.OPENAI_API_KEY
    }
    service = _service(references=references)

    assert (
        service.probe(SecretId.OPENAI_API_KEY).status is SecretAccessStatus.REFERENCE_UNCONFIGURED
    )
    with pytest.raises(SecretAccessError, match="reference_unconfigured"):
        service.get(SecretId.OPENAI_API_KEY)


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
    provider: Any,
    expected: SecretAccessStatus,
) -> None:
    service = _service(provider=provider)

    assert service.probe(SecretId.OPENAI_API_KEY).status is expected
    with pytest.raises(SecretAccessError) as caught:
        service.get(SecretId.OPENAI_API_KEY)
    assert caught.value.probe.status is expected


class _SpyMapping(Mapping[str, str]):
    def __init__(self, values: Mapping[str, str]) -> None:
        self._items = dict(values)
        self.getitem_calls = 0

    def __getitem__(self, key: str) -> str:
        self.getitem_calls += 1
        return self._items[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


@pytest.mark.unit
def test_development_probe_uses_key_metadata_without_fetching_or_truth_testing() -> None:
    secret_id = SecretId.OPENAI_API_KEY
    provider_reference = _REF_NAMES[secret_id]
    environment_key = "AINVEST_TEST_RESEARCH_VALUE"
    source = _SpyMapping({environment_key: _sentinel(secret_id)})
    provider = DevelopmentEnvironmentSecretProvider(
        source,
        bindings={provider_reference: environment_key},
        deployment_environment="development",
    )
    service = _service(provider=provider)

    assert service.probe(secret_id).status is SecretAccessStatus.AVAILABLE
    assert source.getitem_calls == 0
    assert service.get(secret_id).reveal() == _sentinel(secret_id)
    assert source.getitem_calls == 1


@pytest.mark.unit
def test_memory_probe_does_not_fetch_wrapped_value() -> None:
    secret_id = SecretId.OPENAI_API_KEY
    provider_reference = _REF_NAMES[secret_id]
    source = _SpyMapping({provider_reference: _sentinel(secret_id)})
    provider = MemorySecretProvider(source, allowed_references={provider_reference})
    source.getitem_calls = 0
    service = _service(provider=provider)

    assert service.probe(secret_id).status is SecretAccessStatus.AVAILABLE
    assert source.getitem_calls == 0


@pytest.mark.unit
def test_development_read_validates_empty_or_disappearing_values_without_leak() -> None:
    secret_id = SecretId.OPENAI_API_KEY
    provider_reference = _REF_NAMES[secret_id]
    environment_key = "AINVEST_TEST_RESEARCH_VALUE"
    source = _SpyMapping({environment_key: ""})
    provider = DevelopmentEnvironmentSecretProvider(
        source,
        bindings={provider_reference: environment_key},
        deployment_environment="development",
    )
    service = _service(provider=provider)

    assert service.probe(secret_id).status is SecretAccessStatus.AVAILABLE
    with pytest.raises(SecretAccessError) as empty:
        service.get(secret_id)
    assert empty.value.probe.status is SecretAccessStatus.PROVIDER_ERROR

    del source._items[environment_key]
    with pytest.raises(SecretAccessError) as missing:
        service.get(secret_id)
    assert missing.value.probe.status is SecretAccessStatus.MISSING


@pytest.mark.unit
def test_reference_based_rotation_changes_value_without_service_or_reference_change() -> None:
    reference = _REFERENCES[SecretId.ROBINHOOD_READ_CREDENTIAL]
    provider = MemorySecretProvider(
        {reference.provider_reference: _sentinel(reference.secret_id, 1)},
        allowed_references={reference.provider_reference},
    )
    service = _service(ServiceRole.READ_BROKER, provider)

    before = service.get(reference.secret_id).reveal()
    provider.rotate(reference.provider_reference, _sentinel(reference.secret_id, 2))
    after = service.get(reference.secret_id).reveal()

    assert before != after
    assert before.endswith("generation-1")
    assert after.endswith("generation-2")


@pytest.mark.unit
def test_secret_value_and_reference_never_render_sensitive_material() -> None:
    plaintext = _sentinel(SecretId.OPENAI_API_KEY)
    provider_location = _REF_NAMES[SecretId.OPENAI_API_KEY]
    value = SecretValue(plaintext)
    reference = _REFERENCES[SecretId.OPENAI_API_KEY]

    rendered = (str(value), repr(value), str(reference), repr(reference))
    assert_no_plaintext_secrets(rendered, [plaintext, provider_location])
    assert all("REDACTED" in item for item in rendered)


@pytest.mark.unit
@pytest.mark.parametrize(
    "holder",
    [
        SecretValue(_sentinel(SecretId.OPENAI_API_KEY)),
        _REFERENCES[SecretId.OPENAI_API_KEY],
    ],
)
def test_value_and_reference_block_json_pickle_and_copy(holder: object) -> None:
    with pytest.raises(TypeError):
        json.dumps(holder)
    with pytest.raises(TypeError, match="serialized"):
        pickle.dumps(holder)
    with pytest.raises(TypeError, match="copied"):
        copy.copy(holder)
    with pytest.raises(TypeError, match="copied"):
        copy.deepcopy(holder)
    if isinstance(holder, SecretRef):
        with pytest.raises(TypeError):
            asdict(holder)  # type: ignore[call-overload]
        with pytest.raises(TypeError):
            vars(holder)
        with pytest.raises(AttributeError, match="immutable"):
            holder.provider_reference = "ainvest/test/replacement"  # type: ignore[misc]


@pytest.mark.unit
def test_provider_holders_block_copy_pickle_and_state_exposure() -> None:
    plaintext = _sentinel(SecretId.OPENAI_API_KEY)
    provider_reference = _REF_NAMES[SecretId.OPENAI_API_KEY]
    environment_key = "AINVEST_TEST_RESEARCH_VALUE"
    providers = (
        MemorySecretProvider(
            {provider_reference: plaintext},
            allowed_references={provider_reference},
        ),
        DevelopmentEnvironmentSecretProvider(
            {environment_key: plaintext},
            bindings={provider_reference: environment_key},
            deployment_environment="development",
        ),
    )

    for provider in providers:
        assert plaintext not in repr(provider)
        with pytest.raises(TypeError, match="serialized"):
            pickle.dumps(provider)
        with pytest.raises(TypeError, match="copied"):
            copy.copy(provider)
        with pytest.raises(TypeError, match="copied"):
            copy.deepcopy(provider)
        with pytest.raises(TypeError, match="serialized"):
            provider.__getstate__()


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
def test_provider_failure_has_no_cause_context_or_plaintext_in_exception_log() -> None:
    plaintext = _sentinel(SecretId.OPENAI_API_KEY)

    class LeakyProvider:
        def probe(self, reference: SecretRef) -> ProviderSecretStatus:
            del reference
            raise RuntimeError(plaintext)

        def read(self, reference: SecretRef) -> SecretValue:
            del reference
            raise RuntimeError(plaintext)

    service = _service(provider=LeakyProvider())
    assert service.probe(SecretId.OPENAI_API_KEY).status is SecretAccessStatus.PROVIDER_ERROR

    stream = StringIO()
    configure_logging(stream=stream, environment="test")
    try:
        service.get(SecretId.OPENAI_API_KEY)
    except SecretAccessError as error:
        assert error.probe.status is SecretAccessStatus.PROVIDER_ERROR
        assert error.__cause__ is None
        assert error.__context__ is None
        get_logger("secret-test").exception("secret_provider_failed")
    else:
        pytest.fail("provider failure must fail closed")

    assert plaintext not in stream.getvalue()


@pytest.mark.unit
@pytest.mark.parametrize("hostile_status", [[], {"poisoned": "status"}, object()])
def test_hostile_unhashable_or_foreign_probe_status_fails_closed(
    hostile_status: object,
) -> None:
    class HostileStatusProvider:
        def probe(self, reference: SecretRef) -> Any:
            del reference
            return hostile_status

        def read(self, reference: SecretRef) -> SecretValue:
            del reference
            raise AssertionError("read is not part of this test")

    assert (
        _service(provider=HostileStatusProvider()).probe(SecretId.OPENAI_API_KEY).status
        is SecretAccessStatus.PROVIDER_ERROR
    )


@pytest.mark.unit
def test_poisoned_provider_error_status_and_message_are_not_observed() -> None:
    plaintext = _sentinel(SecretId.OPENAI_API_KEY)

    class PoisonedProviderError(SecretProviderError):
        @property
        def status(self) -> ProviderSecretStatus:
            raise RuntimeError(plaintext)

        def __str__(self) -> str:
            return plaintext

    class PoisonedProvider:
        def probe(self, reference: SecretRef) -> ProviderSecretStatus:
            del reference
            return ProviderSecretStatus.PRESENT

        def read(self, reference: SecretRef) -> SecretValue:
            del reference
            raise PoisonedProviderError(ProviderSecretStatus.MISSING)

    with pytest.raises(SecretAccessError) as caught:
        _service(provider=PoisonedProvider()).get(SecretId.OPENAI_API_KEY)

    assert caught.value.probe.status is SecretAccessStatus.PROVIDER_ERROR
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert plaintext not in str(caught.value)


@pytest.mark.unit
def test_exact_provider_error_with_mutated_unhashable_status_fails_closed() -> None:
    error = SecretProviderError(ProviderSecretStatus.MISSING)
    error._status = []  # type: ignore[assignment]

    class MutatedStatusProvider:
        def probe(self, reference: SecretRef) -> ProviderSecretStatus:
            del reference
            return ProviderSecretStatus.PRESENT

        def read(self, reference: SecretRef) -> SecretValue:
            del reference
            raise error

    with pytest.raises(SecretAccessError) as caught:
        _service(provider=MutatedStatusProvider()).get(SecretId.OPENAI_API_KEY)
    assert caught.value.probe.status is SecretAccessStatus.PROVIDER_ERROR


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
        _service(provider=InvalidProvider()).get(SecretId.OPENAI_API_KEY)
    assert caught.value.probe.status is SecretAccessStatus.PROVIDER_ERROR


@pytest.mark.unit
def test_provider_returning_hostile_secret_value_subclass_fails_closed() -> None:
    calls: list[str] = []

    class HostileSecretValue(SecretValue):  # type: ignore[misc]
        def reveal(self) -> str:
            calls.append("reveal")
            return _sentinel(SecretId.ROBINHOOD_WRITE_CREDENTIAL)

        def __repr__(self) -> str:
            calls.append("repr")
            return self.reveal()

        def __str__(self) -> str:
            calls.append("str")
            return self.reveal()

    class HostileValueProvider:
        def probe(self, reference: SecretRef) -> ProviderSecretStatus:
            del reference
            return ProviderSecretStatus.PRESENT

        def read(self, reference: SecretRef) -> SecretValue:
            del reference
            return HostileSecretValue(_sentinel(SecretId.OPENAI_API_KEY))

    with pytest.raises(SecretAccessError) as caught:
        _service(provider=HostileValueProvider()).get(SecretId.OPENAI_API_KEY)

    assert caught.value.probe.status is SecretAccessStatus.PROVIDER_ERROR
    assert calls == []


@pytest.mark.unit
def test_registry_and_providers_reject_hostile_secret_reference_runtime_types() -> None:
    calls: list[str] = []

    class HostileSecretRef(SecretRef):  # type: ignore[misc]
        @property
        def secret_id(self) -> SecretId:
            calls.append("secret_id")
            return SecretId.OPENAI_API_KEY

        @property
        def provider_reference(self) -> str:
            calls.append("provider_reference")
            return _sentinel(SecretId.ROBINHOOD_WRITE_CREDENTIAL)

    class DuckReference:
        @property
        def secret_id(self) -> SecretId:
            calls.append("duck_secret_id")
            raise AssertionError("duck reference must not be inspected")

        @property
        def provider_reference(self) -> str:
            calls.append("duck_provider_reference")
            raise AssertionError("duck reference must not be inspected")

    hostile = HostileSecretRef(
        SecretId.OPENAI_API_KEY,
        _REF_NAMES[SecretId.OPENAI_API_KEY],
    )
    duck = cast(SecretRef, DuckReference())

    for reference in (hostile, duck):
        with pytest.raises(ValueError, match="exact SecretRef"):
            _service(references={SecretId.OPENAI_API_KEY: reference})

        for provider in (
            _provider_for_all(),
            DevelopmentEnvironmentSecretProvider(
                {},
                bindings={},
                deployment_environment="development",
            ),
            UnavailableProductionSecretProvider(),
        ):
            assert provider.probe(reference) is ProviderSecretStatus.ERROR
            with pytest.raises(SecretProviderError) as caught:
                provider.read(reference)
            assert caught.value.status is ProviderSecretStatus.ERROR

    assert calls == []


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
    assert (
        _service(provider=provider).get(SecretId.OPENAI_API_KEY).reveal()
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

    assert _service(provider=provider).get(SecretId.OPENAI_API_KEY).reveal() == plaintext


@pytest.mark.unit
def test_reference_registry_rejects_mismatched_logical_id() -> None:
    with pytest.raises(ValueError, match="does not match"):
        _service(
            references={
                SecretId.OPENAI_API_KEY: SecretRef(
                    SecretId.TELEGRAM_BOT_TOKEN,
                    _REF_NAMES[SecretId.OPENAI_API_KEY],
                )
            }
        )


@pytest.mark.unit
def test_provider_errors_are_sanitized_at_construction() -> None:
    expected = SecretProviderError(ProviderSecretStatus.PERMISSION_DENIED)
    hostile = SecretProviderError(["unhashable", "status"])

    assert str(expected) == "secret provider access failed: permission_denied"
    assert expected.status is ProviderSecretStatus.PERMISSION_DENIED
    assert str(hostile) == "secret provider access failed: error"
    assert hostile.status is ProviderSecretStatus.ERROR
