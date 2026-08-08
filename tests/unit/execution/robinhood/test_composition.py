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
probe and the published surface are both injected for those tests.

The last group of tests uses the **installed** `rh-mcp` wheel instead, to
check that the two published callables really take the arguments this adapter
passes them. That makes this file require the ``broker`` profile —
``./scripts/dev setup``, ``./scripts/dev broker-install``, or
``./scripts/dev verify``, which installs it — and without it the module fails
to import rather than quietly skipping. Those tests still open nothing: they
stop at ``GatewayConfig`` construction and at ``inspect.signature``, so no
authorization, credential or socket is involved here either.
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from typing import Any

import pytest

# The installed wheel, imported here so the assertions below compare against
# `rh-mcp`'s own objects rather than against a description of them. `rh-mcp`
# `v0.2.0` ships no `py.typed`, so mypy sees these as untyped; the adapter's
# typed boundary is `PublishedSurface`, not these modules.
import rh_mcp.config  # type: ignore[import-untyped]
import rh_mcp.gateway  # type: ignore[import-untyped]

from ainvest.execution.robinhood.artifact import InstalledDistribution
from ainvest.execution.robinhood.composition import (
    ComposedReadGateway,
    _RhMcpSurface,
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
    """Reads are refused until the projection and readiness have been proven.

    Asserted **inside** the ``async with`` body. Checking after the block does
    not test this name: ``asynccontextmanager.__aexit__`` resumes the generator
    on normal exit, so moving ``verify_startup()`` below the ``yield`` still
    runs it — during teardown — and assertions evaluated afterwards see the
    verified state either way. That mutation survived until this was moved.
    """
    surface = FakeSurface()

    async def _observe_inside_the_block() -> None:
        async with open_read_gateway(surface=surface, probe=installed_probe) as composed:
            # The caller holds the client here; the guarantee is that startup
            # has already happened at this instant, not by the time we return.
            assert surface.gateway.readiness_calls == 1
            assert composed.client.startup is not None
            assert composed.artifact.verified is True

    run(_observe_inside_the_block())


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
# The real published surface, now that the dependency is installed
#
# Everything above injects a fake surface, which is the only way to drive the
# composition without a live authorization. These four exercise the import
# itself, against the installed `rh-mcp` wheel. They stop at construction:
# nothing here calls ``open_gateway``, which would open a provider session.
# ---------------------------------------------------------------------------


def test_importing_the_published_surface_returns_the_installed_rh_mcp_names() -> None:
    """The deferred import resolves, and resolves to `rh-mcp`'s own objects.

    Object identity against the installed modules, not truthiness: a surface
    built from anything else — a stub, a re-export, a shim this repository
    wrote — would satisfy "it returned something". Reading the adapter's two
    private fields is the point of the test rather than a shortcut around it;
    what is being asserted is precisely which objects it is holding.
    """
    surface = import_published_surface()

    assert isinstance(surface, _RhMcpSurface)
    assert surface._gateway_config is rh_mcp.config.GatewayConfig
    assert surface._open_gateway is rh_mcp.gateway.open_gateway


def test_the_installed_gateway_config_takes_the_digest_this_adapter_passes() -> None:
    """`composition.py` calls ``GatewayConfig(expected_manifest_digest=...)``.

    Until the wheel was installed, nothing checked that the published callable
    accepts that keyword — the fake surface accepts whatever it is given. A
    renamed or repositioned parameter upstream would have been found at the
    first live startup instead of here.
    """
    parameters = inspect.signature(rh_mcp.config.GatewayConfig).parameters

    assert "expected_manifest_digest" in parameters
    assert parameters["expected_manifest_digest"].default is inspect.Parameter.empty
    assert parameters["expected_manifest_digest"].kind is not inspect.Parameter.POSITIONAL_ONLY

    configured = import_published_surface().gateway_config(
        expected_manifest_digest=EXPECTED_MANIFEST_DIGEST
    )
    assert getattr(configured, "expected_manifest_digest", None) == EXPECTED_MANIFEST_DIGEST


def test_the_installed_open_gateway_takes_the_config_as_its_only_argument() -> None:
    """`composition.py` calls ``open_gateway(config)`` positionally.

    ``store``, ``manifest`` and ``transport`` are `rh-mcp`'s injection points
    and are keyword-only with defaults; the adapter passes none of them, and
    a production caller must not. Checked as a signature fact rather than by
    calling it, which would open a provider session.
    """
    parameters = inspect.signature(rh_mcp.gateway.open_gateway).parameters
    positional = [
        name
        for name, parameter in parameters.items()
        if parameter.kind
        in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]

    assert positional == ["config"]
    assert all(
        parameter.default is not inspect.Parameter.empty
        for name, parameter in parameters.items()
        if name != "config"
    )


def test_the_published_surface_still_fails_closed_when_it_cannot_be_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-broker deployment reaches this, and it must be a real refusal.

    ``None`` in ``sys.modules`` is the documented way to make an import raise
    ``ImportError`` without unloading a package, so the deferred import inside
    :func:`import_published_surface` takes exactly the branch a missing wheel
    would take.
    """
    monkeypatch.setitem(sys.modules, "rh_mcp.config", None)

    with pytest.raises(GatewayReadError) as caught:
        import_published_surface()

    assert caught.value.code is GatewayReadErrorCode.DEPENDENCY_UNAVAILABLE
    assert caught.value.rejection == "published_surface_unimportable"
    # The chained ImportError names file system paths and is displayed.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None or caught.value.__suppress_context__


def test_no_surface_and_no_dependency_refuses_rather_than_defaulting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting ``surface`` must reach the real import and fail, not fall back."""
    monkeypatch.setitem(sys.modules, "rh_mcp.gateway", None)

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
    """Config carries deployment settings; logs must not echo them back.

    A read is performed inside the block so the sink actually receives
    something. Without it this test asserted a string was absent from
    ``repr([])`` — true of every string, and true no matter what the adapter
    logs. The emptiness guard below is what keeps it honest.
    """
    sink = RecordingSink()
    surface = FakeSurface()

    async def _read_under_composition() -> None:
        async with open_read_gateway(
            surface=surface,
            probe=installed_probe,
            config_options={"namespace": "ainvest-read-broker"},
            log_sink=sink,
        ) as composed:
            await composed.client.read_accounts()

    run(_read_under_composition())

    assert sink.records, "nothing was logged, so this test would pass vacuously"
    emitted = repr(sink.records)
    assert "ainvest-read-broker" not in emitted
    assert PINNED_WHEEL_SHA256 not in emitted
    assert EXPECTED_MANIFEST_DIGEST not in emitted or "manifest_digest" in emitted


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
