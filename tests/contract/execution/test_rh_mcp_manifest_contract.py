"""Cross-repository contract: the pins are recomputed, never transcribed.

`src/ainvest/execution/robinhood/pins.py` states the identity of the reviewed
`rh-mcp` `v0.3.0` permission set. Until this module existed those constants
were prose: a reviewer demonstrated that swapping ``EXPECTED_MANIFEST_DIGEST``
for the wrong-but-plausible digest `rh-mcp`'s changelog prints, *and* changing
the capability split, left ainvest's entire suite green. Nothing executable
guarded the values the whole Non-Trading Preview rests on.

This file closes that. It does not import `rh_mcp` — the dependency is out of
scope for `P06-T0` and importing the package would only prove that `rh-mcp`
agrees with itself. Instead it implements `rh-canon-1` **from the written
specification** in `rh-mcp` `canonical.py`'s module docstring and `DESIGN.md`
§6, then recomputes the full-manifest digest of the committed `v0.3.0`
manifest and compares it to the pin. Two independent implementations landing
on the same 64 hex characters is evidence; one implementation agreeing with
itself is not.

The fixture is byte-identical to
``git show v0.3.0:src/rh_mcp/manifests/read-manifest.json`` and
:func:`test_the_committed_fixture_is_the_reviewed_artifact` keeps it that way
by re-deriving its digest rather than trusting the filename.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import pytest

from ainvest.execution.robinhood import pins

MANIFEST_PATH: Final = (
    Path(__file__).resolve().parents[2] / "fixtures" / "rh_mcp" / "v0.3.0" / "read-manifest.json"
)
DESIGN_PATH: Final = Path(__file__).resolve().parents[3] / "design.md"

FULL_MANIFEST_DIGEST_FIELD: Final = "full_manifest_digest"

# `rh-canon-1` §3: the minimal RFC 8259 escape set. Every other code point
# below U+0020 becomes `\u00xx` with **lowercase** hex; U+007F and all
# non-ASCII are emitted literally, so the canonical form is UTF-8 bytes rather
# than ASCII.
_SHORT_ESCAPES: Final[dict[str, str]] = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _canonical_string(value: str) -> str:
    out = ['"']
    for char in value:
        escape = _SHORT_ESCAPES.get(char)
        if escape is not None:
            out.append(escape)
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _canonical(value: Any, depth: int = 0) -> str:
    """`rh-canon-1`, written from the specification rather than the source."""
    if depth > 128:
        raise ValueError("structure is too deep to canonicalize")
    if value is None:
        return "null"
    # `bool` before `int`: `True` is an `int` in Python and must not render `1`.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return repr(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("NaN and Infinity have no canonical form")
        return repr(value)
    if isinstance(value, str):
        return _canonical_string(value)
    if isinstance(value, Mapping):
        # §1: pairs sorted by the key's sequence of Unicode code points, which
        # is Python's native string ordering — deliberately not RFC 8785/JCS.
        items = sorted(value.items(), key=lambda item: item[0])
        for key, _ in items:
            if not isinstance(key, str):
                raise ValueError("object keys must be strings")
        body = ",".join(
            f"{_canonical_string(key)}:{_canonical(item, depth + 1)}" for key, item in items
        )
        return f"{{{body}}}"
    # `bytes` is a `Sequence`, so it must be excluded explicitly or it would
    # canonicalize as an array of integers instead of being rejected. The
    # specification rejects anything JSON cannot carry rather than coercing it,
    # because a coercion is a place where two different inputs collide.
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        # §2: array order is semantically meaningful and is never sorted.
        return f"[{','.join(_canonical(item, depth + 1) for item in value)}]"
    raise ValueError(f"no canonical form for {type(value).__name__}")


def _canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _full_manifest_digest(document: Mapping[str, Any]) -> str:
    """`rh-mcp` `DESIGN.md` §6: SHA-256 over everything but the digest field.

    `entries` is sorted by ``provider_tool_name`` before hashing, so the digest
    is a function of the reviewed content rather than of file order. The hashed
    input is tagged with its ``digest_kind`` and the canonicalization version
    and nested under ``manifest``, so the tag cannot collide with a document
    field.
    """
    payload: dict[str, Any] = {
        key: value for key, value in document.items() if key != FULL_MANIFEST_DIGEST_FIELD
    }
    entries = payload.get("entries")
    if isinstance(entries, (list, tuple)):
        payload["entries"] = sorted(
            entries,
            key=lambda entry: (
                entry.get("provider_tool_name", "") if isinstance(entry, Mapping) else ""
            ),
        )
    return _canonical_digest(
        {
            "digest_kind": "full_manifest",
            "canonicalization": pins.PINNED_CANONICALIZATION_VERSION,
            "manifest": payload,
        }
    )


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    document: Any = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


# ---------------------------------------------------------------------------
# The canonicalization itself, before it is trusted with the manifest
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({}, "{}"),
        ([], "[]"),
        ({"b": 1, "a": 2}, '{"a":2,"b":1}'),
        ([3, 1, 2], "[3,1,2]"),
        (True, "true"),
        (False, "false"),
        (None, "null"),
        ({"k": True}, '{"k":true}'),
        # `1` and `1.0` are different canonical values (§5), which is the
        # fail-closed direction: more digest changes, never fewer.
        (1, "1"),
        (1.0, "1.0"),
        (-0.0, "-0.0"),
        (0.0, "0.0"),
        ("\n", '"\\n"'),
        ("\x00", '"\\u0000"'),
        ("\x1f", '"\\u001f"'),
        # U+007F and non-ASCII are emitted literally: UTF-8 bytes, not ASCII.
        ("\x7f", '"\x7f"'),
        ("é", '"é"'),
        ("☃", '"☃"'),
        ('a"b\\c', '"a\\"b\\\\c"'),
    ],
)
def test_canonical_form_matches_the_written_specification(value: Any, expected: str) -> None:
    """Golden vectors for `rh-canon-1`, taken from its specification.

    If this implementation is wrong, the digest agreement below is a
    coincidence rather than evidence, so the primitive is pinned first.
    """
    assert _canonical(value) == expected


@pytest.mark.contract
def test_canonical_sorts_by_code_point_not_utf16_code_unit() -> None:
    """§1: `rh-canon-1` is deliberately not RFC 8785/JCS.

    JCS sorts by UTF-16 code units and would order the astral character before
    U+FFFF; code-point ordering puts it after. Pinning the difference stops
    somebody "fixing" it into JCS by accident.
    """
    assert _canonical({"\U0001f600": 1, "￿": 2}) == '{"￿":2,"\U0001f600":1}'


@pytest.mark.contract
def test_canonical_rejects_values_json_cannot_carry() -> None:
    """Rejected rather than coerced: a coercion is where two inputs collide."""
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            _canonical(value)
    with pytest.raises(ValueError):
        _canonical({1: "non-string key"})
    with pytest.raises(ValueError):
        _canonical({"set"}.pop().encode())


# ---------------------------------------------------------------------------
# The pins, recomputed from the artifact
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_the_committed_fixture_is_the_reviewed_artifact(manifest: dict[str, Any]) -> None:
    """The fixture's own recorded digest is re-derived, not read and believed.

    A fixture is only evidence if it is the artifact it claims to be. This
    recomputes the digest from the file's content and checks the file's own
    ``full_manifest_digest`` field against it, so an edited fixture fails here
    rather than silently redefining what the pins are compared against.
    """
    assert _full_manifest_digest(manifest) == manifest[FULL_MANIFEST_DIGEST_FIELD]


@pytest.mark.contract
def test_expected_manifest_digest_is_recomputed_from_the_manifest(
    manifest: dict[str, Any],
) -> None:
    """The pin ainvest enforces equals an independent recomputation.

    This is the assertion the whole Non-Trading Preview rests on: it is what
    makes ``EXPECTED_MANIFEST_DIGEST`` a checked value rather than a string
    somebody typed.
    """
    assert _full_manifest_digest(manifest) == pins.EXPECTED_MANIFEST_DIGEST


@pytest.mark.contract
def test_the_historical_rejected_digest_is_not_this_manifests_digest(
    manifest: dict[str, Any],
) -> None:
    """The retained v0.2.0 documentation mismatch is not a current pin.

    Its ``[0.1.0]`` and ``[0.2.0]`` entries both show ``sha256:49b7218…``
    beside manifest version ``2026.08.03.1``. That value remains named as a
    regression, but it does not belong to the independently reviewed v0.3.0
    artifact and can never become its accepted full-manifest digest.
    """
    # Widened to `str` deliberately. Both pins are `Final` literals, so mypy
    # folds the comparison and reports `comparison-overlap` — it can prove at
    # type level what this asserts at value level. Silencing it by deleting the
    # assertion would remove the only thing that fails if somebody "tidies" the
    # two constants into the same value.
    rejected: str = pins.REJECTED_CHANGELOG_MANIFEST_DIGEST
    assert rejected != pins.EXPECTED_MANIFEST_DIGEST
    assert _full_manifest_digest(manifest) != rejected


@pytest.mark.contract
def test_manifest_identity_fields_match_the_pins(manifest: dict[str, Any]) -> None:
    assert manifest["manifest_version"] == pins.PINNED_MANIFEST_VERSION
    assert manifest["manifest_format_version"] == pins.PINNED_MANIFEST_FORMAT_VERSION
    assert manifest["canonicalization_version"] == pins.PINNED_CANONICALIZATION_VERSION
    assert manifest["digest_algorithm"] == pins.PINNED_DIGEST_ALGORITHM
    assert manifest["provider_surface_digest"] == pins.PINNED_PROVIDER_SURFACE_DIGEST


# ---------------------------------------------------------------------------
# The 35 / 11 / 8 split (IMPLEMENTATION_TODO.md rule 32)
# ---------------------------------------------------------------------------


def _partition(manifest: Mapping[str, Any]) -> tuple[set[str], set[str], set[str]]:
    entries = manifest["entries"]
    reads = {e["capability"] for e in entries if e["disposition"] == "allowed" and not e["mutates"]}
    mutations = {e["capability"] for e in entries if e["disposition"] == "allowed" and e["mutates"]}
    denied = {e["capability"] for e in entries if e["disposition"] != "allowed"}
    return reads, mutations, denied


@pytest.mark.contract
def test_the_three_dispositions_are_the_pinned_name_sets(manifest: dict[str, Any]) -> None:
    """Names, not counts. Two capabilities swapping sides keeps every count."""
    reads, mutations, denied = _partition(manifest)
    assert reads == pins.MANIFEST_READ_CAPABILITIES
    assert mutations == pins.APPROVED_NON_TRADING_MUTATIONS
    assert denied == pins.DENIED_TRADING_CAPABILITIES


@pytest.mark.contract
def test_the_split_is_exactly_35_11_8(manifest: dict[str, Any]) -> None:
    """Rule 32's arithmetic, checked against the artifact and against itself."""
    reads, mutations, denied = _partition(manifest)
    assert (len(reads), len(mutations), len(denied)) == (
        pins.EXPECTED_READ_CAPABILITY_COUNT,
        pins.EXPECTED_APPROVED_MUTATION_COUNT,
        pins.EXPECTED_DENIED_CAPABILITY_COUNT,
    )
    assert (len(reads), len(mutations), len(denied)) == (35, 11, 8)
    assert len(manifest["entries"]) == pins.EXPECTED_MANIFEST_ENTRY_COUNT == 54
    assert len(reads) + len(mutations) + len(denied) == 54
    assert reads.isdisjoint(mutations) and reads.isdisjoint(denied)
    assert mutations.isdisjoint(denied)


@pytest.mark.contract
def test_every_denied_capability_is_a_mutation(manifest: dict[str, Any]) -> None:
    """The boundary `rh-mcp` enforces is "no trading", not "no writes"."""
    denied_entries = [e for e in manifest["entries"] if e["disposition"] != "allowed"]
    assert denied_entries
    assert all(e["mutates"] for e in denied_entries)


# ---------------------------------------------------------------------------
# The ainvest read projection — the narrowing `rh-mcp` does not do for us
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_the_read_projection_is_a_subset_of_the_manifests_read_capabilities(
    manifest: dict[str, Any],
) -> None:
    """`rh-mcp` ships no read-only projection; this narrowing is ainvest code.

    ``RobinhoodGateway.invoke()`` accepts any *allowed* capability, the 11
    approved mutations included. Rules 20 and 32 make excluding them ainvest's
    obligation, so the projection is checked against the reviewed artifact
    rather than against another ainvest constant.
    """
    reads, mutations, denied = _partition(manifest)
    projection = {capability.value for capability in pins.ReadCapability}

    assert projection
    assert projection <= reads
    assert projection.isdisjoint(mutations)
    assert projection.isdisjoint(denied)


@pytest.mark.contract
def test_no_read_projection_entry_mutates(manifest: dict[str, Any]) -> None:
    """Read from the entry's own ``mutates`` flag, not from set membership.

    The subset test above would still pass if the manifest marked one of these
    ``mutates=true`` and the partition helper had a bug. This asks each entry
    directly, which is the flag requirement the external review names.
    """
    by_name = {e["capability"]: e for e in manifest["entries"]}
    for capability in pins.ReadCapability:
        entry = by_name[capability.value]
        assert entry["disposition"] == "allowed"
        assert entry["mutates"] is False


@pytest.mark.contract
def test_read_capability_wire_names_are_pinned_as_literals() -> None:
    """Comparing a ``StrEnum`` against itself proves only that ``StrEnum`` works.

    ``READ_CAPABILITY_WIRE_NAMES`` is written out as literal member names and
    literal wire strings so a rename in either position has to survive a table
    that was not generated from the thing it checks.
    """
    assert {c.name for c in pins.ReadCapability} == set(pins.READ_CAPABILITY_WIRE_NAMES)
    for member_name, wire_name in pins.READ_CAPABILITY_WIRE_NAMES.items():
        capability = getattr(pins.ReadCapability, member_name)
        assert capability.value == wire_name
        assert str(capability) == wire_name
        assert json.dumps(capability) == json.dumps(wire_name)


@pytest.mark.contract
def test_limited_margin_upgrade_read_is_reviewed_but_not_projected() -> None:
    """The v0.3.0 manifest expansion must not widen ainvest's public reads."""
    capability = "get_limited_margin_upgrade_info"
    projection = {member.value for member in pins.ReadCapability}

    assert capability in pins.MANIFEST_READ_CAPABILITIES
    assert capability not in projection
    assert len(projection) == 10


@pytest.mark.contract
def test_design_phase4_distinguishes_manifest_from_callable_projection() -> None:
    """Prevent the reviewed manifest from being described as the public API."""
    design = DESIGN_PATH.read_text(encoding="utf-8")
    phase4 = design.split("### Phase 4\uff1a", maxsplit=1)[1].split(
        "### Phase 5\uff1a", maxsplit=1
    )[0]

    assert "manifest 精确允许 35 个读取能力和 11 个非交易 mutation" in phase4
    assert "ainvest 当前只能调用已有 10 个命名读取能力" in phase4
    assert "永久拒绝 8 个交易能力" in phase4
    assert "调用精确批准的 34 个读取能力" not in design


@pytest.mark.contract
def test_the_projection_excludes_every_approved_mutation_by_name() -> None:
    """Named individually: a set-difference passes vacuously if a set is empty."""
    projection = {capability.value for capability in pins.ReadCapability}
    for mutation in (
        "add_option_to_watchlist",
        "add_to_watchlist",
        "create_scan",
        "create_watchlist",
        "follow_watchlist",
        "remove_from_watchlist",
        "remove_option_from_watchlist",
        "unfollow_watchlist",
        "update_scan_config",
        "update_scan_filters",
        "update_watchlist",
    ):
        assert mutation in pins.APPROVED_NON_TRADING_MUTATIONS
        assert mutation not in projection


@pytest.mark.contract
def test_the_projection_excludes_every_denied_trading_capability_by_name() -> None:
    projection = {capability.value for capability in pins.ReadCapability}
    for denied in (
        "cancel_equity_order",
        "cancel_option_exercise",
        "cancel_option_order",
        "exercise_option",
        "place_equity_order",
        "place_option_order",
        "review_equity_order",
        "review_option_order",
    ):
        assert denied in pins.DENIED_TRADING_CAPABILITIES
        assert denied not in projection
