"""Executable validation for the threat-to-control evidence manifest."""

from __future__ import annotations

import ast
import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
MATRIX_PATH = REPO_ROOT / "docs" / "security" / "control-matrix.md"
MANIFEST_PATH = REPO_ROOT / "docs" / "security" / "control-evidence.json"
THREAT_MODEL_PATH = REPO_ROOT / "docs" / "security" / "threat-model.md"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
MATRIX_START = "<!-- control-matrix:start -->"
MATRIX_END = "<!-- control-matrix:end -->"
THREAT_ID = re.compile(r"T-\d{3}")
TASK_ID = re.compile(r"(?:P\d{2}-T\d+|DEC-\d{3})")
PLANNED_TARGET = re.compile(r"(?:SEC|SAFE)-[A-Z0-9*-]+")

ALLOWED_STATUSES = {"implemented", "partial", "planned", "not_applicable"}
ALLOWED_EVIDENCE_STATES = {"passing", "partial", "planned", "blocked", "not_applicable"}
ALLOWED_KINDS = {"pytest", "ci_check", "planned", "absence"}
REQUIRED_RULESET_CONTEXTS = ("Verify", "Secret scan", "Dependency audit", "SAST")

# Completion is deliberately review-gated. Adding an implemented threat requires
# adding its exact, semantically relevant evidence set here. Merely pointing the
# manifest at an existing file or unrelated test can never authorize transition.
APPROVED_COMPLETION_EVIDENCE: dict[str, dict[str, tuple[str, str | None, str]]] = {
    "T-015": {
        "E-TIME-CALENDAR": (
            "pytest",
            "tests/unit/data/test_calendar_port.py",
            "tests/unit/data/test_calendar_port.py::test_fake_calendar_holiday_and_early_close",
        ),
        "E-TIME-TTL": (
            "pytest",
            "tests/unit/approval/test_approval_service.py",
            "tests/unit/approval/test_approval_service.py::test_expired_token_creates_only_an_expired_event",
        ),
        "E-TIME-STALE": (
            "pytest",
            "tests/unit/risk/test_market_quality.py",
            "tests/unit/risk/test_market_quality.py::test_stale_quote_rejected",
        ),
        "E-CI-VERIFY": ("ci_check", ".github/workflows/ci.yml", "Verify"),
    }
}


class ManifestError(ValueError):
    """Raised when control evidence cannot support its claimed state."""


@dataclass(frozen=True, slots=True)
class Evidence:
    id: str
    kind: str
    path: str | None
    target: str
    state: str
    supports: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Threat:
    id: str
    severity: str
    applicability: str
    status: str
    evidence_state: str
    owner: str
    preventive_controls: tuple[str, ...]
    detective_controls: tuple[str, ...]
    tasks: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    completion_evidence_ids: tuple[str, ...]
    release_disposition: str


@dataclass(frozen=True, slots=True)
class Manifest:
    schema_version: str
    evidence: tuple[Evidence, ...]
    threats: tuple[Threat, ...]


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if type(value) is not str or not value.strip():
        raise ManifestError(f"{key} must be a non-empty string")
    return value


def _string_tuple(raw: dict[str, Any], key: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    value = raw.get(key)
    if type(value) is not list or any(type(item) is not str or not item for item in value):
        raise ManifestError(f"{key} must be a string list")
    if not allow_empty and not value:
        raise ManifestError(f"{key} must not be empty")
    if len(value) != len(set(value)):
        raise ManifestError(f"{key} must not contain duplicates")
    return tuple(value)


def _parse_manifest(raw: dict[str, Any]) -> Manifest:
    if type(raw) is not dict or set(raw) != {"schema_version", "evidence", "threats"}:
        raise ManifestError("manifest top-level keys do not match schema 1.0")
    if raw["schema_version"] != "1.0":
        raise ManifestError("unsupported manifest schema")
    if type(raw["evidence"]) is not list or type(raw["threats"]) is not list:
        raise ManifestError("manifest collections must be lists")

    evidence = []
    for item in raw["evidence"]:
        if type(item) is not dict or set(item) != {
            "id",
            "kind",
            "path",
            "target",
            "state",
            "supports",
        }:
            raise ManifestError("evidence fields do not match schema 1.0")
        path = item["path"]
        if path is not None and type(path) is not str:
            raise ManifestError("evidence path must be a string or null")
        evidence.append(
            Evidence(
                id=_required_str(item, "id"),
                kind=_required_str(item, "kind"),
                path=path,
                target=_required_str(item, "target"),
                state=_required_str(item, "state"),
                supports=_string_tuple(item, "supports"),
            )
        )

    threats = []
    for item in raw["threats"]:
        if type(item) is not dict or set(item) != {
            "id",
            "severity",
            "applicability",
            "status",
            "evidence_state",
            "owner",
            "preventive_controls",
            "detective_controls",
            "tasks",
            "evidence_ids",
            "completion_evidence_ids",
            "release_disposition",
        }:
            raise ManifestError("threat fields do not match schema 1.0")
        threats.append(
            Threat(
                id=_required_str(item, "id"),
                severity=_required_str(item, "severity"),
                applicability=_required_str(item, "applicability"),
                status=_required_str(item, "status"),
                evidence_state=_required_str(item, "evidence_state"),
                owner=_required_str(item, "owner"),
                preventive_controls=_string_tuple(item, "preventive_controls"),
                detective_controls=_string_tuple(item, "detective_controls"),
                tasks=_string_tuple(item, "tasks"),
                evidence_ids=_string_tuple(item, "evidence_ids"),
                completion_evidence_ids=_string_tuple(
                    item, "completion_evidence_ids", allow_empty=True
                ),
                release_disposition=_required_str(item, "release_disposition"),
            )
        )
    return Manifest(schema_version="1.0", evidence=tuple(evidence), threats=tuple(threats))


def _load_raw_manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _threat_sections() -> dict[str, str]:
    document = THREAT_MODEL_PATH.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^### (T-\d{3})\b", document, re.MULTILINE))
    return {
        match.group(1): document[match.start() : matches[index + 1].start()]
        if index + 1 < len(matches)
        else document[match.start() : document.index("\n## 5.", match.start())]
        for index, match in enumerate(matches)
    }


def _severity(section: str) -> str:
    match = re.search(r"^\| Impact \| (.+?) \|$", section, re.MULTILINE)
    if match is None:
        raise ManifestError("threat section has no Impact row")
    impact = match.group(1).lower()
    if impact.startswith("critical"):
        return "critical"
    if re.search(r"\bcritical if\b", impact):
        return "conditional_critical"
    if impact.startswith("high"):
        return "high"
    if "high" in impact:
        return "conditional_high"
    if impact.startswith("medium"):
        return "medium"
    return "low"


def _pytest_target_exists(evidence: Evidence) -> bool:
    if evidence.path is None or not evidence.target.startswith(f"{evidence.path}::"):
        return False
    path = REPO_ROOT / evidence.path
    if not path.is_file():
        return False
    node_name = evidence.target.split("::", maxsplit=1)[1]
    if not re.fullmatch(r"test_[A-Za-z0-9_]+", node_name):
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(isinstance(node, ast.FunctionDef) and node.name == node_name for node in tree.body)


def _ci_job_names() -> set[str]:
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    return {job["name"] for job in workflow["jobs"].values()}


def _validate_manifest(raw: dict[str, Any]) -> Manifest:
    manifest = _parse_manifest(raw)
    evidence_by_id = {item.id: item for item in manifest.evidence}
    threat_by_id = {item.id: item for item in manifest.threats}
    if len(evidence_by_id) != len(manifest.evidence):
        raise ManifestError("duplicate evidence ID")
    if len(threat_by_id) != len(manifest.threats):
        raise ManifestError("duplicate threat ID")

    sections = _threat_sections()
    if set(threat_by_id) != set(sections):
        raise ManifestError("manifest must cover every threat exactly once")
    for threat_id, section in sections.items():
        if threat_by_id[threat_id].severity != _severity(section):
            raise ManifestError(f"{threat_id} severity does not match threat model")

    used_evidence: set[str] = set()
    ci_jobs = _ci_job_names()
    for evidence in manifest.evidence:
        if evidence.kind not in ALLOWED_KINDS:
            raise ManifestError(f"{evidence.id} has unknown kind")
        if evidence.state not in ALLOWED_EVIDENCE_STATES:
            raise ManifestError(f"{evidence.id} has unknown state")
        if not set(evidence.supports) <= set(threat_by_id):
            raise ManifestError(f"{evidence.id} supports an unknown threat")
        if evidence.kind == "pytest":
            if evidence.state != "passing" or not _pytest_target_exists(evidence):
                raise ManifestError(f"{evidence.id} pytest target is not executable")
        elif evidence.kind == "ci_check":
            if (
                evidence.path != ".github/workflows/ci.yml"
                or evidence.target not in ci_jobs
                or evidence.target not in REQUIRED_RULESET_CONTEXTS
                or evidence.state != "passing"
            ):
                raise ManifestError(f"{evidence.id} is not an approved required CI check")
        elif evidence.kind == "planned":
            if (
                evidence.path is not None
                or evidence.state not in {"planned", "blocked"}
                or PLANNED_TARGET.fullmatch(evidence.target) is None
            ):
                raise ManifestError(f"{evidence.id} planned evidence is malformed")
        elif (
            evidence.path is None
            or (REPO_ROOT / evidence.path).exists()
            or evidence.state != "not_applicable"
        ):
            raise ManifestError(f"{evidence.id} absence evidence is not true")

    for threat in manifest.threats:
        if THREAT_ID.fullmatch(threat.id) is None:
            raise ManifestError("invalid threat ID")
        if (
            threat.status not in ALLOWED_STATUSES
            or threat.evidence_state not in ALLOWED_EVIDENCE_STATES
        ):
            raise ManifestError(f"{threat.id} has unknown state")
        if not threat.applicability or not threat.owner.endswith("owner"):
            raise ManifestError(f"{threat.id} has no accountable applicability/owner")
        if any(TASK_ID.fullmatch(task) is None for task in threat.tasks):
            raise ManifestError(f"{threat.id} has an invalid task reference")
        if any(evidence_id not in evidence_by_id for evidence_id in threat.evidence_ids):
            raise ManifestError(f"{threat.id} references unknown evidence")
        for evidence_id in threat.evidence_ids:
            evidence = evidence_by_id[evidence_id]
            if threat.id not in evidence.supports:
                raise ManifestError(f"{evidence_id} is not mapped to {threat.id}")
            used_evidence.add(evidence_id)

        if threat.severity in {"critical", "conditional_critical"} and (
            not threat.preventive_controls
            or not threat.detective_controls
            or not threat.evidence_ids
        ):
            raise ManifestError(f"{threat.id} has incomplete critical mapping")

        if threat.status == "implemented":
            approved = APPROVED_COMPLETION_EVIDENCE.get(threat.id)
            if threat.evidence_state != "passing" or approved is None:
                raise ManifestError(f"{threat.id} is not approved for completion")
            if set(threat.completion_evidence_ids) != set(approved):
                raise ManifestError(f"{threat.id} completion evidence is incomplete")
            for evidence_id, expected in approved.items():
                evidence = evidence_by_id[evidence_id]
                actual = (evidence.kind, evidence.path, evidence.target)
                if actual != expected or evidence.state != "passing":
                    raise ManifestError(f"{evidence_id} is not approved completion evidence")
                if evidence_id not in threat.evidence_ids:
                    raise ManifestError(f"{evidence_id} is absent from {threat.id}")
        elif threat.completion_evidence_ids:
            raise ManifestError(f"{threat.id} cannot carry completion evidence")

        if threat.status == "not_applicable":
            if threat.evidence_state != "not_applicable" or not any(
                evidence_by_id[evidence_id].kind == "absence" for evidence_id in threat.evidence_ids
            ):
                raise ManifestError(f"{threat.id} lacks verified absence evidence")
        elif threat.evidence_state == "not_applicable":
            raise ManifestError(f"{threat.id} has mismatched not-applicable state")

        if threat.evidence_state in {"planned", "blocked"} and threat.status not in {
            "planned",
            "partial",
        }:
            raise ManifestError(f"{threat.id} counts nonpassing evidence as complete")

    if used_evidence != set(evidence_by_id):
        raise ManifestError("orphan evidence is not allowed")
    return manifest


def _render_summary(manifest: Manifest) -> str:
    lines = [
        MATRIX_START,
        "| Threat | Severity | Applicability | Accountable owner | Evidence state | "
        "Status | Evidence IDs | Release disposition |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for threat in manifest.threats:
        evidence = ", ".join(f"`{item}`" for item in threat.evidence_ids)
        lines.append(
            f"| `{threat.id}` | `{threat.severity}` | `{threat.applicability}` | "
            f"{threat.owner} | `{threat.evidence_state}` | `{threat.status}` | {evidence} | "
            f"{threat.release_disposition} |"
        )
    lines.append(MATRIX_END)
    return "\n".join(lines)


@pytest.mark.unit
def test_control_evidence_manifest_is_valid_and_summary_is_derived() -> None:
    """The typed register is executable and its Markdown view cannot drift."""
    manifest = _validate_manifest(_load_raw_manifest())
    document = MATRIX_PATH.read_text(encoding="utf-8")
    actual = document.split(MATRIX_START, maxsplit=1)[1].split(MATRIX_END, maxsplit=1)[0]
    actual = f"{MATRIX_START}{actual}{MATRIX_END}"
    assert actual == _render_summary(manifest)


@pytest.mark.unit
@pytest.mark.parametrize("threat_id", ["T-001", "T-016"])
def test_unrelated_existing_test_cannot_relabel_incomplete_threat(
    threat_id: str,
) -> None:
    """Partial/blocked work cannot become complete by borrowing a real test."""
    raw = copy.deepcopy(_load_raw_manifest())
    threat = next(item for item in raw["threats"] if item["id"] == threat_id)
    evidence = next(item for item in raw["evidence"] if item["id"] == "E-TIME-CALENDAR")
    evidence["supports"].append(threat_id)
    threat["evidence_ids"].append("E-TIME-CALENDAR")
    threat["completion_evidence_ids"] = ["E-TIME-CALENDAR"]
    threat["status"] = "implemented"
    threat["evidence_state"] = "passing"

    with pytest.raises(ManifestError, match="not approved for completion"):
        _validate_manifest(raw)


@pytest.mark.unit
@pytest.mark.parametrize("threat_id", ["T-001", "T-005", "T-008"])
def test_critical_or_conditional_critical_threat_cannot_be_removed(threat_id: str) -> None:
    """Direct and conditional Critical rows are both mandatory."""
    raw = copy.deepcopy(_load_raw_manifest())
    raw["threats"] = [item for item in raw["threats"] if item["id"] != threat_id]

    with pytest.raises(ManifestError, match="cover every threat"):
        _validate_manifest(raw)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"path": "tests/unit/security/missing.py"}, "not executable"),
        ({"target": "tests/unit/data/test_calendar_port.py"}, "not executable"),
        (
            {
                "target": "tests/unit/data/test_calendar_port.py::"
                "test_fake_calendar_regular_open_and_closed"
            },
            "not approved completion evidence",
        ),
    ],
)
def test_missing_nonexecutable_or_mismatched_completion_evidence_fails(
    mutation: dict[str, str],
    message: str,
) -> None:
    """Completion pins an exact executable node, not just an existing path."""
    raw = copy.deepcopy(_load_raw_manifest())
    evidence = next(item for item in raw["evidence"] if item["id"] == "E-TIME-CALENDAR")
    evidence.update(mutation)

    with pytest.raises(ManifestError, match=message):
        _validate_manifest(raw)


@pytest.mark.unit
def test_implemented_evidence_is_exact_and_runs_under_required_verify() -> None:
    """Implemented evidence is executable and bound to the required full-suite gate."""
    manifest = _validate_manifest(_load_raw_manifest())
    evidence_by_id = {item.id: item for item in manifest.evidence}
    implemented = [item for item in manifest.threats if item.status == "implemented"]

    assert [item.id for item in implemented] == ["T-015"]
    for threat in implemented:
        for evidence_id in threat.completion_evidence_ids:
            evidence = evidence_by_id[evidence_id]
            if evidence.kind == "pytest":
                assert _pytest_target_exists(evidence)
            else:
                assert evidence.target == "Verify"
    assert "./scripts/dev verify" in CI_WORKFLOW_PATH.read_text(encoding="utf-8")
