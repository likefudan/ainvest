"""Unit tests for strategy conformance suite helpers and negative plugins."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ainvest.strategies.definitions import StrategyDefinition
from ainvest.strategies.reference.moving_average.plugin import METADATA
from ainvest.strategies.reference.moving_average.strategy import MovingAverageStrategy
from ainvest.strategy_conformance import (
    ConformanceCode,
    ConformanceStatus,
    check_ids,
    render_human_report,
    report_to_json,
    run_conformance_suite,
)
from ainvest.strategy_conformance.checks.behavior import (
    check_determinism,
    check_exception_handling,
    check_no_future_data,
)
from ainvest.strategy_conformance.checks.isolation import (
    check_broker_imports,
    check_network_isolation,
    check_secret_access,
)
from ainvest.strategy_conformance.checks.metadata import check_api_range, check_metadata
from ainvest.strategy_conformance.suite import SUITE_VERSION
from strategy_conformance.invalid._common import definition_for
from strategy_conformance.invalid.broker_import import BrokerImportStrategy
from strategy_conformance.invalid.network_import import NetworkImportStrategy
from strategy_conformance.invalid.nondeterministic import NondeterministicStrategy
from strategy_conformance.invalid.raising import RaisingStrategy
from strategy_conformance.invalid.secret_probe import SecretProbeStrategy
from strategy_conformance.invalid.wall_clock import WallClockStrategy


@pytest.fixture
def ma_definition() -> StrategyDefinition:
    return StrategyDefinition.from_type(MovingAverageStrategy, metadata=METADATA)


def test_check_ids_cover_required_surface() -> None:
    ids = set(check_ids())
    required = {
        "metadata",
        "api_range",
        "hooks",
        "parameters",
        "signal_schemas",
        "determinism",
        "no_future_data",
        "timeout",
        "exceptions",
        "paper_example",
        "broker_imports",
        "network",
        "secret_access",
    }
    assert required <= ids


def test_reference_ma_passes_full_suite(ma_definition: StrategyDefinition) -> None:
    report = run_conformance_suite(ma_definition)
    assert report.passed is True
    assert report.suite_version == SUITE_VERSION
    assert report.plugin_id == "moving_average"
    assert report.strategy_name == "moving_average"
    assert all(check.status is ConformanceStatus.PASSED for check in report.checks)
    assert all(check.code is ConformanceCode.OK for check in report.checks)


def test_report_json_and_human_round_trip(ma_definition: StrategyDefinition) -> None:
    report = run_conformance_suite(
        ma_definition,
        checks=(("metadata", check_metadata), ("api_range", check_api_range)),
    )
    payload = json.loads(report_to_json(report))
    assert payload["passed"] is True
    assert payload["suite_version"] == SUITE_VERSION
    human = render_human_report(report)
    assert "PASSED" in human
    assert "metadata" in human


def test_incompatible_api_range_fails_stable_code(ma_definition: StrategyDefinition) -> None:
    bad_meta = replace(ma_definition.metadata, ainvest_strategy_api=">=99.0.0,<100.0.0")
    bad = replace(ma_definition, metadata=bad_meta)
    result = check_api_range(bad)
    assert result.status is ConformanceStatus.FAILED
    assert result.code is ConformanceCode.API_INCOMPATIBLE


def test_wall_clock_plugin_fails_future_data() -> None:
    definition = definition_for(WallClockStrategy)
    result = check_no_future_data(definition)
    assert result.status is ConformanceStatus.FAILED
    assert result.code is ConformanceCode.FUTURE_DATA


def test_network_import_plugin_fails_network() -> None:
    definition = definition_for(NetworkImportStrategy)
    result = check_network_isolation(definition)
    assert result.status is ConformanceStatus.FAILED
    assert result.code is ConformanceCode.NETWORK_ACCESS


def test_broker_import_plugin_fails_broker() -> None:
    definition = definition_for(BrokerImportStrategy)
    result = check_broker_imports(definition)
    assert result.status is ConformanceStatus.FAILED
    assert result.code is ConformanceCode.BROKER_IMPORT


def test_raising_plugin_fails_exceptions() -> None:
    definition = definition_for(RaisingStrategy)
    result = check_exception_handling(definition)
    assert result.status is ConformanceStatus.FAILED
    assert result.code is ConformanceCode.EXCEPTION


def test_raising_plugin_network_preserves_worker_failure() -> None:
    definition = definition_for(RaisingStrategy)
    result = check_network_isolation(definition)
    assert result.status is ConformanceStatus.FAILED
    assert result.code is ConformanceCode.WORKER_FAILURE


def test_raising_plugin_secret_preserves_worker_failure() -> None:
    definition = definition_for(RaisingStrategy)
    result = check_secret_access(definition)
    assert result.status is ConformanceStatus.FAILED
    assert result.code is ConformanceCode.WORKER_FAILURE


def test_raising_plugin_paper_preserves_worker_failure() -> None:
    from ainvest.strategy_conformance.checks.behavior import check_paper_example

    definition = definition_for(RaisingStrategy)
    result = check_paper_example(definition)
    assert result.status is ConformanceStatus.FAILED
    assert result.code is ConformanceCode.WORKER_FAILURE


def test_nondeterministic_plugin_fails_determinism() -> None:
    definition = definition_for(NondeterministicStrategy)
    result = check_determinism(definition)
    assert result.status is ConformanceStatus.FAILED
    assert result.code is ConformanceCode.NONDETERMINISTIC


def test_nondeterministic_plugin_fails_future_data() -> None:
    definition = definition_for(NondeterministicStrategy)
    result = check_no_future_data(definition)
    assert result.status is ConformanceStatus.FAILED
    assert result.code is ConformanceCode.FUTURE_DATA
    assert "time.time_ns" in result.details.get("offenders", "")


def test_bare_time_import_fails_future_data() -> None:
    from strategy_conformance.invalid.bare_time import BareTimeStrategy

    definition = definition_for(BareTimeStrategy)
    result = check_no_future_data(definition)
    assert result.status is ConformanceStatus.FAILED
    assert result.code is ConformanceCode.FUTURE_DATA
    assert "time.time" in result.details.get("offenders", "")


def test_module_imports_forbidden_fails_closed_on_missing_source() -> None:
    from ainvest.strategy_conformance.checks._util import (
        FORBIDDEN_NETWORK_MODULES,
        SourceScanError,
        module_imports_forbidden,
    )

    with pytest.raises(SourceScanError):
        module_imports_forbidden(
            "/nonexistent/path/for/conformance_scan.py",
            FORBIDDEN_NETWORK_MODULES,
        )


def test_secret_probe_fails_secret_access() -> None:
    definition = definition_for(SecretProbeStrategy)
    result = check_secret_access(definition)
    assert result.status is ConformanceStatus.FAILED
    assert result.code is ConformanceCode.SECRET_ACCESS


def test_metadata_check_rejects_mismatched_name(ma_definition: StrategyDefinition) -> None:
    bad = replace(ma_definition, name="other_name")
    result = check_metadata(bad)
    assert result.status is ConformanceStatus.FAILED
    assert result.code is ConformanceCode.METADATA_INVALID
