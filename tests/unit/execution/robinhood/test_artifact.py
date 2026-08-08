"""Unit tests for release/artifact pin verification (P06-T0)."""

from __future__ import annotations

from typing import Any

import pytest

from ainvest.execution.robinhood.artifact import (
    ARTIFACT_REJECTION_WIRE_NAMES,
    ArtifactRejection,
    ArtifactVerification,
    InstalledDistribution,
    probe_installed_distribution,
    require_installed_artifact,
    verify_installed_artifact,
)
from ainvest.execution.robinhood.errors import GatewayReadError, GatewayReadErrorCode
from ainvest.execution.robinhood.pins import (
    PINNED_PACKAGE_VERSION,
    PINNED_WHEEL_SHA256,
    RH_MCP_DISTRIBUTION,
)

OTHER_SHA256 = "0" * 64


def _archive(sha256: str = PINNED_WHEEL_SHA256) -> dict[str, Any]:
    return {
        "url": f"https://github.com/likefudan/rh-mcp/releases/download/v0.2.0/{RH_MCP_DISTRIBUTION}.whl",
        "archive_info": {"hashes": {"sha256": sha256}},
    }


def _probe(installed: InstalledDistribution | None) -> Any:
    def probe() -> InstalledDistribution | None:
        return installed

    return probe


@pytest.mark.unit
def test_rejection_wire_strings_are_pinned_by_literal_table() -> None:
    assert {member.name: member.value for member in ArtifactRejection} == (
        ARTIFACT_REJECTION_WIRE_NAMES
    )


@pytest.mark.unit
def test_the_pinned_wheel_artifact_verifies() -> None:
    installed = InstalledDistribution(version=PINNED_PACKAGE_VERSION, direct_url=_archive())

    assert verify_installed_artifact(_probe(installed)) == ArtifactVerification(True, None)


@pytest.mark.unit
def test_the_deprecated_pep610_hash_spelling_also_verifies() -> None:
    installed = InstalledDistribution(
        version=PINNED_PACKAGE_VERSION,
        direct_url={"archive_info": {"hash": f"sha256={PINNED_WHEEL_SHA256}"}},
    )

    assert verify_installed_artifact(_probe(installed)).verified is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("installed", "expected"),
    [
        (None, ArtifactRejection.DISTRIBUTION_ABSENT),
        (
            InstalledDistribution(version="0.1.0", direct_url=_archive()),
            ArtifactRejection.VERSION_MISMATCH,
        ),
        (
            InstalledDistribution(version="0.3.0", direct_url=_archive()),
            ArtifactRejection.VERSION_MISMATCH,
        ),
        (
            InstalledDistribution(version=PINNED_PACKAGE_VERSION, direct_url=None),
            ArtifactRejection.ARTIFACT_UNVERIFIABLE,
        ),
        (
            InstalledDistribution(
                version=PINNED_PACKAGE_VERSION,
                direct_url={"url": "file:///src/rh-mcp", "dir_info": {"editable": True}},
            ),
            ArtifactRejection.MUTABLE_INSTALL,
        ),
        (
            InstalledDistribution(
                version=PINNED_PACKAGE_VERSION,
                direct_url={"url": "file:///src/rh-mcp", "dir_info": {}},
            ),
            ArtifactRejection.MUTABLE_INSTALL,
        ),
        (
            InstalledDistribution(
                version=PINNED_PACKAGE_VERSION,
                direct_url={
                    "url": "https://github.com/likefudan/rh-mcp",
                    "vcs_info": {"vcs": "git", "commit_id": "46128a62"},
                },
            ),
            ArtifactRejection.MUTABLE_INSTALL,
        ),
        (
            InstalledDistribution(
                version=PINNED_PACKAGE_VERSION,
                direct_url={"url": "https://example.invalid/x.whl", "archive_info": {}},
            ),
            ArtifactRejection.ARTIFACT_DIGEST_ABSENT,
        ),
        (
            InstalledDistribution(
                version=PINNED_PACKAGE_VERSION,
                direct_url={
                    "url": "https://example.invalid/x.whl",
                    "archive_info": {"hashes": {"md5": "d41d8cd98f00b204e9800998ecf8427e"}},
                },
            ),
            ArtifactRejection.ARTIFACT_DIGEST_ABSENT,
        ),
        (
            InstalledDistribution(
                version=PINNED_PACKAGE_VERSION, direct_url=_archive(OTHER_SHA256)
            ),
            ArtifactRejection.ARTIFACT_DIGEST_MISMATCH,
        ),
        (
            InstalledDistribution(
                version=PINNED_PACKAGE_VERSION, direct_url={"url": "x", "archive_info": "no"}
            ),
            ArtifactRejection.ARTIFACT_UNVERIFIABLE,
        ),
    ],
)
def test_a_missing_mutable_or_mismatched_artifact_is_rejected(
    installed: InstalledDistribution | None,
    expected: ArtifactRejection,
) -> None:
    """Every rejection the execution envelope names, with its own reason."""
    verification = verify_installed_artifact(_probe(installed))

    assert verification.verified is False
    assert verification.rejection is expected


@pytest.mark.unit
def test_a_sdist_only_install_does_not_satisfy_the_wheel_digest_pin() -> None:
    """The sdist has a different SHA-256; the pin is on the wheel artifact."""
    installed = InstalledDistribution(
        version=PINNED_PACKAGE_VERSION,
        direct_url=_archive("da1d2231fd7be4129e035879eec4965727b968496c382bdaaa6f663bec11842c"),
    )

    assert verify_installed_artifact(_probe(installed)).rejection is (
        ArtifactRejection.ARTIFACT_DIGEST_MISMATCH
    )


@pytest.mark.unit
def test_require_raises_a_sanitized_dependency_error_carrying_the_reason() -> None:
    with pytest.raises(GatewayReadError) as caught:
        require_installed_artifact(_probe(None))

    assert caught.value.code is GatewayReadErrorCode.DEPENDENCY_UNAVAILABLE
    assert caught.value.rejection == ArtifactRejection.DISTRIBUTION_ABSENT.value


@pytest.mark.unit
def test_require_returns_the_verdict_when_the_pinned_artifact_is_installed() -> None:
    installed = InstalledDistribution(version=PINNED_PACKAGE_VERSION, direct_url=_archive())

    assert require_installed_artifact(_probe(installed)).verified is True


@pytest.mark.unit
def test_a_verification_cannot_be_both_verified_and_rejected() -> None:
    with pytest.raises(ValueError, match="verified xor rejected"):
        ArtifactVerification(True, ArtifactRejection.VERSION_MISMATCH)
    with pytest.raises(ValueError, match="verified xor rejected"):
        ArtifactVerification(False, None)


@pytest.mark.unit
def test_the_real_probe_reports_absent_because_the_dependency_is_not_declared() -> None:
    """Not a stub: this is the live fail-closed state of this repository.

    P06-T0 deliberately does not add `rh-mcp` to ``pyproject.toml``, populate
    the ``broker`` extra, or move ``uv.lock`` — that is a separate reviewed
    envelope. So the real probe finds nothing and the real verification
    refuses. This test is what keeps that honest rather than assumed, and it
    is expected to be rewritten by the envelope that installs the dependency.
    """
    assert probe_installed_distribution() is None
    assert verify_installed_artifact().rejection is ArtifactRejection.DISTRIBUTION_ABSENT

    with pytest.raises(GatewayReadError) as caught:
        require_installed_artifact()
    assert caught.value.code is GatewayReadErrorCode.DEPENDENCY_UNAVAILABLE
