"""Strategy conformance test suite (P03-T5).

Independent plugin teams run this suite in their own CI to validate hooks,
metadata, parameters, signal schemas, determinism, isolation boundaries, and a
Paper Trading example before integrating with ainvest.
"""

from __future__ import annotations

from ainvest.strategy_conformance.codes import ConformanceCode, ConformanceStatus
from ainvest.strategy_conformance.models import CheckResult, ConformanceReport
from ainvest.strategy_conformance.suite import (
    SUITE_VERSION,
    check_ids,
    render_human_report,
    report_to_json,
    run_conformance_suite,
)

__all__ = [
    "SUITE_VERSION",
    "CheckResult",
    "ConformanceCode",
    "ConformanceReport",
    "ConformanceStatus",
    "check_ids",
    "render_human_report",
    "report_to_json",
    "run_conformance_suite",
]
