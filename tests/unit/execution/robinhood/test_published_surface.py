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

Both are AST facts about the source, so neither depends on whether the
distribution is installed, and neither changed when the dependency envelope
installed it. That is the point: these properties are about what this
package's code is allowed to name, and a wheel appearing in ``site-packages``
may not relax them.
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
    """Every module in the package, at any depth.

    ``rglob`` rather than ``glob``: with a single-level scan, a plain module in
    a subpackage — ``robinhood/session/helper.py`` importing
    ``_open_provider_session`` — passed the entire gate. The docstrings claimed
    "every module in this package" while the code covered one directory level,
    which is the same adjacent-claim defect these tests exist to prevent.
    """
    paths = sorted(path for path in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in path.parts)
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


def _dynamic_import_mechanisms(tree: ast.AST) -> list[str]:
    """Any way of reaching a module by a runtime string, not a static name.

    No static walk can follow ``importlib.import_module(f"{pkg}.transport")``,
    and a name assembled as ``"_open" + "_provider_session"`` does not even
    appear in the source. Rather than leave that as an unstated limit, the
    mechanisms themselves are refused: this package has six modules and no
    reason to import dynamically, so their absence is checkable where the
    destination of a dynamic import is not.
    """
    # `importlib.metadata` is not one of them: it reads installation facts and
    # is how `artifact.py` probes the installed distribution. What is refused
    # is the machinery that turns a *string* into a module or an attribute.
    permitted = {"importlib.metadata", "importlib.resources"}
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(
                alias.name
                for alias in node.names
                if alias.name == "importlib" or alias.name.startswith("importlib.")
                if alias.name not in permitted
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "importlib" or module.startswith("importlib."):
                # `from importlib import metadata` names the same thing as
                # `import importlib.metadata`, so resolve it the same way.
                found.extend(
                    qualified
                    for alias in node.names
                    if (qualified := f"{module}.{alias.name}") not in permitted
                    and module not in permitted
                )
        elif isinstance(node, ast.Call):
            found.extend(_dynamic_call(node))
    return found


def _dynamic_call(node: ast.Call) -> list[str]:
    """Flag a call only when it can name its target at runtime.

    ``getattr(view, "capability", None)`` with a **literal** attribute is duck
    typing on an untrusted gateway object and is the safe way to read it; this
    package does it five times deliberately. ``getattr(m, "_open" + "_provider_session")``
    is the bypass. The difference is whether the name is a constant, so that is
    what is tested rather than the function's identity.
    """
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")

    if name in {"__import__", "import_module"}:
        return [name]
    if name in {"getattr", "vars", "globals", "eval", "exec"}:
        computed = name in {"vars", "globals", "eval", "exec"} or not (
            len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        )
        return [f"{name}(computed name)"] if computed else []
    return []


def _relative_name(path: Path) -> str:
    """Identify a module by its path within the package, not by basename.

    Two subpackages may each hold a ``helper.py``; keying on the basename
    would silently collapse them and check only one.
    """
    return path.relative_to(PACKAGE_ROOT).as_posix()


@pytest.fixture(scope="module")
def package_sources() -> dict[str, str]:
    return {_relative_name(path): path.read_text(encoding="utf-8") for path in _module_paths()}


@pytest.fixture(scope="module")
def package_imports(package_sources: dict[str, str]) -> dict[str, list[tuple[str, str]]]:
    return {
        module_name: _imports(ast.parse(source, filename=module_name))
        for module_name, source in package_sources.items()
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


def test_the_mechanism_detector_sees_each_dynamic_import_form() -> None:
    """Proven before it is trusted, same as the import walk."""
    for source in (
        "import importlib\n",
        "from importlib import import_module\n",
        "x = __import__('rh_mcp.transport')\n",
        "y = importlib.import_module('rh_mcp.transport')\n",
        "z = getattr(m, '_open' + '_provider_session')\n",
        "w = getattr(m, name)\n",
    ):
        assert _dynamic_import_mechanisms(ast.parse(source)), source

    # And is not a blanket ban on `getattr`: a literal attribute name cannot
    # be assembled, and duck-typing an untrusted object is the safe idiom.
    assert _dynamic_import_mechanisms(ast.parse('v = getattr(view, "capability", None)\n')) == []
    assert _dynamic_import_mechanisms(ast.parse("from importlib import metadata\n")) == []


def test_no_module_reaches_a_module_by_runtime_string(
    package_sources: dict[str, str],
) -> None:
    """Closes the hole a static import walk cannot close by inspection.

    ``importlib.import_module(f"{pkg}.transport")`` with ``pkg`` assembled at
    runtime is invisible to :func:`_imports` — the string ``rh_mcp`` need never
    appear. Refusing the mechanism is checkable where refusing the destination
    is not.
    """
    for module_name, source in package_sources.items():
        mechanisms = _dynamic_import_mechanisms(ast.parse(source, filename=module_name))
        assert not mechanisms, f"{module_name} can reach a module dynamically via {mechanisms}"


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
