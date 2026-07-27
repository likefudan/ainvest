"""CLI entry point for third-party strategy conformance CI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from ainvest.strategies.definitions import StrategyDefinition, StrategyError
from ainvest.strategies.registry import RegistryLoadConfig, load_strategy_registry
from ainvest.strategy_conformance.suite import (
    render_human_report,
    report_to_json,
    run_conformance_suite,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ainvest-strategy-conformance",
        description=(
            "Run the ainvest strategy conformance suite against an installed "
            "strategy plugin (P03-T5)."
        ),
    )
    parser.add_argument(
        "--strategy",
        required=True,
        help="Registered strategy name (for example: moving_average)",
    )
    parser.add_argument(
        "--plugin-id",
        default=None,
        help="Optional plugin_id allowlist pin; when set, only that plugin is loaded",
    )
    parser.add_argument(
        "--plugin-version",
        default=None,
        help="Required when --plugin-id is set; pinned plugin version for allowlist",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write machine-readable ConformanceReport JSON to this path",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human-readable report (JSON still written when requested)",
    )
    return parser


def resolve_definition(
    strategy_name: str,
    *,
    plugin_id: str | None = None,
    plugin_version: str | None = None,
) -> StrategyDefinition:
    allowlist = None
    if plugin_id is not None:
        if not plugin_version:
            raise SystemExit("--plugin-version is required when --plugin-id is set")
        allowlist = {plugin_id: plugin_version}
    registry = load_strategy_registry(
        RegistryLoadConfig(allowlist=allowlist) if allowlist else RegistryLoadConfig(),
    )
    definition = registry.get(strategy_name)
    if plugin_id is not None and definition.metadata.plugin_id != plugin_id:
        raise StrategyError(
            f"strategy {strategy_name!r} belongs to plugin "
            f"{definition.metadata.plugin_id!r}, not {plugin_id!r}",
            code="STRATEGY_UNKNOWN_PLUGIN",
        )
    return definition


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        definition = resolve_definition(
            args.strategy,
            plugin_id=args.plugin_id,
            plugin_version=args.plugin_version,
        )
    except StrategyError as exc:
        print(f"conformance: failed to load strategy: {exc}", file=sys.stderr)
        return 2

    report = run_conformance_suite(definition)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(report_to_json(report), encoding="utf-8")
    if not args.quiet:
        sys.stdout.write(render_human_report(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
