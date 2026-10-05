"""Fixed synthetic NAV references; no inferred source accounting or investment advice."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from ainvest.backtest.reporting import (
    Annualization,
    BenchmarkPoint,
    BenchmarkSeries,
    NavPoint,
    PerformanceReport,
    ReportError,
    ReportIdentity,
    ReportRequest,
    SampleInterval,
    generate_report,
    verify_report,
)
from ainvest.backtest.validation import WalkForwardConfig, walk_forward
from ainvest.strategies.worker.digests import digest_json

CLOCK = datetime(2026, 7, 1, tzinfo=UTC)
DIGEST = digest_json({"synthetic": 1})


def report_inputs() -> ReportRequest:
    return ReportRequest(
        identity=ReportIdentity(
            code_version="synthetic-v1",
            code_digest=DIGEST,
            strategy_version="ma-v1",
            strategy_digest=DIGEST,
            parameter_digest=DIGEST,
            config_digest=DIGEST,
            data_digest=DIGEST,
            cost_digest=DIGEST,
            validation_digest=DIGEST,
        ),
        currency="USD",
        cash_flow_timing="end_of_period",
        points=tuple(
            NavPoint(
                at=CLOCK + timedelta(days=index),
                gross_nav=Decimal(gross),
                net_nav=Decimal(net),
                external_flow=Decimal(0),
                cost=Decimal(index != 0),
                traded_notional=Decimal(index * 10),
                source_digest=DIGEST,
            )
            for index, (gross, net) in enumerate(((100, 100), (110, 109), (99, 97), (108, 105)))
        ),
        interval=SampleInterval(start_at=CLOCK, end_at=CLOCK + timedelta(days=3)),
        benchmark=BenchmarkSeries(
            name="synthetic-benchmark",
            currency="USD",
            basis="total_return",
            data_digest=DIGEST,
            points=tuple(
                BenchmarkPoint(at=CLOCK + timedelta(days=index), level=Decimal(level))
                for index, level in enumerate((100, 105, 100, 110))
            ),
        ),
        annualization=Annualization(seconds_per_period=86400, periods_per_year=252),
        walk_forward=None,
    )


def test_fixed_gross_net_drawdown_turnover_cost_benchmark_and_replay() -> None:
    request = report_inputs()
    result = generate_report(request)
    section = result.sections[0]
    assert section.gross is not None and section.net is not None
    assert section.gross.total_return == Decimal("0.08")
    assert section.net.total_return == Decimal("0.05")
    assert section.gross.maximum_drawdown == Decimal("0.10")
    assert section.net.maximum_drawdown == Decimal("0.110091743119")
    assert section.total_cost == 3
    assert section.turnover == Decimal("0.583941605839")
    assert section.benchmark_return == Decimal("0.10")
    assert section.net_minus_benchmark == Decimal("-0.05")
    assert section.gross.annualized_volatility is not None
    assert result.annualization == request.annualization
    assert result.benchmark_name == "synthetic-benchmark"
    assert result.metric_units == "decimal_ratio"
    assert not result.execution_enabled and not result.scheduled_paper_eligible
    assert "do not predict future" in result.disclosure
    assert result == generate_report(ReportRequest.model_validate_json(request.model_dump_json()))
    assert result == PerformanceReport.model_validate_json(result.model_dump_json())
    verify_report(request, result)
    with localcontext() as context:
        context.prec = 12
        assert result == generate_report(request)


def test_deposits_are_not_returns_or_drawdowns() -> None:
    request = report_inputs()
    points = (
        request.points[0],
        request.points[1].model_copy(
            update={
                "gross_nav": Decimal(200),
                "net_nav": Decimal(200),
                "external_flow": Decimal(100),
            }
        ),
    )
    result = generate_report(
        request.model_copy(
            update={
                "points": points,
                "interval": SampleInterval(start_at=points[0].at, end_at=points[1].at),
                "benchmark": None,
            }
        )
    )
    assert result.sections[0].net is not None
    assert result.sections[0].net.total_return == 0
    assert result.sections[0].net.maximum_drawdown == 0
    assert result.sections[0].net.period_volatility is None


def test_missing_and_unaligned_benchmarks_do_not_fabricate_comparisons() -> None:
    request = report_inputs()
    assert request.benchmark is not None
    for benchmark in (
        None,
        request.benchmark.model_copy(update={"points": request.benchmark.points[:-1]}),
        request.benchmark.model_copy(update={"basis": "price_return"}),
    ):
        section = generate_report(request.model_copy(update={"benchmark": benchmark})).sections[0]
        assert section.net is not None
        assert section.benchmark_return is None and section.net_minus_benchmark is None


def test_missing_interval_or_endpoint_has_no_invented_metrics() -> None:
    request = report_inputs()
    for interval in (None, SampleInterval(start_at=CLOCK, end_at=CLOCK + timedelta(hours=1))):
        section = generate_report(request.model_copy(update={"interval": interval})).sections[0]
        assert section.status == "unavailable" and section.gross is None and section.net is None
        assert section.benchmark_return is None


def test_annualization_is_explicit_and_requires_matching_cadence() -> None:
    request = report_inputs()
    assert request.annualization is not None
    for annualization in (
        None,
        request.annualization.model_copy(update={"seconds_per_period": 3600}),
    ):
        section = generate_report(
            request.model_copy(update={"annualization": annualization})
        ).sections[0]
        assert section.net is not None and section.net.annualized_volatility is None
        assert section.net.period_volatility is not None


def test_train_and_test_boundaries_do_not_double_count_turnover_or_cost() -> None:
    request = report_inputs()
    folds = walk_forward(
        WalkForwardConfig(
            start_at=CLOCK,
            end_at=CLOCK + timedelta(days=3),
            train_seconds=86400,
            test_seconds=2 * 86400,
            step_seconds=2 * 86400,
            embargo_seconds=0,
            parameter_digest=DIGEST,
            data_digest=DIGEST,
            code_version="v1",
        )
    )
    result = generate_report(request.model_copy(update={"walk_forward": folds}))
    assert [section.sample for section in result.sections] == [
        "overall",
        "in_sample",
        "out_of_sample",
    ]
    assert result.sections[1].total_cost == 1 and result.sections[2].total_cost == 2
    assert result.sections[1].traded_notional == 10 and result.sections[2].traded_notional == 50
    mismatch = request.identity.model_copy(
        update={"parameter_digest": digest_json({"different": 1})}
    )
    with pytest.raises(ReportError, match="REPORT_PARAMETER_BINDING"):
        generate_report(request.model_copy(update={"walk_forward": folds, "identity": mismatch}))


def test_invalid_sequence_flow_and_report_tamper_fail_closed() -> None:
    request = report_inputs()
    for points in (
        request.points[::-1],
        request.points * 2,
        (request.points[0].model_copy(update={"external_flow": Decimal(1)}), *request.points[1:]),
        (
            request.points[0],
            request.points[1].model_copy(update={"external_flow": Decimal(1000)}),
            *request.points[2:],
        ),
    ):
        with pytest.raises(ReportError):
            generate_report(request.model_copy(update={"points": points}))
    result = generate_report(request)
    changed = result.model_copy(update={"input_digest": digest_json({"tampered": 1})})
    with pytest.raises(ValueError):
        PerformanceReport.model_validate_json(changed.model_dump_json())
    with pytest.raises(ReportError, match="REPORT_REPLAY_MISMATCH"):
        verify_report(request, changed)


def test_terminal_zero_nav_reports_loss_but_restarting_zero_base_is_rejected() -> None:
    request = report_inputs()
    points = (request.points[0], request.points[1].model_copy(update={"net_nav": Decimal(0)}))
    interval = SampleInterval(start_at=points[0].at, end_at=points[-1].at)
    section = generate_report(
        request.model_copy(update={"points": points, "interval": interval})
    ).sections[0]
    assert (
        section.net is not None
        and section.net.total_return == -1
        and section.net.maximum_drawdown == 1
    )
    with pytest.raises(ReportError, match="REPORT_ZERO_BASE"):
        generate_report(
            request.model_copy(
                update={
                    "points": (*points, request.points[2]),
                    "interval": SampleInterval(start_at=points[0].at, end_at=request.points[2].at),
                }
            )
        )


def test_extreme_benchmark_and_turnover_use_stable_error_codes() -> None:
    request = report_inputs()
    assert request.benchmark is not None
    first = request.benchmark.points[0].model_copy(
        update={"level": Decimal("0.0000000000000000000000000001")}
    )
    benchmark = request.benchmark.model_copy(
        update={"points": (first, *request.benchmark.points[1:])}
    )
    with pytest.raises(ReportError, match="REPORT_BENCHMARK_LIMIT"):
        generate_report(request.model_copy(update={"benchmark": benchmark}))
    points = tuple(
        point.model_copy(
            update={
                "gross_nav": Decimal("0.0000000000000000000000000001"),
                "net_nav": Decimal("0.0000000000000000000000000001"),
            }
        )
        for point in request.points
    )
    with pytest.raises(ReportError, match="REPORT_METRIC_LIMIT"):
        generate_report(request.model_copy(update={"points": points, "benchmark": None}))
