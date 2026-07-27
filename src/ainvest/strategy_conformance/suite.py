"""Run the strategy conformance suite against a StrategyDefinition."""

from __future__ import annotations

import time
from collections.abc import Sequence

from ainvest.strategies.definitions import StrategyDefinition
from ainvest.strategies.worker import package_version
from ainvest.strategy_conformance.checks import DEFAULT_CHECKS
from ainvest.strategy_conformance.checks._util import CheckFn
from ainvest.strategy_conformance.codes import ConformanceStatus
from ainvest.strategy_conformance.models import CheckResult, ConformanceReport

SUITE_VERSION = "1.0.0"


def run_conformance_suite(
    definition: StrategyDefinition,
    *,
    checks: Sequence[tuple[str, CheckFn]] | None = None,
) -> ConformanceReport:
    """Execute all checks and return a versioned machine-readable report."""
    selected = tuple(checks) if checks is not None else DEFAULT_CHECKS
    started = time.perf_counter()
    results: list[CheckResult] = []
    for check_id, fn in selected:
        result = fn(definition)
        if result.check_id != check_id:
            result = result.model_copy(update={"check_id": check_id})
        results.append(result)
    duration_ms = (time.perf_counter() - started) * 1000.0
    passed = all(item.status is not ConformanceStatus.FAILED for item in results)
    return ConformanceReport(
        suite_version=SUITE_VERSION,
        package_version=package_version(),
        plugin_id=definition.metadata.plugin_id,
        strategy_name=definition.name,
        strategy_version=definition.version,
        passed=passed,
        checks=tuple(results),
        duration_ms=duration_ms,
    )


def render_human_report(report: ConformanceReport) -> str:
    """Render a concise human-readable report for CI logs."""
    lines = [
        "Strategy Conformance Report",
        (
            f"suite={report.suite_version} package={report.package_version} "
            f"plugin={report.plugin_id} strategy={report.strategy_name}@"
            f"{report.strategy_version}"
        ),
        (
            f"{'PASSED' if report.passed else 'FAILED'} "
            f"({_count(report, ConformanceStatus.PASSED)}/"
            f"{len(report.checks)} checks, {report.duration_ms:.1f} ms)"
        ),
        "",
    ]
    for check in report.checks:
        mark = {
            ConformanceStatus.PASSED: "PASS",
            ConformanceStatus.FAILED: "FAIL",
            ConformanceStatus.SKIPPED: "SKIP",
        }[check.status]
        lines.append(
            f"  [{mark}] {check.check_id}: {check.message} "
            f"({check.code}, {check.duration_ms:.1f} ms)"
        )
        if check.details:
            detail_text = ", ".join(
                f"{key}={value}" for key, value in sorted(check.details.items())
            )
            lines.append(f"         details: {detail_text}")
    if not report.passed:
        lines.append("")
        lines.append("Failed checks:")
        for check in report.failed_checks:
            lines.append(f"  - {check.check_id}: {check.code}")
    return "\n".join(lines) + "\n"


def report_to_json(report: ConformanceReport) -> str:
    """Serialize the report as stable JSON text."""
    return report.model_dump_json(indent=2) + "\n"


def _count(report: ConformanceReport, status: ConformanceStatus) -> int:
    return sum(1 for check in report.checks if check.status is status)


def check_ids() -> tuple[str, ...]:
    return tuple(check_id for check_id, _ in DEFAULT_CHECKS)


__all__ = [
    "SUITE_VERSION",
    "check_ids",
    "render_human_report",
    "report_to_json",
    "run_conformance_suite",
]
