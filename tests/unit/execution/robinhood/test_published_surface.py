"""Consumer requirement 3: only `rh-mcp`'s published surface is imported.

`rh-mcp`'s `v0.2.0` review accepted, as a **P2 residual**, that
``_open_provider_session``, ``_PrivateSession``, ``StoredTokenProvider`` and
``open_credential_store`` remain importable and can be assembled into a
manifest-free session that never consults the reviewed manifest. `DESIGN.md`
§3 says in-process separation is not the security boundary, so the gateway
does not stop this. The reviewer therefore made it a **consumer** obligation
to assert by test — this file is that test.

Two properties are checked, and they are different:

1. **No module in this package imports a private `rh-mcp` name.** Enforced by
   walking each module's AST rather than by importing it, so a name that is
   imported inside a function body, under a ``try``, or behind a conditional
   is still seen. Import-time inspection would miss all three, and
   :func:`~ainvest.execution.robinhood.composition.import_published_surface`
   is exactly such a deferred import.
2. **The whole package names `rh_mcp` in exactly one module.** A rule that
   only forbids *private* names would let a second module start importing the
   public surface directly and grow its own composition path, which is how the
   single audited chokepoint stops being single.

Both are AST facts about the source, so neither needs the distribution
installed — and it is not installed: the runtime dependency is a separate
reviewed envelope.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

PACKAGE_ROOT: Final = (
    Path(__file__).resolve().parents[4] / "src" / "ainvest" / "execution" / "robinhood"
)

#: The single module allowed to name `rh_mcp` at all.
COMPOSITION_MODULE: Final = "composition.py"

#: Exactly what that module may import, as ``module -> {names}``. Written as
#: literals: deriving it from the source would agree with any change to it.
PERMITTED_IMPORTS: Final[dict[str, frozenset[str]]] = {
    "rh_mcp.config": frozenset({"GatewayConfig"}),
    "rh_mcp.gateway": frozenset({"open_gateway"}),
}

#: Named individually rather than caught by the underscore rule alone.
#: ``StoredTokenProvider`` and ``open_credential_store`` carry no underscore,
#: so a prefix check would wave both of them straight through.
WITHDRAWN_OR_PRIVATE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "_open_provider_session",
        "open_provider_session",
        "_PrivateSession",
        "PrivateSession",
        "StoredTokenProvider",
        "AccessTokenProvider",
        "open_credential_store",
        "open_json_client",
        "ProviderTransport",
        "call_tool",
    }
)

#: `rh-mcp` keeps its MCP SDK private and ainvest imports no `mcp.*` type.
#: `httpx2` is the SDK's transport dependency and is equally out of bounds.
FORBIDDEN_ROOT_PACKAGES: Final[frozenset[str]] = frozenset({"mcp", "httpx2"})


def _module_paths() -> list[Path]:
    paths = sorted(PACKAGE_ROOT.glob("*.py"))
    assert paths, f"no modules found under {PACKAGE_ROOT}"
    return paths


def _imports(tree: ast.AST) -> list[tuple[str, str]]:
    """Every ``(module, imported_name)`` pair, at any nesting depth.

    ``ast.walk`` rather than iterating ``tree.body``: the import this file most
    needs to see is inside a function, inside a ``try``.
    """
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            found.extend((module, alias.name) for alias in node.names)
        elif isinstance(node, ast.Import):
            found.extend((alias.name, alias.name) for alias in node.names)
    return found


@pytest.fixture(scope="module")
def package_imports() -> dict[str, list[tuple[str, str]]]:
    return {
        path.name: _imports(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in _module_paths()
    }


def test_the_ast_walk_sees_imports_nested_inside_functions() -> None:
    """The detector is proven before it is trusted.

    Every assertion below is only as good as ``_imports``. If it walked just
    the module body it would return nothing for a deferred import and every
    test in this file would pass vacuously — which is the exact shape of defect
    this package's review process keeps finding.
    """
    tree = ast.parse(
        "def f():\n"
        "    try:\n"
        "        from rh_mcp.transport import _open_provider_session\n"
        "    except ImportError:\n"
        "        import mcp\n"
    )
    assert ("rh_mcp.transport", "_open_provider_session") in _imports(tree)
    assert ("mcp", "mcp") in _imports(tree)


def test_no_module_imports_a_withdrawn_or_private_rh_mcp_name(
    package_imports: dict[str, list[tuple[str, str]]],
) -> None:
    """The P2 residual, closed on ainvest's side."""
    for module_name, imports in package_imports.items():
        for source, name in imports:
            if not source.startswith("rh_mcp"):
                continue
            assert name not in WITHDRAWN_OR_PRIVATE_NAMES, (
                f"{module_name} imports withdrawn/private {source}.{name}"
            )
            assert not name.startswith("_"), f"{module_name} imports private {source}.{name}"
            assert not source.split(".")[-1].startswith("_"), (
                f"{module_name} imports from private module {source}"
            )


def test_no_module_imports_the_mcp_sdk_or_its_transport(
    package_imports: dict[str, list[tuple[str, str]]],
) -> None:
    """`rh-mcp` keeps its SDK private; ainvest holds no `mcp.*` type."""
    for module_name, imports in package_imports.items():
        for source, _ in imports:
            root = source.split(".")[0]
            assert root not in FORBIDDEN_ROOT_PACKAGES, (
                f"{module_name} imports {source}, which is `rh-mcp`'s private dependency"
            )


def test_only_the_composition_module_names_rh_mcp_at_all(
    package_imports: dict[str, list[tuple[str, str]]],
) -> None:
    """One chokepoint, not one *private-free* chokepoint per module."""
    naming = {
        module_name
        for module_name, imports in package_imports.items()
        if any(source.split(".")[0] == "rh_mcp" for source, _ in imports)
    }
    assert naming == {COMPOSITION_MODULE}


def test_the_composition_module_imports_exactly_the_published_surface(
    package_imports: dict[str, list[tuple[str, str]]],
) -> None:
    """The import list is the whole contract, so it is pinned exactly.

    An equality rather than a subset check: a superset is how an extra name
    arrives, and that is the thing being prevented.
    """
    actual: dict[str, set[str]] = {}
    for source, name in package_imports[COMPOSITION_MODULE]:
        if source.split(".")[0] == "rh_mcp":
            actual.setdefault(source, set()).add(name)

    assert actual == {module: set(names) for module, names in PERMITTED_IMPORTS.items()}


def test_the_permitted_list_is_the_two_names_the_review_allows() -> None:
    """Guards the fixture itself: widening it must be a visible edit here."""
    assert {
        "rh_mcp.config": frozenset({"GatewayConfig"}),
        "rh_mcp.gateway": frozenset({"open_gateway"}),
    } == PERMITTED_IMPORTS
    flattened = {name for names in PERMITTED_IMPORTS.values() for name in names}
    assert flattened == {"GatewayConfig", "open_gateway"}
    assert flattened.isdisjoint(WITHDRAWN_OR_PRIVATE_NAMES)


def test_every_withdrawn_name_the_review_lists_is_covered() -> None:
    """Named one by one: a set built from the code would move with the code."""
    for name in (
        "_open_provider_session",
        "_PrivateSession",
        "StoredTokenProvider",
        "open_credential_store",
    ):
        assert name in WITHDRAWN_OR_PRIVATE_NAMES
    # Two of the four carry no underscore, so the prefix rule alone is not
    # enough and the literal list is load-bearing.
    assert not "StoredTokenProvider".startswith("_")
    assert not "open_credential_store".startswith("_")
