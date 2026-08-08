"""The one place ainvest names an `rh-mcp` symbol (P06-T0).

Consumer requirement 3 of `rh-mcp`'s `v0.2.0` review: a consumer using only
``GatewayConfig`` + ``open_gateway`` / ``RobinhoodGateway.invoke`` cannot
bypass the reviewed manifest; a consumer importing an underscore-prefixed name
can. ``_open_provider_session``, ``_PrivateSession``, ``StoredTokenProvider``,
and ``open_credential_store`` all remain importable by name and would assemble
a manifest-free session. The reviewer accepted that as a **consumer**
obligation, so it lives here: exactly two names are imported, from exactly two
modules, in exactly one function, and
``tests/unit/execution/robinhood/test_published_surface.py`` walks the AST of
every module in this package to prove it.

Note that ``rh_mcp/__init__.py`` is empty in `v0.2.0` — the published surface
is reached at ``rh_mcp.gateway`` and ``rh_mcp.config``, not re-exported at the
package root.

**The runtime dependency is not declared by this task.** ``pyproject.toml``'s
``broker`` extra stays empty and ``uv.lock`` is untouched; wiring it in is a
separate reviewed envelope. So :func:`import_published_surface` fails closed
today with :attr:`~ainvest.execution.robinhood.errors.GatewayReadErrorCode.DEPENDENCY_UNAVAILABLE`,
and that is the behaviour this repository's tests exercise for real. The
composition itself is written for real and is driven end to end by a
deterministic fake surface — no live authorization, no credential, and no
network is involved anywhere in this package's tests.

Ordering here is the security property, and it is the same ordering `rh-mcp`
uses internally: verify the installed artifact, *then* build a config that
pins the expected full-manifest digest, *then* open the gateway, *then* verify
the read projection and readiness — and only then can a read happen.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from ainvest.execution.robinhood.artifact import (
    ArtifactVerification,
    DistributionProbe,
    probe_installed_distribution,
    require_installed_artifact,
)
from ainvest.execution.robinhood.errors import GatewayReadError, GatewayReadErrorCode
from ainvest.execution.robinhood.pins import EXPECTED_MANIFEST_DIGEST
from ainvest.execution.robinhood.read_client import (
    GatewayPort,
    LogSink,
    RobinhoodReadClient,
)


class PublishedSurface(Protocol):
    """The published `rh-mcp` surface, reduced to what ainvest composes.

    Injectable so the composition can be driven deterministically without the
    dependency installed, and so nothing outside
    :func:`import_published_surface` has to name an `rh-mcp` symbol.
    """

    def gateway_config(self, *, expected_manifest_digest: str, **options: Any) -> object: ...

    def open_gateway(self, config: object) -> AbstractAsyncContextManager[GatewayPort]: ...


@dataclass(frozen=True, slots=True)
class ComposedReadGateway:
    """A verified adapter plus the artifact verdict it was opened under."""

    client: RobinhoodReadClient
    artifact: ArtifactVerification


def import_published_surface() -> PublishedSurface:
    """Import `rh-mcp`'s published surface, or fail closed.

    The import list is the whole contract: ``GatewayConfig`` and
    ``open_gateway``. Nothing private, nothing from ``rh_mcp.transport``,
    ``rh_mcp.auth``, or ``rh_mcp.credentials``, and no ``mcp.*`` type.

    The ``type: ignore`` markers are load-bearing in the other direction too:
    the distribution is deliberately not declared by this task, so mypy cannot
    resolve these modules today. When the separate dependency envelope lands
    and it can, ``warn_unused_ignores`` turns them into errors — which is the
    signal to revisit this function with the real types available, rather than
    letting an unchecked import quietly become a checked one.
    """
    try:
        from rh_mcp.config import GatewayConfig  # type: ignore[import-not-found]
        from rh_mcp.gateway import open_gateway  # type: ignore[import-not-found]
    except ImportError:
        # Chained context is dropped: an import error names file system paths
        # and installed module locations, and this error is displayed.
        raise GatewayReadError(
            GatewayReadErrorCode.DEPENDENCY_UNAVAILABLE,
            rejection="published_surface_unimportable",
        ) from None
    return _RhMcpSurface(GatewayConfig, open_gateway)


@dataclass(frozen=True, slots=True)
class _RhMcpSurface:
    """Adapts the two published callables onto :class:`PublishedSurface`."""

    _gateway_config: Any
    _open_gateway: Any

    def gateway_config(self, *, expected_manifest_digest: str, **options: Any) -> object:
        config: object = self._gateway_config(
            expected_manifest_digest=expected_manifest_digest, **options
        )
        return config

    def open_gateway(self, config: object) -> AbstractAsyncContextManager[GatewayPort]:
        opened: AbstractAsyncContextManager[GatewayPort] = self._open_gateway(config)
        return opened


@asynccontextmanager
async def open_read_gateway(
    *,
    surface: PublishedSurface | None = None,
    probe: DistributionProbe = probe_installed_distribution,
    config_options: Mapping[str, Any] | None = None,
    log_sink: LogSink | None = None,
) -> AsyncIterator[ComposedReadGateway]:
    """Open a verified ainvest read projection over the pinned gateway.

    ``config_options`` is passed through to ``GatewayConfig`` for deployment
    settings such as mode and credential namespace. It cannot override
    ``expected_manifest_digest``: that is ainvest's pin, supplied here, and a
    deployment that could change it could change the permission contract.

    No credential, token, or account value is accepted, constructed, or
    logged anywhere on this path. `rh-mcp` owns OAuth, DCR, PKCE, refresh, and
    the credential store; the Read Broker deployment identity (P08-T7) owns
    which store it composes.
    """
    artifact = require_installed_artifact(probe)
    resolved = import_published_surface() if surface is None else surface

    options = dict(config_options or {})
    if "expected_manifest_digest" in options:
        raise GatewayReadError(
            GatewayReadErrorCode.DEPENDENCY_UNAVAILABLE,
            rejection="expected_manifest_digest_override",
        )

    config = resolved.gateway_config(expected_manifest_digest=EXPECTED_MANIFEST_DIGEST, **options)
    async with resolved.open_gateway(config) as gateway:
        client = RobinhoodReadClient(gateway, log_sink=log_sink)
        await client.verify_startup()
        yield ComposedReadGateway(client=client, artifact=artifact)


__all__ = [
    "ComposedReadGateway",
    "PublishedSurface",
    "import_published_surface",
    "open_read_gateway",
]
