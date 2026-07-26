"""Export versioned domain models to committed JSON Schema snapshots (P02-T5)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

from ainvest.schemas.approval import ApprovalChallenge, ApprovalEvent
from ainvest.schemas.broker import (
    BrokerFill,
    BrokerOrder,
    CancelCommand,
    CancelResult,
    ReconciliationResult,
)
from ainvest.schemas.market import (
    FundamentalSnapshot,
    MarketEvent,
    MarketQuote,
    OhlcvBar,
    TechnicalIndicators,
)
from ainvest.schemas.orders import CandidateOrder, OrderProposal
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.research import EvidenceCitation, ResearchPacket
from ainvest.schemas.risk import RiskDecision
from ainvest.schemas.strategy import StrategyContext, TradeSignal

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
SCHEMA_JSON_ROOT: Final[Path] = REPO_ROOT / "schemas" / "json"
SCHEMA_JSON_V1: Final[Path] = SCHEMA_JSON_ROOT / "v1"

# Top-level wire contracts exported for cross-language consumers.
EXPORTED_MODELS: Final[Mapping[str, type[BaseModel]]] = {
    "ResearchPacket": ResearchPacket,
    "TradeSignal": TradeSignal,
    "StrategyContext": StrategyContext,
    "PortfolioSnapshot": PortfolioSnapshot,
    "CandidateOrder": CandidateOrder,
    "OrderProposal": OrderProposal,
    "RiskDecision": RiskDecision,
    "ApprovalChallenge": ApprovalChallenge,
    "ApprovalEvent": ApprovalEvent,
    "BrokerOrder": BrokerOrder,
    "BrokerFill": BrokerFill,
    "CancelCommand": CancelCommand,
    "CancelResult": CancelResult,
    "ReconciliationResult": ReconciliationResult,
    "MarketQuote": MarketQuote,
    "OhlcvBar": OhlcvBar,
    "TechnicalIndicators": TechnicalIndicators,
    "FundamentalSnapshot": FundamentalSnapshot,
    "MarketEvent": MarketEvent,
    "EvidenceCitation": EvidenceCitation,
}


def schema_artifact_path(model_name: str, *, major: int = 1) -> Path:
    """Return the committed JSON Schema path for ``model_name``."""
    return SCHEMA_JSON_ROOT / f"v{major}" / f"{model_name}.json"


def render_model_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a JSON-serializable schema document for ``model``."""
    return model.model_json_schema(mode="validation")


def dump_schema_document(document: Mapping[str, Any]) -> str:
    """Serialize a schema document with stable formatting for snapshots."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export_json_schemas(*, major: int = 1) -> dict[str, Path]:
    """Write all exported schemas under ``schemas/json/v{major}/``."""
    target_dir = SCHEMA_JSON_ROOT / f"v{major}"
    target_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for name, model in EXPORTED_MODELS.items():
        path = target_dir / f"{name}.json"
        path.write_text(dump_schema_document(render_model_json_schema(model)), encoding="utf-8")
        written[name] = path
    manifest = {
        "major": major,
        "models": sorted(EXPORTED_MODELS),
        "schema_version_line": "MAJOR.MINOR payload versions; see docs/schema-versioning.md",
    }
    (target_dir / "MANIFEST.json").write_text(
        dump_schema_document(manifest),
        encoding="utf-8",
    )
    return written


def check_json_schemas(*, major: int = 1) -> list[str]:
    """Return human-readable drift messages; empty means snapshots match."""
    problems: list[str] = []
    target_dir = SCHEMA_JSON_ROOT / f"v{major}"
    if not target_dir.is_dir():
        return [f"missing schema directory: {target_dir}"]
    expected_names = set(EXPORTED_MODELS) | {"MANIFEST"}
    on_disk = {path.stem for path in target_dir.glob("*.json")}
    missing = sorted(expected_names - on_disk)
    extra = sorted(on_disk - expected_names)
    if missing:
        problems.append(f"missing schema files: {', '.join(missing)}")
    if extra:
        problems.append(f"unexpected schema files: {', '.join(extra)}")
    for name, model in EXPORTED_MODELS.items():
        path = target_dir / f"{name}.json"
        if not path.is_file():
            continue
        expected = dump_schema_document(render_model_json_schema(model))
        actual = path.read_text(encoding="utf-8")
        if actual != expected:
            problems.append(f"schema drift for {name}: run ./scripts/dev export-schemas")
    manifest_path = target_dir / "MANIFEST.json"
    if manifest_path.is_file():
        expected_manifest = dump_schema_document(
            {
                "major": major,
                "models": sorted(EXPORTED_MODELS),
                "schema_version_line": (
                    "MAJOR.MINOR payload versions; see docs/schema-versioning.md"
                ),
            }
        )
        if manifest_path.read_text(encoding="utf-8") != expected_manifest:
            problems.append("schema drift for MANIFEST: run ./scripts/dev export-schemas")
    return problems


__all__ = [
    "EXPORTED_MODELS",
    "SCHEMA_JSON_ROOT",
    "SCHEMA_JSON_V1",
    "check_json_schemas",
    "dump_schema_document",
    "export_json_schemas",
    "render_model_json_schema",
    "schema_artifact_path",
]
