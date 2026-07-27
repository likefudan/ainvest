"""CLI entry point for the deterministic paper flow (P03-T16)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ainvest.orchestrator.paper_loop import run_paper_flow
from ainvest.orchestrator.types import PaperFlowTerminal


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ainvest-paper-flow",
        description=(
            "Run the deterministic paper orchestration loop from a fixed "
            "ResearchPacket fixture (P03-T16). Never auto-approves."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stop at APPROVAL_PENDING without consuming approval (default path)",
    )
    parser.add_argument(
        "--inject-approval",
        action="store_true",
        help="Explicitly consume the approval stub and continue to paper submit",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write a machine-readable summary JSON to this path",
    )
    parser.add_argument(
        "--expire-approval",
        action="store_true",
        help="Advance past challenge TTL before consume (requires --inject-approval)",
    )
    parser.add_argument(
        "--partial-fill",
        action="store_true",
        help="Inject market liquidity below order quantity (requires --inject-approval)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from ainvest.orchestrator.fixtures import make_paper_flow_config

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.expire_approval and not args.inject_approval:
        parser.error("--expire-approval requires --inject-approval")
    if args.partial_fill and not args.inject_approval:
        parser.error("--partial-fill requires --inject-approval")
    if args.dry_run and args.inject_approval:
        parser.error("--dry-run and --inject-approval are mutually exclusive")

    inject = bool(args.inject_approval) and not bool(args.dry_run)
    config = make_paper_flow_config(
        inject_approval=inject,
        expire_approval=bool(args.expire_approval),
        market_liquidity="1" if args.partial_fill else "100",
    )
    result = run_paper_flow(config)

    summary = {
        "terminal": result.terminal.value,
        "lifecycle": result.lifecycle.value,
        "correlation_id": result.correlation_id,
        "proposal_id": result.proposal_id,
        "order_hash": result.order_hash,
        "steps": [step.name for step in result.steps],
        "digests": result.digests,
        "filled_quantity": str(result.filled_quantity),
        "conservation_ok": result.conservation_ok,
        "error": result.error,
    }
    rendered = json.dumps(summary, indent=2, sort_keys=True)
    print(rendered)
    if args.json_out is not None:
        args.json_out.write_text(rendered + "\n", encoding="utf-8")

    if result.terminal in {
        PaperFlowTerminal.FAILED,
    }:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
