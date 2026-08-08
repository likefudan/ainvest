"""Deployment/startup verification of the installed `rh-mcp` artifact (P06-T0).

The execution envelope requires ainvest to "accept only the pinned gateway
release/artifact and the expected full-manifest digest" and to "reject a
missing, mutable, or mismatched installed artifact at deployment/startup".
This module is the artifact half; :mod:`ainvest.execution.robinhood.read_client`
is the manifest half.

**The runtime dependency is deliberately not declared by this task.** Adding
`rh-mcp` to ``pyproject.toml``, populating the ``broker`` extra, and moving
``uv.lock`` is a separate reviewed envelope. The consequence is handled rather
than hidden: with the distribution absent, :func:`verify_installed_artifact`
reports :attr:`ArtifactRejection.DISTRIBUTION_ABSENT` and
:func:`require_installed_artifact` raises — that is the fail-closed outcome,
and it is what runs in this repository today.

Artifact identity is read from PEP 610 ``direct_url.json``, which is the only
standard, offline record of *what was actually installed*. The three failure
modes the envelope names map onto it directly:

* **missing** — no distribution metadata at all.
* **mutable** — an editable install, a plain directory install, or a VCS
  install. None of those is an immutable release artifact; each can change
  underneath a running deployment without the version changing.
* **mismatched** — a wheel whose recorded SHA-256 is not the pinned one, or a
  package version that is not the pinned one.

A registry install that records no ``direct_url.json`` is **also** refused, as
:attr:`ArtifactRejection.ARTIFACT_UNVERIFIABLE`. That is a deliberate, and
strict, choice: the pin is on an artifact digest, and an installation that
cannot evidence which artifact it came from has not satisfied the pin. The
deployment therefore has to install from the pinned URL/file so the digest is
recorded — which is what makes the pin load-bearing rather than decorative.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, distribution
from typing import Any, Final, Protocol

from ainvest.execution.robinhood.errors import GatewayReadError, GatewayReadErrorCode
from ainvest.execution.robinhood.pins import (
    PINNED_PACKAGE_VERSION,
    PINNED_WHEEL_SHA256,
    RH_MCP_DISTRIBUTION,
)


class ArtifactRejection(StrEnum):
    """Why an installed artifact is not the pinned one."""

    DISTRIBUTION_ABSENT = "distribution_absent"
    VERSION_MISMATCH = "version_mismatch"
    ARTIFACT_UNVERIFIABLE = "artifact_unverifiable"
    MUTABLE_INSTALL = "mutable_install"
    ARTIFACT_DIGEST_ABSENT = "artifact_digest_absent"
    ARTIFACT_DIGEST_MISMATCH = "artifact_digest_mismatch"


#: The wire strings :class:`ArtifactRejection` is allowed to carry.
ARTIFACT_REJECTION_WIRE_NAMES: Final[dict[str, str]] = {
    "DISTRIBUTION_ABSENT": "distribution_absent",
    "VERSION_MISMATCH": "version_mismatch",
    "ARTIFACT_UNVERIFIABLE": "artifact_unverifiable",
    "MUTABLE_INSTALL": "mutable_install",
    "ARTIFACT_DIGEST_ABSENT": "artifact_digest_absent",
    "ARTIFACT_DIGEST_MISMATCH": "artifact_digest_mismatch",
}


@dataclass(frozen=True, slots=True)
class InstalledDistribution:
    """What an offline probe can learn about an installed distribution.

    ``direct_url`` is the decoded PEP 610 ``direct_url.json`` document, or
    ``None`` when the installer recorded none.
    """

    version: str
    direct_url: Mapping[str, Any] | None


class DistributionProbe(Protocol):
    """Reads installation facts. Injected so tests need no real install."""

    def __call__(self) -> InstalledDistribution | None: ...


@dataclass(frozen=True, slots=True)
class ArtifactVerification:
    """The verdict, with the reason kept as a stable machine code."""

    verified: bool
    rejection: ArtifactRejection | None

    def __post_init__(self) -> None:
        if self.verified is (self.rejection is not None):
            raise ValueError("an ArtifactVerification is verified xor rejected")


_VERIFIED: Final = ArtifactVerification(verified=True, rejection=None)


def probe_installed_distribution() -> InstalledDistribution | None:
    """Read `rh-mcp`'s installed metadata, or ``None`` when it is absent.

    Touches only local installation metadata: no import of the package, no
    network, and nothing that could execute dependency code.
    """
    try:
        dist = distribution(RH_MCP_DISTRIBUTION)
    except PackageNotFoundError:
        return None
    raw = dist.read_text("direct_url.json")
    direct_url: Mapping[str, Any] | None = None
    if raw is not None:
        try:
            decoded = json.loads(raw)
        except ValueError:
            decoded = None
        if isinstance(decoded, Mapping):
            direct_url = decoded
    return InstalledDistribution(version=dist.version, direct_url=direct_url)


def verify_installed_artifact(
    probe: DistributionProbe = probe_installed_distribution,
) -> ArtifactVerification:
    """Check the installed distribution against the release/artifact pins."""
    installed = probe()
    if installed is None:
        return ArtifactVerification(False, ArtifactRejection.DISTRIBUTION_ABSENT)
    if installed.version != PINNED_PACKAGE_VERSION:
        return ArtifactVerification(False, ArtifactRejection.VERSION_MISMATCH)

    direct_url = installed.direct_url
    if direct_url is None:
        return ArtifactVerification(False, ArtifactRejection.ARTIFACT_UNVERIFIABLE)
    if "vcs_info" in direct_url or "dir_info" in direct_url:
        return ArtifactVerification(False, ArtifactRejection.MUTABLE_INSTALL)

    archive = direct_url.get("archive_info")
    if not isinstance(archive, Mapping):
        return ArtifactVerification(False, ArtifactRejection.ARTIFACT_UNVERIFIABLE)

    recorded = _archive_sha256(archive)
    if recorded is None:
        return ArtifactVerification(False, ArtifactRejection.ARTIFACT_DIGEST_ABSENT)
    if recorded != PINNED_WHEEL_SHA256:
        return ArtifactVerification(False, ArtifactRejection.ARTIFACT_DIGEST_MISMATCH)
    return _VERIFIED


def require_installed_artifact(
    probe: DistributionProbe = probe_installed_distribution,
) -> ArtifactVerification:
    """Verify, or raise the sanitized dependency error. Fails closed."""
    verification = verify_installed_artifact(probe)
    rejection = verification.rejection
    if rejection is not None:
        raise GatewayReadError(
            GatewayReadErrorCode.DEPENDENCY_UNAVAILABLE,
            rejection=rejection.value,
        )
    return verification


def _archive_sha256(archive: Mapping[str, Any]) -> str | None:
    """The recorded SHA-256, from either PEP 610 spelling.

    ``hashes`` is the current field; ``hash`` is the deprecated
    ``"<algorithm>=<value>"`` form that older installers still write. An
    algorithm other than SHA-256 is treated as no digest at all rather than
    accepted on its own terms.
    """
    hashes = archive.get("hashes")
    if isinstance(hashes, Mapping):
        value = hashes.get("sha256")
        if isinstance(value, str) and value:
            return value.lower()
    legacy = archive.get("hash")
    if isinstance(legacy, str) and legacy.startswith("sha256="):
        return legacy.split("=", maxsplit=1)[1].lower()
    return None


__all__ = [
    "ARTIFACT_REJECTION_WIRE_NAMES",
    "ArtifactRejection",
    "ArtifactVerification",
    "DistributionProbe",
    "InstalledDistribution",
    "probe_installed_distribution",
    "require_installed_artifact",
    "verify_installed_artifact",
]
