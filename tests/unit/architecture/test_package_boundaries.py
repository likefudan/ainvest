"""Architecture unit tests for package boundaries and import direction."""

from __future__ import annotations

from pathlib import Path

import pytest
from import_graph import (
    BOUNDARY_PACKAGES,
    FORBIDDEN_EDGES,
    ImportEdge,
    build_adjacency,
    check_edge_against_matrix,
    collect_import_edges,
    extract_ainvest_boundary_imports,
    extract_module_imports,
    find_forbidden_edges,
    find_forbidden_external_imports,
    find_forbidden_internal_imports,
    find_import_cycles,
    iter_python_files,
    package_root,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
AINVEST_ROOT = package_root(REPO_ROOT / "src")
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROVIDER_SDK_ROOTS = frozenset({"edgar", "mcp", "pandas_market_calendars", "yfinance"})


@pytest.mark.unit
def test_boundary_packages_exist_as_importable_modules() -> None:
    """Each designed boundary package is present and importable."""
    import ainvest.agents as agents
    import ainvest.api as api
    import ainvest.approval as approval
    import ainvest.audit as audit
    import ainvest.data as data
    import ainvest.execution as execution
    import ainvest.portfolio as portfolio
    import ainvest.risk as risk
    import ainvest.schemas as schemas
    import ainvest.strategies as strategies
    import ainvest.workflow as workflow

    modules = {
        "agents": agents,
        "api": api,
        "approval": approval,
        "audit": audit,
        "data": data,
        "execution": execution,
        "portfolio": portfolio,
        "risk": risk,
        "schemas": schemas,
        "strategies": strategies,
        "workflow": workflow,
    }
    assert set(modules) == BOUNDARY_PACKAGES
    for name, module in modules.items():
        assert module.__doc__, f"{name} must document its boundary role"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("importer", "imported"),
    sorted(
        (importer, imported) for importer, banned in FORBIDDEN_EDGES.items() for imported in banned
    ),
)
def test_forbidden_edge_matrix_is_complete(importer: str, imported: str) -> None:
    """Every matrix entry is recognized as forbidden by the checker helper."""
    assert check_edge_against_matrix(importer, imported)


@pytest.mark.unit
def test_production_packages_have_no_forbidden_imports() -> None:
    """``src/ainvest`` must not contain forbidden reverse dependencies."""
    edges = collect_import_edges(AINVEST_ROOT)
    violations = find_forbidden_edges(edges)
    assert violations == [], _format_edge_violations(violations)


@pytest.mark.unit
def test_telegram_query_execution_bridge_exists_only_in_orchestrator() -> None:
    """P05-T9 must not create an approval-to-execution dependency bypass."""
    assert not (AINVEST_ROOT / "approval" / "telegram_queries.py").exists()
    bridge = AINVEST_ROOT / "orchestrator" / "telegram_queries.py"
    imports = {
        module
        for module, _ in extract_module_imports(
            bridge.read_text(encoding="utf-8"),
            source_path=bridge,
            current_package="ainvest.orchestrator",
        )
    }
    assert "ainvest.approval.telegram_updates" in imports
    assert "ainvest.execution.robinhood.display" in imports


@pytest.mark.unit
def test_production_packages_have_no_import_cycles() -> None:
    """Boundary packages under ``src/ainvest`` must not form import cycles."""
    edges = collect_import_edges(AINVEST_ROOT)
    cycles = find_import_cycles(build_adjacency(edges))
    assert cycles == [], f"import cycles detected: {cycles}"


@pytest.mark.unit
def test_cycle_detector_flags_synthetic_cycle() -> None:
    """The cycle detector reports a known synthetic cycle."""
    edges = [
        ImportEdge("agents", "strategies", Path("a.py"), 1),
        ImportEdge("strategies", "agents", Path("b.py"), 1),
    ]
    cycles = find_import_cycles(build_adjacency(edges))
    assert cycles
    assert any(set(cycle[:-1]) == {"agents", "strategies"} for cycle in cycles)


@pytest.mark.unit
def test_schemas_do_not_import_sqlalchemy_orm() -> None:
    """Domain schemas stay separate from ORM persistence models."""
    violations = find_forbidden_external_imports(AINVEST_ROOT)
    assert violations == [], "schemas must not import SQLAlchemy ORM; found: " + ", ".join(
        f"{item.package}:{item.module}@{item.source_path}:{item.lineno}" for item in violations
    )


@pytest.mark.unit
def test_production_packages_do_not_import_orm_models() -> None:
    """ORM implementation modules stay behind repository interfaces."""
    violations = find_forbidden_internal_imports(AINVEST_ROOT)
    assert violations == [], "ORM model imports crossed a boundary: " + ", ".join(
        f"{item.package}:{item.module}@{item.source_path}:{item.lineno}" for item in violations
    )


@pytest.mark.unit
def test_upper_layers_do_not_import_provider_sdks_directly() -> None:
    """Provider SDK imports stay in data adapters or broker execution gateways."""
    violations: list[str] = []
    for path in iter_python_files(AINVEST_ROOT):
        relative = path.relative_to(AINVEST_ROOT)
        if relative.parts[0] in {"data", "execution"}:
            continue
        source = path.read_text(encoding="utf-8")
        current_package = ".".join(("ainvest", *relative.parts[:-1]))
        for module, lineno in extract_module_imports(
            source,
            source_path=path,
            current_package=current_package,
        ):
            if module.split(".", maxsplit=1)[0] in PROVIDER_SDK_ROOTS:
                violations.append(f"{relative}:{lineno} imports {module}")
    assert violations == [], "provider SDK imports crossed the data boundary: " + ", ".join(
        violations
    )


@pytest.mark.unit
def test_checker_detects_forbidden_strategies_execution_fixture() -> None:
    """An intentionally invalid fixture proves the checker fails closed."""
    fixture = FIXTURES / "forbidden_strategies_imports_execution.py"
    source = fixture.read_text(encoding="utf-8")
    imported = {name for name, _ in extract_ainvest_boundary_imports(source)}
    assert "execution" in imported
    assert check_edge_against_matrix("strategies", "execution")
    synthetic = ImportEdge(
        importer="strategies",
        imported="execution",
        source_path=fixture,
        lineno=1,
    )
    assert find_forbidden_edges([synthetic]) == [synthetic]


@pytest.mark.unit
def test_checker_detects_forbidden_relative_import_fixture() -> None:
    """Relative imports cannot bypass the boundary matrix."""
    fixture = FIXTURES / "forbidden_strategies_relative_execution.txt"
    source = fixture.read_text(encoding="utf-8")
    imported = {
        name
        for name, _ in extract_ainvest_boundary_imports(
            source,
            source_path=fixture,
            current_package="ainvest.strategies",
        )
    }
    assert imported == {"execution"}
    assert check_edge_against_matrix("strategies", "execution")


@pytest.mark.unit
def test_checker_detects_forbidden_schemas_orm_fixture(
    tmp_path: Path,
) -> None:
    """An ORM import fixture proves domain/ORM separation is enforced."""
    fixture = FIXTURES / "forbidden_schemas_imports_orm.py"
    source = fixture.read_text(encoding="utf-8")
    modules = {name for name, _ in extract_module_imports(source)}
    assert "sqlalchemy.orm" in modules

    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "__init__.py").write_text(source, encoding="utf-8")
    violations = find_forbidden_external_imports(tmp_path)
    assert len(violations) == 1
    assert violations[0].package == "schemas"
    assert violations[0].module == "sqlalchemy.orm"


@pytest.mark.unit
def test_checker_detects_ainvest_db_model_import_fixture(tmp_path: Path) -> None:
    """An internal ORM-model import is detected even before ``db`` exists."""
    fixture = FIXTURES / "forbidden_strategies_imports_db_models.txt"
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "__init__.py").write_text(
        fixture.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    violations = find_forbidden_internal_imports(tmp_path)

    assert len(violations) == 1
    assert violations[0].package == "strategies"
    assert violations[0].module == "ainvest.db.models"


def _format_edge_violations(violations: list[ImportEdge]) -> str:
    parts = [
        f"{edge.importer}->{edge.imported} at {edge.source_path}:{edge.lineno}"
        for edge in violations
    ]
    return "forbidden imports: " + ", ".join(parts)
