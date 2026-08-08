"""The declared `rh-mcp` dependency, and the MCP SDK it drags in behind it.

`docs/development.md` states a standing rule: "ainvest must not depend on or
import the MCP Python SDK directly". Installing `rh-mcp` installs `mcp`,
because `rh-mcp` requires ``mcp>=2,<3``. This file is where that rule is
given a decidable meaning instead of being re-argued in prose each time:

* **Direct dependency.** ainvest's own metadata may not name `mcp` (or its
  transport `httpx2`) at all. Reaching the SDK has to be a consequence of
  depending on the reviewed gateway, never a decision ainvest took itself.
* **Direct import.** No module under ``src/ainvest`` may import ``mcp`` or
  ``httpx2``, at any depth, in any package — not only in the Robinhood
  adapter, which is the one place
  ``tests/unit/execution/robinhood/test_published_surface.py`` covers.
* **Reachability.** The SDK must be reachable from the ``broker`` extra and
  from nowhere else. A default install, and a `research` or `approval`
  deployment, must not acquire it. That is checked as a graph walk over
  ``uv.lock``, so "broker extra only" is a property of the resolution rather
  than of the line someone typed in ``pyproject.toml``.

The declaration itself is pinned here too, and that part is not redundant with
``uv lock --check``. uv **silently ignores** the ``#sha256=`` fragment on a
direct reference: corrupting it leaves ``uv lock --check`` green and leaves
``uv export`` emitting the original hash. pip is the installer that honours
the fragment, and a digest that only one of the two tools reads is exactly the
kind of decoration this repository's review process keeps finding. So the
fragment, the URL, and ``uv.lock``'s recorded hash are each bound to
``ainvest.execution.robinhood.pins`` by literal comparison below.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path
from typing import Any, Final

import pytest

from ainvest.execution.robinhood.pins import (
    PINNED_PACKAGE_VERSION,
    PINNED_RELEASE_TAG,
    PINNED_SDIST_SHA256,
    PINNED_WHEEL_FILENAME,
    PINNED_WHEEL_SHA256,
    RH_MCP_DISTRIBUTION,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
PYPROJECT: Final = REPO_ROOT / "pyproject.toml"
LOCKFILE: Final = REPO_ROOT / "uv.lock"
AINVEST_SOURCE_ROOT: Final = REPO_ROOT / "src" / "ainvest"

#: The extra the coordinator's envelope confines `rh-mcp` to.
BROKER_EXTRA: Final = "broker"

#: Built from the pins rather than pasted: the release URL is a function of
#: the pinned tag and the pinned wheel filename, and moving either pin has to
#: move this too. Only the host and repository path are literal here, and
#: those are the parts a typo-squat would change.
EXPECTED_WHEEL_URL: Final = (
    "https://github.com/likefudan/rh-mcp/releases/download/"
    f"{PINNED_RELEASE_TAG}/{PINNED_WHEEL_FILENAME}"
)

EXPECTED_DIRECT_REFERENCE: Final = (
    f"{RH_MCP_DISTRIBUTION} @ {EXPECTED_WHEEL_URL}#sha256={PINNED_WHEEL_SHA256}"
)

#: `rh-mcp` DESIGN.md §4/§12 keeps both of these private: no public signature,
#: exception, serialized result or annotation may carry an `mcp.*` or
#: `httpx2.*` type. ainvest holds the same line from its own side.
FORBIDDEN_SDK_ROOTS: Final[frozenset[str]] = frozenset({"mcp", "httpx2"})

#: `mcp` is the SDK the standing rule names. `httpx2` is deliberately *not*
#: in this set: `pydantic-ai-slim` reaches it through `genai-prices`, so it is
#: already present in the `research` profile and confining it to `broker`
#: would be a claim that is simply false. The import ban above still covers
#: both; only the reachability claim is narrowed to what is true.
BROKER_ONLY_PACKAGES: Final[frozenset[str]] = frozenset({"mcp"})


def _load(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        document: dict[str, Any] = tomllib.load(handle)
    return document


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    return _load(PYPROJECT)


@pytest.fixture(scope="module")
def lockfile() -> dict[str, Any]:
    return _load(LOCKFILE)


@pytest.fixture(scope="module")
def locked_packages(lockfile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    packages = {package["name"]: package for package in lockfile["package"]}
    assert "ainvest" in packages
    return packages


def _declared_requirements(pyproject: dict[str, Any]) -> dict[str, list[str]]:
    """Every requirement string in the project, keyed by where it was declared.

    Groups and extras alike: a rule that only inspected
    ``project.optional-dependencies`` would wave through a `rh-mcp` added to
    the ``test`` group, which installs it into exactly the environment whose
    green result is the merge gate.
    """
    project = pyproject["project"]
    declared: dict[str, list[str]] = {"project.dependencies": list(project["dependencies"])}
    for extra, requirements in project.get("optional-dependencies", {}).items():
        declared[f"project.optional-dependencies.{extra}"] = list(requirements)
    for group, requirements in pyproject.get("dependency-groups", {}).items():
        declared[f"dependency-groups.{group}"] = list(requirements)
    return declared


def _normalize(name: str) -> str:
    """PEP 503 normalization: the form two spellings of one package share.

    Distribution names are case-insensitive and treat runs of ``-``, ``_`` and
    ``.`` as equivalent, so ``MCP``, ``Mcp`` and ``mcp`` are the same package to
    every installer. Comparing an unnormalized name against a lower-case
    forbidden set is therefore not the check it appears to be: measured, ``MCP<3``
    in the ``test`` dependency group passed all twenty-one tests here before a
    relock, and only `uv.lock`'s own normalization caught it afterwards — so the
    pyproject-side rule, which is the primary statement of the rule, was blind.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    """The normalized distribution name from a PEP 508 requirement string.

    Split on every character that can terminate a name, not a hand-picked
    few. The original listed space, ``[``, ``>`` and ``=`` but not ``<``, so
    ``mcp<3`` parsed as the name ``"mcp<3"`` — which is in no forbidden set,
    and a direct MCP SDK dependency passed every test here.
    """
    stripped = requirement.strip()
    for index, char in enumerate(stripped):
        if char in " \t[<>=!~;@(,":
            return _normalize(stripped[:index])
    return _normalize(stripped)


def _reachable(
    packages: dict[str, dict[str, Any]],
    roots: list[str],
) -> set[str]:
    """Every package reachable from ``roots`` in ``uv.lock``'s dependency graph.

    Markers are ignored, which makes the answer a superset of any single
    platform's install. That direction is the safe one: a package this says is
    unreachable cannot be installed anywhere.
    """
    seen: set[str] = set()
    queue = list(roots)
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        package = packages.get(name)
        if package is None:
            continue
        queue.extend(entry["name"] for entry in package.get("dependencies", []))
        for group in package.get("optional-dependencies", {}).values():
            queue.extend(entry["name"] for entry in group)
    return seen


def _ainvest_roots(ainvest: dict[str, Any], *, extra: str | None) -> list[str]:
    roots = [entry["name"] for entry in ainvest.get("dependencies", [])]
    for group in ainvest.get("dev-dependencies", {}).values():
        roots.extend(entry["name"] for entry in group)
    if extra is not None:
        roots.extend(entry["name"] for entry in ainvest["optional-dependencies"][extra])
    return roots


def _imported_roots(source: str, *, filename: str) -> set[str]:
    """Top-level package name of every absolute import, at any nesting depth.

    ``ast.walk`` rather than a scan of the module body: the import this has to
    see most is the deferred one inside
    :func:`~ainvest.execution.robinhood.composition.import_published_surface`,
    which sits inside a function inside a ``try``.
    """
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            roots.update(_normalize(alias.name.split(".")[0]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            roots.add(_normalize((node.module or "").split(".")[0]))
    return roots


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_broker_extra_declares_exactly_the_pinned_wheel(
    pyproject: dict[str, Any],
) -> None:
    """One requirement, and it is the pinned artifact spelled out in full."""
    broker = pyproject["project"]["optional-dependencies"][BROKER_EXTRA]

    assert broker == [EXPECTED_DIRECT_REFERENCE]


@pytest.mark.unit
def test_the_declared_url_carries_the_pinned_tag_and_wheel_filename(
    pyproject: dict[str, Any],
) -> None:
    """Repointing at another release has to fail here, not at deploy time."""
    (requirement,) = pyproject["project"]["optional-dependencies"][BROKER_EXTRA]
    url = requirement.split(" @ ", maxsplit=1)[1].split("#", maxsplit=1)[0]

    assert url == EXPECTED_WHEEL_URL
    assert url.startswith("https://github.com/likefudan/rh-mcp/releases/download/")
    assert f"/{PINNED_RELEASE_TAG}/" in url
    assert url.endswith(f"/{PINNED_WHEEL_FILENAME}")


@pytest.mark.unit
def test_the_declared_fragment_is_the_pinned_wheel_digest(
    pyproject: dict[str, Any],
) -> None:
    """The one check standing between the fragment and being decoration.

    pip enforces this fragment and refuses a mismatching download. uv does
    not: it drops the fragment when locking, so a corrupted digest here
    survives ``uv lock --check`` and survives ``uv export``, which still emits
    the original hash from the lock.
    """
    (requirement,) = pyproject["project"]["optional-dependencies"][BROKER_EXTRA]
    _, fragment = requirement.split("#", maxsplit=1)

    assert fragment == f"sha256={PINNED_WHEEL_SHA256}"


@pytest.mark.unit
def test_the_declared_digest_is_the_wheels_and_not_the_sdists() -> None:
    """A pin that is the wrong 64 hex characters is still 64 hex characters.

    `rh-mcp` publishes both a wheel and an sdist for `v0.2.0` and
    ``docs/tasks/status.md`` records both digests side by side, so pasting the
    neighbouring row is the realistic way to get this wrong. An sdist would
    also be *built* rather than installed, producing a wheel nobody hashed.
    """
    assert len(PINNED_WHEEL_SHA256) == 64
    assert PINNED_WHEEL_SHA256.lower() == PINNED_WHEEL_SHA256
    assert set(PINNED_WHEEL_SHA256) <= set("0123456789abcdef")
    assert PINNED_WHEEL_FILENAME.endswith(".whl")

    # Widened to `str` on purpose: compared as the literals they are declared
    # as, mypy proves them unequal and rejects the check as non-overlapping —
    # which would remove the one assertion that catches the two pins being
    # made equal by a paste.
    wheel_digest: str = PINNED_WHEEL_SHA256
    sdist_digest: str = PINNED_SDIST_SHA256
    assert wheel_digest != sdist_digest
    assert f"sha256={wheel_digest}" in EXPECTED_DIRECT_REFERENCE
    assert sdist_digest not in EXPECTED_DIRECT_REFERENCE


@pytest.mark.unit
def test_the_pinned_wheel_is_the_only_direct_reference_in_the_project(
    pyproject: dict[str, Any],
) -> None:
    """``allow-direct-references`` is on; this is the allowlist it needs.

    Hatchling's refusal of direct references is a supply-chain default, and
    turning it off for one reviewed artifact must not turn it off for the next
    unreviewed one.
    """
    direct: dict[str, list[str]] = {}
    for location, requirements in _declared_requirements(pyproject).items():
        referencing = [requirement for requirement in requirements if " @ " in requirement]
        if referencing:
            direct[location] = referencing

    assert direct == {f"project.optional-dependencies.{BROKER_EXTRA}": [EXPECTED_DIRECT_REFERENCE]}


@pytest.mark.unit
def test_rh_mcp_is_declared_in_the_broker_extra_and_nowhere_else(
    pyproject: dict[str, Any],
) -> None:
    """Coordinator decision 1, as a property of the file rather than a note."""
    declaring = {
        location
        for location, requirements in _declared_requirements(pyproject).items()
        if any(
            requirement.split(" ")[0].split("[")[0] == RH_MCP_DISTRIBUTION
            for requirement in requirements
        )
    }

    assert declaring == {f"project.optional-dependencies.{BROKER_EXTRA}"}


# ---------------------------------------------------------------------------
# The lockfile
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_lockfile_records_the_pinned_digest_for_the_pinned_url(
    locked_packages: dict[str, dict[str, Any]],
) -> None:
    """What ``uv export`` hands pip, and therefore what pip enforces."""
    rh_mcp = locked_packages[RH_MCP_DISTRIBUTION]

    assert rh_mcp["version"] == PINNED_PACKAGE_VERSION
    assert rh_mcp["source"] == {"url": EXPECTED_WHEEL_URL}
    assert rh_mcp["wheels"] == [
        {"url": EXPECTED_WHEEL_URL, "hash": f"sha256:{PINNED_WHEEL_SHA256}"}
    ]


@pytest.mark.unit
def test_the_lockfile_confines_rh_mcp_to_the_broker_extra(
    locked_packages: dict[str, dict[str, Any]],
) -> None:
    """The resolved metadata, not the declaration, carries the marker."""
    ainvest = locked_packages["ainvest"]
    requires = [
        entry
        for entry in ainvest["metadata"]["requires-dist"]
        if entry["name"] == RH_MCP_DISTRIBUTION
    ]

    assert requires == [
        {
            "name": RH_MCP_DISTRIBUTION,
            "marker": f"extra == '{BROKER_EXTRA}'",
            "url": EXPECTED_WHEEL_URL,
        }
    ]
    assert ainvest["optional-dependencies"][BROKER_EXTRA] == [{"name": RH_MCP_DISTRIBUTION}]
    assert RH_MCP_DISTRIBUTION not in {entry["name"] for entry in ainvest["dependencies"]}


@pytest.mark.unit
def test_the_lockfile_installs_no_sdist_fallback_for_the_pinned_wheel(
    locked_packages: dict[str, dict[str, Any]],
) -> None:
    """The pin is on the wheel; an sdist would be built, not verified.

    `rh-mcp` publishes an sdist with a different SHA-256, and building it
    would produce a wheel this repository has never hashed.
    """
    rh_mcp = locked_packages[RH_MCP_DISTRIBUTION]

    assert "sdist" not in rh_mcp


# ---------------------------------------------------------------------------
# The transitive MCP SDK: a security question, answered as reachability
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ainvest_never_names_the_mcp_sdk_in_its_own_metadata(
    pyproject: dict[str, Any],
    locked_packages: dict[str, dict[str, Any]],
) -> None:
    """Metadata half of the rule: no direct dependency, declared anywhere."""
    for location, requirements in _declared_requirements(pyproject).items():
        for requirement in requirements:
            name = _requirement_name(requirement)
            assert name not in FORBIDDEN_SDK_ROOTS, f"{location} names {name} directly"

    # Both halves of the lock's own record. `requires-dist` carries
    # `project.dependencies` and the extras; the dependency groups land in
    # `requires-dev`. Reading only the first left `mcp<3` in the `test` group —
    # the environment whose green result *is* the merge gate — passing all
    # sixteen tests in this file.
    metadata = locked_packages["ainvest"]["metadata"]
    declared = {_normalize(entry["name"]) for entry in metadata.get("requires-dist", [])}
    for group in metadata.get("requires-dev", {}).values():
        declared |= {_normalize(entry["name"]) for entry in group}

    assert declared, "no requirements were read, so this assertion proves nothing"
    assert "pytest" in declared, "requires-dev was not read; the group half is unchecked"
    assert declared.isdisjoint(FORBIDDEN_SDK_ROOTS)


@pytest.mark.unit
def test_the_mcp_sdk_is_required_only_by_the_reviewed_gateway(
    locked_packages: dict[str, dict[str, Any]],
) -> None:
    """`mcp` is in the lock because `rh-mcp` asked for it, and for no other reason."""
    requiring = {
        name
        for name, package in locked_packages.items()
        if "mcp" in {entry["name"] for entry in package.get("dependencies", [])}
    }

    assert requiring == {RH_MCP_DISTRIBUTION}
    assert locked_packages[RH_MCP_DISTRIBUTION]["metadata"]["requires-dist"] == [
        {"name": "httpx2", "specifier": ">=2.5.0,<3.0.0"},
        {"name": "mcp", "specifier": ">=2.0.0,<3.0.0"},
    ]


@pytest.mark.unit
def test_no_profile_but_broker_can_reach_the_mcp_sdk(
    locked_packages: dict[str, dict[str, Any]],
) -> None:
    """The conclusion, as a graph fact: broker-only, every other profile clean.

    The default profile includes the ``dev`` and ``test`` groups, because
    ``[tool.uv] default-groups`` installs them and they are what the merge
    gate runs inside.
    """
    ainvest = locked_packages["ainvest"]
    extras = sorted(ainvest["optional-dependencies"])
    assert BROKER_EXTRA in extras

    default = _reachable(locked_packages, _ainvest_roots(ainvest, extra=None))

    # Anchors, before the disjointness below is believed. Every `isdisjoint`
    # in this test is satisfied by the empty set, so a walk narrowed to
    # nothing — `_reachable(packages, [])` is the whole mutation — would report
    # a clean profile with no profile examined. Five such narrowings each left
    # all sixteen tests green. These name packages each profile must reach:
    # one runtime dependency, one dev-group tool that proves `_ainvest_roots`
    # really includes the groups its docstring claims, and one package two
    # edges deep that proves the walk is transitive at all.
    assert "pydantic" in default, "the walk missed project.dependencies"
    assert "pytest" in default, "_ainvest_roots dropped the dev/test groups"
    assert "pydantic-core" in default, "the walk did not follow a second edge"

    assert default.isdisjoint(BROKER_ONLY_PACKAGES), "the default profile reaches the MCP SDK"

    for extra in extras:
        reachable = _reachable(locked_packages, _ainvest_roots(ainvest, extra=extra))
        assert default <= reachable, f"the {extra} profile lost the default packages"

        contaminated = reachable & BROKER_ONLY_PACKAGES
        if extra == BROKER_EXTRA:
            assert contaminated == BROKER_ONLY_PACKAGES, "the broker profile must reach it"
            assert RH_MCP_DISTRIBUTION in reachable
        else:
            assert contaminated == set(), f"the {extra} profile reaches {sorted(contaminated)}"


@pytest.mark.unit
def test_the_reachability_walk_follows_more_than_one_edge(
    locked_packages: dict[str, dict[str, Any]],
) -> None:
    """Proves the detector before the claim above is trusted.

    `mcp` is two edges from ainvest — ainvest -> rh-mcp -> mcp — and three
    edges from it is `mcp-types`. A walk that stopped at direct dependencies
    would report the broker profile clean and pass the test above for the
    wrong reason.
    """
    ainvest = locked_packages["ainvest"]
    direct = set(_ainvest_roots(ainvest, extra=BROKER_EXTRA))
    reachable = _reachable(locked_packages, sorted(direct))

    assert "mcp" not in direct
    assert "mcp" in reachable
    assert "mcp-types" in reachable
    assert reachable > direct


# ---------------------------------------------------------------------------
# "No direct import", across the whole of `src/ainvest`
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_import_walk_sees_imports_nested_inside_functions() -> None:
    """Proven before it is trusted: a body-only walk would return nothing."""
    source = (
        "def f():\n"
        "    try:\n"
        "        from mcp.client.session import ClientSession\n"
        "    except ImportError:\n"
        "        import httpx2.transports\n"
    )

    assert _imported_roots(source, filename="probe.py") == {"mcp", "httpx2"}
    assert _imported_roots("from . import sibling\n", filename="probe.py") == set()
    assert _imported_roots("import structlog\n", filename="probe.py") == {"structlog"}


@pytest.mark.unit
def test_no_ainvest_module_imports_the_mcp_sdk_or_its_transport() -> None:
    """Repo-wide, not adapter-wide.

    ``tests/unit/execution/robinhood/test_published_surface.py`` walks the
    Robinhood package, and ``test_upper_layers_do_not_import_provider_sdks_directly``
    exempts ``data`` and ``execution`` outright. Between them, an ``mcp``
    import anywhere else under ``execution`` was nobody's job. Installing the
    SDK is what makes that gap reachable, so this closes it.
    """
    modules = sorted(
        path for path in AINVEST_SOURCE_ROOT.rglob("*.py") if "__pycache__" not in path.parts
    )
    assert modules, f"no modules found under {AINVEST_SOURCE_ROOT}"

    violations = [
        f"{path.relative_to(REPO_ROOT).as_posix()} imports {sorted(offending)}"
        for path in modules
        if (
            offending := _imported_roots(
                path.read_text(encoding="utf-8"),
                filename=path.name,
            )
            & FORBIDDEN_SDK_ROOTS
        )
    ]

    assert violations == []


@pytest.mark.unit
def test_the_forbidden_roots_are_the_two_rh_mcp_keeps_private() -> None:
    """Written as literals: a set derived from the lock moves with the lock."""
    assert frozenset({"mcp", "httpx2"}) == FORBIDDEN_SDK_ROOTS
    assert frozenset({"mcp"}) == BROKER_ONLY_PACKAGES
    assert BROKER_ONLY_PACKAGES < FORBIDDEN_SDK_ROOTS


# ---------------------------------------------------------------------------
# The two helpers, pinned directly. Review showed five distinct narrowings of
# them left all sixteen tests green, because every claim they support is a
# negative one and a narrowed walk satisfies a negative claim vacuously.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_requirement_name_parser_handles_every_terminator() -> None:
    """`mcp<3` parsed as the name `"mcp<3"` and passed the forbidden-set check."""
    for requirement, expected in (
        ("mcp<3", "mcp"),
        ("mcp>=2,<3", "mcp"),
        ("mcp==2.0.0", "mcp"),
        ("mcp!=2.1", "mcp"),
        ("mcp~=2.0", "mcp"),
        ("mcp [cli] >=2", "mcp"),
        ("mcp[cli]>=2", "mcp"),
        ("mcp; python_version>='3.12'", "mcp"),
        ("rh-mcp @ https://example.invalid/x.whl#sha256=00", "rh-mcp"),
        ("mcp", "mcp"),
        ("  mcp  ", "mcp"),
    ):
        assert _requirement_name(requirement) == expected, requirement

    # And does not over-trim a name that legitimately contains a hyphen or dot.
    assert _requirement_name("pandas-market-calendars>=5,<6") == "pandas-market-calendars"


@pytest.mark.unit
def test_the_forbidden_set_is_compared_on_a_normalized_name() -> None:
    """Distribution names are case- and separator-insensitive to installers.

    Measured before this existed: `MCP<3` in the `test` dependency group passed
    all twenty-one tests in this file, because the name reached the lower-case
    forbidden set unnormalized. Only `uv.lock`'s own normalization caught it,
    and only after a relock — so the pyproject-side rule, the primary statement
    of the whole rule, was blind to a spelling every installer resolves to the
    same package.
    """
    for spelling in ("MCP", "Mcp", "mCp", "MCP<3", "MCP >= 2", "MCP[cli]>=2"):
        assert _requirement_name(spelling) in FORBIDDEN_SDK_ROOTS, spelling
    for spelling in ("HTTPX2", "HttpX2>=2.5", "HTTPX2<3"):
        assert _requirement_name(spelling) in FORBIDDEN_SDK_ROOTS, spelling

    # Separator runs collapse, per PEP 503, and the dot form is the same package.
    assert _requirement_name("ruamel.yaml>=0.18") == "ruamel-yaml"
    assert _normalize("ruamel_yaml") == _normalize("ruamel.yaml") == "ruamel-yaml"
    assert _normalize("pandas__market..calendars") == "pandas-market-calendars"

    # And a genuinely different package is still different: `mcp_sdk` normalizes
    # to `mcp-sdk`, which is not the SDK and must not be swept up.
    assert _requirement_name("mcp_sdk>=1") == "mcp-sdk"
    assert _requirement_name("mcp_sdk>=1") not in FORBIDDEN_SDK_ROOTS

    # The forbidden set must itself be in normalized form, or none of the above
    # comparisons mean anything.
    assert all(_normalize(name) == name for name in FORBIDDEN_SDK_ROOTS)


@pytest.mark.unit
def test_the_reachability_walk_is_transitive_and_follows_extras() -> None:
    """Pinned against a synthetic graph, so a narrowing cannot hide in real data.

    A walk that stopped at direct dependencies, or ignored
    ``optional-dependencies`` edges, still reports every negative claim in this
    file as satisfied.
    """
    packages: dict[str, dict[str, Any]] = {
        "root": {
            "dependencies": [{"name": "direct"}],
            "optional-dependencies": {"extra": [{"name": "optional"}]},
        },
        "direct": {"dependencies": [{"name": "deep"}]},
        "deep": {"dependencies": [{"name": "deeper"}]},
        "deeper": {},
        "optional": {"dependencies": [{"name": "optional-deep"}]},
        "optional-deep": {},
        "unrelated": {},
    }

    reached = _reachable(packages, ["root"])

    assert reached == {
        "root",
        "direct",
        "deep",
        "deeper",
        "optional",
        "optional-deep",
    }
    assert "unrelated" not in reached


@pytest.mark.unit
def test_the_reachability_walk_terminates_on_a_cycle() -> None:
    """A cycle must not hang the gate; the `seen` set is what prevents it."""
    packages = {
        "a": {"dependencies": [{"name": "b"}]},
        "b": {"dependencies": [{"name": "a"}, {"name": "c"}]},
        "c": {},
    }
    assert _reachable(packages, ["a"]) == {"a", "b", "c"}


@pytest.mark.unit
def test_empty_roots_reach_nothing_so_a_narrowed_walk_is_visible() -> None:
    """The mutation that survived, named.

    ``_reachable(packages, [])`` returning the empty set is correct behaviour —
    the defect was that every caller's claim was satisfied by it. This states
    the behaviour so the anchors above are the thing standing in its way.
    """
    assert _reachable({"a": {"dependencies": [{"name": "b"}]}, "b": {}}, []) == set()


@pytest.mark.unit
def test_ainvest_roots_include_the_dependency_groups(
    locked_packages: dict[str, dict[str, Any]],
) -> None:
    """`_ainvest_roots`'s docstring claims the dev/test groups; this checks it.

    Dropping the ``dev-dependencies`` loop falsified that sentence and left the
    suite green, because the profile it silently stopped examining was the one
    the merge gate runs in.
    """
    ainvest = locked_packages["ainvest"]
    roots = _ainvest_roots(ainvest, extra=None)

    assert "pydantic" in roots, "project.dependencies missing from the roots"
    assert "pytest" in roots, "the dev/test groups are not roots"
    assert RH_MCP_DISTRIBUTION not in roots, "the broker extra must not be a default root"

    broker_roots = _ainvest_roots(ainvest, extra=BROKER_EXTRA)
    assert RH_MCP_DISTRIBUTION in broker_roots
    assert set(roots) < set(broker_roots), "the extra must add to the roots, not replace them"
