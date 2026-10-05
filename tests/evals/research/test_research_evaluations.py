"""Offline gates do not authorize scheduled Paper or real AI."""

from decimal import Decimal

import pytest
from research_eval_fixtures import definitions, observation, rates, run_suite, thresholds

from ainvest.agents.research_evaluation import (
    ResearchEvalReport,
    comparable_reports,
    evaluate_research,
)
from ainvest.data.indicators import digest_bytes
from ainvest.schemas.common import QualityFlag


def test_versioned_synthetic_suite_and_safety_gates() -> None:
    report = run_suite()
    failures = [
        (case.case_id, case.failures, case.error_code) for case in report.cases if not case.passed
    ]
    assert report.offline_checks_passed, failures
    assert report.outcome_rate == 1 and report.cost_complete
    assert report.total_known_cost_usd == Decimal("0.00528")
    assert sum(case.unsupported_claim_attempts for case in report.cases) == 4
    assert not any(case.unsupported_claims_accepted for case in report.cases)
    assert not report.scheduled_paper_eligible and not report.real_ai_eligible
    assert report == ResearchEvalReport.model_validate_json(report.model_dump_json())
    assert comparable_reports(report, report)
    changed = report.model_copy(update={"suite_version": "other-suite"})
    assert not comparable_reports(report, changed)
    assert all(case.evidence_coverage == 1 for case in report.cases if case.status != "error")
    assert all(case.numeric_consistency for case in report.cases if case.status != "error")


@pytest.mark.parametrize(
    "case_id,flag",
    [
        ("eval_conflicting_sources", QualityFlag.CONFLICTING_SOURCES),
        ("eval_old_news", QualityFlag.STALE),
    ],
)
def test_source_quality_flags_survive_assembly(case_id: str, flag: QualityFlag) -> None:
    case = next(case for case in definitions().cases if case.id == case_id)
    seen = observation(case)
    assert seen.archive.payload.packet is not None
    assert flag in seen.archive.payload.packet.quality_flags


@pytest.mark.parametrize(
    "change,code",
    [
        ({"expected_last_price": Decimal(999)}, "NUMERIC_INCONSISTENCY"),
        ({"expected_status": "error"}, "OUTCOME_MISMATCH"),
        ({"latency_ms": 10_001}, "LATENCY_LIMIT"),
        ({"expected_schema_success": False}, "SCHEMA_OUTCOME_MISMATCH"),
    ],
)
def test_intentionally_failed_release_thresholds(change: dict[str, object], code: str) -> None:
    seen = observation(definitions().cases[0]).model_copy(update=change)
    report = evaluate_research(
        (seen,),
        suite_version="deliberate-failure-v1",
        suite_digest=digest_bytes(b"test"),
        thresholds=thresholds(),
        rates=rates(),
    )
    assert not report.offline_checks_passed and code in report.cases[0].failures


def test_unknown_usage_and_cost_limit_fail_closed() -> None:
    seen = observation(definitions().cases[0])
    agent = seen.archive.payload.agent
    agent = agent.model_copy(
        update={
            "status": "partial",
            "quality_flags": (QualityFlag.PARTIAL,),
            "record": agent.record.model_copy(update={"usage_complete": False}),
        }
    )
    # Evaluation must detect unknown usage even when the archive has no packet.
    payload = seen.archive.payload.model_copy(
        update={
            "agent": agent,
            "status": "error",
            "packet": None,
            "error_code": "USAGE_UNKNOWN",
        }
    )
    from ainvest.agents.research_archive import ResearchArchive

    seen = seen.model_copy(
        update={
            "archive": ResearchArchive.create(payload),
            "expected_status": "error",
            "expected_last_price": None,
            "expected_error_code": "USAGE_UNKNOWN",
        }
    )
    report = evaluate_research(
        (seen,),
        suite_version="usage-probe-v1",
        suite_digest=digest_bytes(b"usage"),
        thresholds=thresholds(),
        rates=rates(),
    )
    assert not report.cost_complete and not report.offline_checks_passed
    assert report.cases[0].cost_usd is None and "USAGE_UNKNOWN" in report.cases[0].failures
    original = observation(definitions().cases[0])
    report = evaluate_research(
        (original,),
        suite_version="cost-probe-v1",
        suite_digest=digest_bytes(b"cost"),
        thresholds=thresholds().model_copy(update={"max_case_cost_usd": Decimal("0.00001")}),
        rates=rates(),
    )
    assert not report.offline_checks_passed and "COST_LIMIT" in report.cases[0].failures


def test_report_cannot_mark_failed_cases_passing_or_enable_runtime() -> None:
    report = run_suite()
    body = report.model_dump(mode="json")
    body["real_ai_eligible"] = True
    with pytest.raises(ValueError):
        ResearchEvalReport.model_validate(body)
    body = report.model_dump(mode="json")
    body["cases"][0]["failures"] = ["NUMERIC_INCONSISTENCY"]
    with pytest.raises(ValueError):
        ResearchEvalReport.model_validate(body)
