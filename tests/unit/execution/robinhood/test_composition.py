"""`open_read_gateway`: the only place the recomputed pin is consumed.

`tests/contract/execution/test_rh_mcp_manifest_contract.py` proves
``EXPECTED_MANIFEST_DIGEST`` is the digest of the reviewed `v0.2.0` manifest.
That proof is worth nothing if the runtime path passes a different value to
``GatewayConfig``, and until this file existed nothing executed that path:
replacing the pin at the call site with sixty-four zeroes left the whole suite
green. A recomputed constant that no test watches being *used* is still prose.

So these tests drive :func:`~ainvest.execution.robinhood.composition.open_read_gateway`
end to end against a fake published surface, and assert on the value the
surface actually received rather than on the constant it was supposed to come
from.

No credential, token, account value, or network is involved: the artifact
probe and the published surface are both injected.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from typing import Any

import pytest

from ainvest.execution.robinhood.artifact import InstalledDistribution
from ainvest.execution.robinhood.composition import (
    ComposedReadGateway,
    import_published_surface,
    open_read_gateway,
)
from ainvest.execution.robinhood.errors import GatewayReadError, GatewayReadErrorCode
from ainvest.execution.robinhood.pins import (
    EXPECTED_MANIFEST_DIGEST,
    PINNED_PACKAGE_VERSION,
    PINNED_WHEEL_FILENAME,
    PINNED_WHEEL_SHA256,
    REJECTED_CHANGELOG_MANIFEST_DIGEST,
)
from execution.robinhood.gateway_fakes import (
    FakeGateway,
    RecordingSink,
    manifest_capabilities,
    readiness_document,
    run,
)


@dataclass(slots=True)
class FakeSurface:
    """A stand-in for `rh-mcp`'s two published callables.

    Records what it was handed, which is the point: the assertions below are
    about the argument the gateway would really have been configured with.
    """

    gateway: FakeGateway = field(default_factory=FakeGateway)
    configs: list[dict[str, Any]] = field(default_factory=list)
    opened: list[object] = field(default_factory=list)
    closed: int = 0

    def gateway_config(self, *, expected_manifest_digest: str, **options: Any) -> object:
        record = {"expected_manifest_digest": expected_manifest_digest, **options}
        self.configs.append(record)
        return record

    def open_gateway(self, config: object) -> Any:
        @asynccontextmanager
        async def _opened() -> AsyncIterator[FakeGateway]:
            self.opened.append(config)
            try:
                yield self.gateway
            finally:
                self.closed += 1

        return _opened()


def installed_probe() -> InstalledDistribution | None:
    """An installation that satisfies every artifact pin.

    An immutable archive install recording the pinned wheel's SHA-256, which is
    the only shape :func:`verify_installed_artifact` accepts.
    """
    return InstalledDistribution(
        version=PINNED_PACKAGE_VERSION,
        direct_url={
            "url": f"https://github.com/likefudan/rh-mcp/releases/download/v0.2.0/{PINNED_WHEEL_FILENAME}",
            "archive_info": {"hash": f"sha256={PINNED_WHEEL_SHA256}"},
        },
    )


def absent_probe() -> InstalledDistribution | None:
    return None


async def _open(**kwargs: Any) -> tuple[ComposedReadGateway, FakeSurface]:
    surface = kwargs.pop("surface", None) or FakeSurface()
    async with open_read_gateway(surface=surface, probe=installed_probe, **kwargs) as composed:
        return composed, surface


# ---------------------------------------------------------------------------
# The pin, at the point it is consumed
# ---------------------------------------------------------------------------


def test_the_gateway_is_configured_with_the_recomputed_pin() -> None:
    """The contract test proves the constant; this proves the constant is used."""
    _, surface = run(_open())

    assert len(surface.configs) == 1
    assert surface.configs[0]["expected_manifest_digest"] == EXPECTED_MANIFEST_DIGEST
    # The literal too. Comparing the call argument against the same constant
    # the call site reads would still pass if both moved together, which is
    # precisely the mutation this file exists to catch.
    assert surface.configs[0]["expected_manifest_digest"] == (
        "sha256:70f88615716b05b8f547bf21ba756643ba2ded140202395998d428f63d84c91b"
    )


def test_the_configured_pin_is_never_the_changelog_digest() -> None:
    """`rh-mcp`'s CHANGELOG prints a digest belonging to another manifest."""
    _, surface = run(_open())
    rejected: str = REJECTED_CHANGELOG_MANIFEST_DIGEST
    assert surface.configs[0]["expected_manifest_digest"] != rejected


def test_deployment_options_cannot_override_the_pin() -> None:
    """A deployment that could change this could change the permission set."""
    surface = FakeSurface()

    with pytest.raises(GatewayReadError) as caught:
        run(
            _open(
                surface=surface,
                config_options={"expected_manifest_digest": REJECTED_CHANGELOG_MANIFEST_DIGEST},
            )
        )

    assert caught.value.code is GatewayReadErrorCode.DEPENDENCY_UNAVAILABLE
    assert caught.value.rejection == "expected_manifest_digest_override"
    # Refused before anything was built, not repaired afterwards.
    assert surface.configs == []
    assert surface.opened == []


def test_other_deployment_options_are_passed_through() -> None:
    """The override guard must reject one key, not disable the parameter."""
    _, surface = run(_open(config_options={"mode": "read_broker", "namespace": "ainvest"}))

    assert surface.configs[0]["mode"] == "read_broker"
    assert surface.configs[0]["namespace"] == "ainvest"
    assert surface.configs[0]["expected_manifest_digest"] == EXPECTED_MANIFEST_DIGEST


# ---------------------------------------------------------------------------
# Ordering — the security property of this function
# ---------------------------------------------------------------------------


def test_a_failed_artifact_check_opens_no_gateway() -> None:
    """Verify the installed artifact *before* building anything."""
    surface = FakeSurface()

    with pytest.raises(GatewayReadError) as caught:
        run(_open_with_probe(surface, absent_probe))

    assert caught.value.code is GatewayReadErrorCode.DEPENDENCY_UNAVAILABLE
    assert surface.configs == []
    assert surface.opened == []


async def _open_with_probe(surface: FakeSurface, probe: Any) -> ComposedReadGateway:
    async with open_read_gateway(surface=surface, probe=probe) as composed:
        return composed


def test_startup_verification_runs_before_the_caller_gets_the_client() -> None:
    """Reads are refused until the projection and readiness have been proven."""
    surface = FakeSurface()
    composed, _ = run(_open(surface=surface))

    assert surface.gateway.readiness_calls == 1
    assert composed.client.startup is not None
    assert composed.artifact.verified is True


def test_a_failed_readiness_check_propagates_and_yields_nothing() -> None:
    """A gateway reporting the wrong digest is refused, not consumed."""
    surface = FakeSurface(
        gateway=FakeGateway(
            readiness_result=readiness_document(
                manifest_digest=REJECTED_CHANGELOG_MANIFEST_DIGEST,
                expected_manifest_digest=REJECTED_CHANGELOG_MANIFEST_DIGEST,
            )
        )
    )

    with pytest.raises(GatewayReadError):
        run(_open(surface=surface))

    assert surface.closed == 1, "the gateway context must still be exited"


def test_a_mutating_capability_in_the_listing_stops_composition() -> None:
    """The projection check is part of opening, not something a caller may skip."""
    # Replace one listed read with a mutating entry of the same name, which is
    # the drift `rh-mcp` would not stop: it is still an *allowed* capability.
    listing = list(manifest_capabilities())
    listing[0] = type(listing[0])(capability=listing[0].capability, read_allowed=True, mutates=True)
    surface = FakeSurface(gateway=FakeGateway(listing=tuple(listing)))

    with pytest.raises(GatewayReadError):
        run(_open(surface=surface))

    assert surface.closed == 1


def test_the_gateway_context_is_exited_on_the_happy_path() -> None:
    surface = FakeSurface()
    run(_open(surface=surface))
    assert surface.closed == 1
    assert surface.opened == surface.configs


# ---------------------------------------------------------------------------
# Fail-closed while the dependency is absent
# ---------------------------------------------------------------------------


def test_importing_the_published_surface_fails_closed_today() -> None:
    """The runtime dependency is a separate reviewed envelope.

    This must be a real refusal, not a no-op that returns something usable
    when the package is missing.
    """
    with pytest.raises(GatewayReadError) as caught:
        import_published_surface()

    assert caught.value.code is GatewayReadErrorCode.DEPENDENCY_UNAVAILABLE
    assert caught.value.rejection == "published_surface_unimportable"
    # The chained ImportError names file system paths and is displayed.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None or caught.value.__suppress_context__


def test_no_surface_and_no_dependency_refuses_rather_than_defaulting() -> None:
    """Omitting ``surface`` must reach the real import and fail, not fall back."""

    async def _run() -> None:
        async with open_read_gateway(probe=installed_probe):
            pass

    with pytest.raises(GatewayReadError) as caught:
        run(_run())

    assert caught.value.rejection == "published_surface_unimportable"


# ---------------------------------------------------------------------------
# Disclosure
# ---------------------------------------------------------------------------


def test_composition_logs_disclose_no_configuration_values() -> None:
    """Config carries deployment settings; logs must not echo them back."""
    sink = RecordingSink()
    run(_open(config_options={"namespace": "ainvest-read-broker"}, log_sink=sink))

    emitted = repr(sink.records)
    assert "ainvest-read-broker" not in emitted
    assert PINNED_WHEEL_SHA256 not in emitted


def test_the_composed_result_exposes_no_gateway_internals() -> None:
    """What a caller receives is the adapter and a verdict — nothing else."""
    composed, _ = run(_open())

    # A slots dataclass, so `vars()` does not apply; ask the dataclass itself.
    assert {field.name for field in dataclass_fields(ComposedReadGateway)} == {
        "client",
        "artifact",
    }
    for forbidden in ("gateway", "session", "transport", "token", "config", "credential"):
        assert not hasattr(composed, forbidden)
