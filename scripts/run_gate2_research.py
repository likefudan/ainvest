"""Print offline Gate 2 evidence; no deployment, credentials or activation."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/unit"))


def main() -> int:
    try:
        from gate2_fixtures import run_gate2

        report = run_gate2()
        print(report.model_dump_json())
        return 0 if report.offline_checks_passed else 1
    except Exception:
        print('{"status":"error","code":"GATE2_OFFLINE_CHECK_FAILED"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
