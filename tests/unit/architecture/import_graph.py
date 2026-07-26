"""Architecture dependency checker for ainvest boundary packages.

Parses Python sources with the AST and reports forbidden cross-package imports
and import cycles. Production packages under ``src/ainvest`` are checked by
unit tests; intentional violation fixtures live under
``tests/unit/architecture/fixtures`` and are never imported by production code.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

BOUNDARY_PACKAGES: frozenset[str] = frozenset(
    {
        "agents",
        "data",
        "schemas",
        "strategies",
        "risk",
        "approval",
        "execution",
        "portfolio",
        "audit",
        "api",
    }
)

# Importer package -> packages it must never import.
FORBIDDEN_EDGES: Mapping[str, frozenset[str]] = {
    "schemas": BOUNDARY_PACKAGES - {"schemas"},
    "data": frozenset({"agents", "strategies", "risk", "approval", "execution", "api"}),
    "agents": frozenset({"execution", "approval", "risk"}),
    "strategies": frozenset({"execution", "approval", "risk", "agents"}),
    "risk": frozenset({"approval", "execution", "agents", "strategies"}),
    "approval": frozenset({"execution", "agents", "strategies"}),
    "portfolio": frozenset({"execution", "approval", "agents", "strategies"}),
    "audit": frozenset({"execution", "approval", "agents", "strategies", "risk"}),
    # api and execution may depend on other packages; cycles are still forbidden.
}

# Domain/boundary packages must not pull in SQLAlchemy ORM APIs.
FORBIDDEN_EXTERNAL_MODULES: Mapping[str, frozenset[str]] = {
    "schemas": frozenset({"sqlalchemy", "sqlalchemy.orm"}),
}


@dataclass(frozen=True, slots=True)
class ImportEdge:
    """A directed import from one boundary package into another."""

    importer: str
    imported: str
    source_path: Path
    lineno: int


@dataclass(frozen=True, slots=True)
class ForbiddenExternalImport:
    """A boundary package importing a forbidden third-party module."""

    package: str
    module: str
    source_path: Path
    lineno: int


def package_root(src_root: Path) -> Path:
    """Return the ``src/ainvest`` directory under a repository root or src root."""
    candidate = src_root / "ainvest"
    if candidate.is_dir():
        return candidate
    if src_root.name == "ainvest" and src_root.is_dir():
        return src_root
    msg = f"ainvest package root not found under {src_root}"
    raise FileNotFoundError(msg)


def iter_python_files(root: Path) -> list[Path]:
    """List ``*.py`` files under ``root``, excluding caches."""
    return sorted(
        path for path in root.rglob("*.py") if ".__pycache__" not in path.parts and path.is_file()
    )


def _boundary_name(module_parts: Sequence[str]) -> str | None:
    """Map ``ainvest.<pkg>...`` parts to a boundary package name."""
    if len(module_parts) < 2 or module_parts[0] != "ainvest":
        return None
    name = module_parts[1]
    if name in BOUNDARY_PACKAGES:
        return name
    return None


def extract_ainvest_boundary_imports(
    source: str,
    *,
    source_path: Path | None = None,
) -> list[tuple[str, int]]:
    """Return ``(boundary_package, lineno)`` imports found in ``source``."""
    tree = ast.parse(source, filename=str(source_path or "<string>"))
    found: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = tuple(alias.name.split("."))
                boundary = _boundary_name(parts)
                if boundary is not None:
                    found.append((boundary, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            parts = tuple(node.module.split("."))
            # ``from ainvest import execution`` — names are boundary packages.
            if parts == ("ainvest",):
                for alias in node.names:
                    if alias.name in BOUNDARY_PACKAGES:
                        found.append((alias.name, node.lineno))
                continue
            boundary = _boundary_name(parts)
            if boundary is not None:
                found.append((boundary, node.lineno))

    return found


def extract_module_imports(
    source: str,
    *,
    source_path: Path | None = None,
) -> list[tuple[str, int]]:
    """Return ``(module_name, lineno)`` for top-level and dotted imports."""
    tree = ast.parse(source, filename=str(source_path or "<string>"))
    found: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.append((node.module, node.lineno))

    return found


def boundary_package_for_path(file_path: Path, ainvest_root: Path) -> str | None:
    """Return the boundary package owning ``file_path``, if any."""
    try:
        relative = file_path.resolve().relative_to(ainvest_root.resolve())
    except ValueError:
        return None
    if not relative.parts:
        return None
    name = relative.parts[0]
    if name in BOUNDARY_PACKAGES:
        return name
    return None


def collect_import_edges(ainvest_root: Path) -> list[ImportEdge]:
    """Collect directed boundary-package import edges under ``ainvest_root``."""
    edges: list[ImportEdge] = []
    for path in iter_python_files(ainvest_root):
        importer = boundary_package_for_path(path, ainvest_root)
        if importer is None:
            continue
        source = path.read_text(encoding="utf-8")
        for imported, lineno in extract_ainvest_boundary_imports(source, source_path=path):
            if imported == importer:
                continue
            edges.append(
                ImportEdge(
                    importer=importer,
                    imported=imported,
                    source_path=path,
                    lineno=lineno,
                )
            )
    return edges


def find_forbidden_edges(edges: Iterable[ImportEdge]) -> list[ImportEdge]:
    """Return edges that violate the forbidden-dependency matrix."""
    violations: list[ImportEdge] = []
    for edge in edges:
        forbidden = FORBIDDEN_EDGES.get(edge.importer, frozenset())
        if edge.imported in forbidden:
            violations.append(edge)
    return violations


def find_forbidden_external_imports(ainvest_root: Path) -> list[ForbiddenExternalImport]:
    """Return forbidden third-party imports (e.g. ORM in schemas)."""
    violations: list[ForbiddenExternalImport] = []
    for path in iter_python_files(ainvest_root):
        package = boundary_package_for_path(path, ainvest_root)
        if package is None:
            continue
        banned = FORBIDDEN_EXTERNAL_MODULES.get(package)
        if not banned:
            continue
        source = path.read_text(encoding="utf-8")
        for module, lineno in extract_module_imports(source, source_path=path):
            root_module = module.split(".", maxsplit=1)[0]
            if module in banned or root_module in banned:
                violations.append(
                    ForbiddenExternalImport(
                        package=package,
                        module=module,
                        source_path=path,
                        lineno=lineno,
                    )
                )
    return violations


def build_adjacency(edges: Iterable[ImportEdge]) -> dict[str, set[str]]:
    """Build an adjacency map from import edges."""
    graph: dict[str, set[str]] = {name: set() for name in BOUNDARY_PACKAGES}
    for edge in edges:
        graph.setdefault(edge.importer, set()).add(edge.imported)
        graph.setdefault(edge.imported, set())
    return graph


def find_import_cycles(graph: Mapping[str, set[str]]) -> list[tuple[str, ...]]:
    """Return simple cycles among packages (each cycle as a tuple of names)."""
    cycles: list[tuple[str, ...]] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def dfs(node: str) -> None:
        visiting.add(node)
        stack.append(node)
        for neighbor in sorted(graph.get(node, ())):
            if neighbor in visiting:
                start = stack.index(neighbor)
                cycle = (*stack[start:], neighbor)
                normalized = _normalize_cycle(cycle)
                if normalized not in cycles:
                    cycles.append(normalized)
            elif neighbor not in visited:
                dfs(neighbor)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for name in sorted(graph):
        if name not in visited:
            dfs(name)
    return cycles


def _normalize_cycle(cycle: tuple[str, ...]) -> tuple[str, ...]:
    """Rotate a cycle so the lexicographically smallest node is first."""
    # cycle includes repeated start node at the end.
    body = cycle[:-1]
    if not body:
        return cycle
    min_index = body.index(min(body))
    rotated = body[min_index:] + body[:min_index]
    return (*rotated, rotated[0])


def check_edge_against_matrix(importer: str, imported: str) -> bool:
    """Return True when ``importer -> imported`` is a forbidden edge."""
    return imported in FORBIDDEN_EDGES.get(importer, frozenset())
