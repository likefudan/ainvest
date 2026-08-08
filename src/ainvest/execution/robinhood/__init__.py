"""Thin adapter over the external `likefudan/rh-mcp` Non-Trading Gateway.

P06-T0 only. The gateway itself — OAuth, DCR, PKCE, refresh, the credential
store protocol, private MCP SDK v2 transport, session lifecycle, bounded
pagination, tool discovery, the reviewed capability manifest and its digests,
and default-deny enforcement — is owned by `rh-mcp` and pinned at `v0.2.0`.
This package pins that release, verifies it, narrows it to a read projection,
and hands validated payloads to P06-T1.
"""

from ainvest.execution.robinhood.artifact import (
    ArtifactRejection,
    ArtifactVerification,
    InstalledDistribution,
    probe_installed_distribution,
    require_installed_artifact,
    verify_installed_artifact,
)
from ainvest.execution.robinhood.composition import (
    ComposedReadGateway,
    PublishedSurface,
    import_published_surface,
    open_read_gateway,
)
from ainvest.execution.robinhood.errors import (
    GatewayReadError,
    GatewayReadErrorCode,
    translate_gateway_failure,
)
from ainvest.execution.robinhood.pins import (
    APPROVED_NON_TRADING_MUTATIONS,
    DENIED_TRADING_CAPABILITIES,
    EXPECTED_MANIFEST_DIGEST,
    MANIFEST_READ_CAPABILITIES,
    PINNED_ENVELOPE_VERSION,
    PINNED_MANIFEST_VERSION,
    PINNED_PACKAGE_VERSION,
    PINNED_RELEASE_TAG,
    PINNED_WHEEL_SHA256,
    ReadCapability,
)
from ainvest.execution.robinhood.prose import (
    PROVIDER_PROSE_KEYS,
    contains_provider_prose,
    discard_provider_prose,
)
from ainvest.execution.robinhood.read_client import (
    GatewayPort,
    GatewayReadResult,
    ReadinessVerification,
    ReadProjectionVerification,
    ReadRejection,
    RobinhoodReadClient,
    StartupVerification,
    verify_read_projection,
    verify_readiness,
)

__all__ = [
    "APPROVED_NON_TRADING_MUTATIONS",
    "DENIED_TRADING_CAPABILITIES",
    "EXPECTED_MANIFEST_DIGEST",
    "MANIFEST_READ_CAPABILITIES",
    "PINNED_ENVELOPE_VERSION",
    "PINNED_MANIFEST_VERSION",
    "PINNED_PACKAGE_VERSION",
    "PINNED_RELEASE_TAG",
    "PINNED_WHEEL_SHA256",
    "PROVIDER_PROSE_KEYS",
    "ArtifactRejection",
    "ArtifactVerification",
    "ComposedReadGateway",
    "GatewayPort",
    "GatewayReadError",
    "GatewayReadErrorCode",
    "GatewayReadResult",
    "InstalledDistribution",
    "PublishedSurface",
    "ReadCapability",
    "ReadProjectionVerification",
    "ReadRejection",
    "ReadinessVerification",
    "RobinhoodReadClient",
    "StartupVerification",
    "contains_provider_prose",
    "discard_provider_prose",
    "import_published_surface",
    "open_read_gateway",
    "probe_installed_distribution",
    "require_installed_artifact",
    "translate_gateway_failure",
    "verify_installed_artifact",
    "verify_read_projection",
    "verify_readiness",
]
