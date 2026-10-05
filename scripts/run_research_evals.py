"""Print a bounded synthetic offline report; never load runtime credentials."""

import argparse
import sys
from pathlib import Path

from ainvest.agents.research_evaluation import ResearchEvalReport, comparable_reports

REPO_ROOT = Path(__file__).resolve().parents[1]
# Fixed repository-owned synthetic fixtures only, never a user-selected module.
sys.path.insert(0, str(REPO_ROOT / "tests" / "unit"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "evals" / "research"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic offline research software evaluations")
    parser.add_argument("--compare", type=Path, help="Compare definitions with a prior JSON report")
    options = parser.parse_args()
    try:
        from research_eval_fixtures import run_suite

        report = run_suite()
        if options.compare is not None:
            with options.compare.open("rb") as saved:
                raw = saved.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("oversized report")
            previous = ResearchEvalReport.model_validate_json(raw)
            if not comparable_reports(previous, report):
                print('{"status":"error","code":"EVAL_INCOMPARABLE"}')
                return 2
        print(report.model_dump_json())
        return 0 if report.offline_checks_passed else 1
    except Exception:
        print('{"status":"error","code":"EVAL_FAILED"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
