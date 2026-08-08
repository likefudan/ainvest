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
    PINNED_RELEASE_TAG,
    PINNED_WHEEL_FILENAME,
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


# ---------------------------------------------------------------------------
# The real installed distribution
#
# Every test above injects a probe, so until the dependency envelope landed
# they proved only that the checker agrees with documents this file wrote.
# The tests below read `importlib.metadata` for real. They are the only place
# the pin in `pins.py`, the URL and digest in `pyproject.toml`, the hash in
# `uv.lock`, and the artifact the installer actually fetched are all required
# to be the same thing.
#
# They need the `broker` profile installed — `./scripts/dev setup`, or
# `./scripts/dev broker-install`, both of which `./scripts/dev verify` also
# performs. They deliberately do not skip when it is absent: a pin whose only
# unfaked check quietly skips is a pin that is not being checked.
# ---------------------------------------------------------------------------

#: Rebuilt from the pins rather than pasted, so repointing the declaration at
#: another release cannot agree with this file by accident.
EXPECTED_WHEEL_URL = (
    "https://github.com/likefudan/rh-mcp/releases/download/"
    f"{PINNED_RELEASE_TAG}/{PINNED_WHEEL_FILENAME}"
)


@pytest.mark.unit
def test_the_real_installed_distribution_is_the_pinned_release_artifact() -> None:
    """What the installer recorded, field by field, before any verdict."""
    installed = probe_installed_distribution()

    assert installed is not None, (
        "`rh-mcp` is not installed. Run `./scripts/dev setup` (or "
        "`./scripts/dev broker-install`) to install the locked broker profile."
    )
    assert installed.version == PINNED_PACKAGE_VERSION

    direct_url = installed.direct_url
    assert direct_url is not None, (
        "the installer recorded no PEP 610 direct_url.json; the broker profile "
        "must be installed from the pinned URL, not from an index"
    )
    assert direct_url["url"] == EXPECTED_WHEEL_URL
    assert "vcs_info" not in direct_url
    assert "dir_info" not in direct_url
    assert direct_url["archive_info"]["hashes"]["sha256"] == PINNED_WHEEL_SHA256


@pytest.mark.unit
def test_the_real_installed_distribution_satisfies_the_artifact_pins() -> None:
    """The verdict itself, with no probe injected and nothing stubbed."""
    assert verify_installed_artifact() == ArtifactVerification(True, None)
    assert require_installed_artifact().verified is True


@pytest.mark.unit
def test_the_real_verification_would_still_refuse_a_different_digest() -> None:
    """Vacuity guard for the two tests above.

    The real record is fed back through the checker with one field changed. A
    ``verify_installed_artifact`` that had degenerated into "return verified"
    would satisfy both tests above; it cannot satisfy this one.
    """
    installed = probe_installed_distribution()
    assert installed is not None
    assert installed.direct_url is not None

    tampered = InstalledDistribution(
        version=installed.version,
        direct_url={**installed.direct_url, "archive_info": {"hashes": {"sha256": OTHER_SHA256}}},
    )

    assert verify_installed_artifact(_probe(tampered)).rejection is (
        ArtifactRejection.ARTIFACT_DIGEST_MISMATCH
    )


@pytest.mark.unit
def test_a_uv_shaped_install_of_the_very_same_wheel_is_still_refused() -> None:
    """Why the broker profile is installed by pip and not by `uv sync`.

    uv writes ``"archive_info": {}`` for every install shape it offers — URL,
    local file, ``uv pip install --require-hashes``, ``uv sync`` — on uv
    0.11.26 and 0.12.3 alike. The URL it records is the right one and the
    digest it verified while downloading is in ``uv.lock``, but neither fact
    survives into the installed metadata, so nothing at startup can check the
    artifact. This has to stay a refusal: were an empty ``archive_info``
    accepted, the pin would be satisfied by whatever that URL served.
    """
    uv_shaped = InstalledDistribution(
        version=PINNED_PACKAGE_VERSION,
        direct_url={"url": EXPECTED_WHEEL_URL, "archive_info": {}},
    )

    assert verify_installed_artifact(_probe(uv_shaped)).rejection is (
        ArtifactRejection.ARTIFACT_DIGEST_ABSENT
    )


@pytest.mark.unit
def test_an_absent_distribution_still_fails_closed() -> None:
    """The other side of the same coin, which used to be this file's only side.

    A deployment that installs no ``broker`` extra — every non-broker profile
    in `docs/development.md` — reaches exactly this, and it must refuse rather
    than proceed without a gateway.
    """
    assert verify_installed_artifact(_probe(None)).rejection is (
        ArtifactRejection.DISTRIBUTION_ABSENT
    )

    with pytest.raises(GatewayReadError) as caught:
        require_installed_artifact(_probe(None))
    assert caught.value.code is GatewayReadErrorCode.DEPENDENCY_UNAVAILABLE
