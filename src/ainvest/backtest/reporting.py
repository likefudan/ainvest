"""Deterministic historical NAV metrics with explicit cash flows and limitations."""

from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from itertools import pairwise
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ainvest.backtest.models import Version
from ainvest.backtest.validation import WalkForwardResult
from ainvest.data.indicators import Digest
from ainvest.schemas.common import (
    CurrencyCode,
    DecimalString,
    DomainModel,
    MachineCode,
    NonNegativeDecimal,
    PositiveDecimal,
    UtcDateTime,
)
from ainvest.strategies.worker.digests import digest_json

Disclosure = Literal[
    "Historical and simulated results do not predict future performance. "
    "This report is not investment advice or permission to trade."
]
DISCLOSURE: Disclosure = (
    "Historical and simulated results do not predict future performance. "
    "This report is not investment advice or permission to trade."
)
Amount = Annotated[NonNegativeDecimal, Field(le=1_000_000_000_000)]
_QUANTUM = Decimal("0.000000000001")


class ReportError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ReportIdentity(DomainModel):
    code_version: Version
    code_digest: Digest
    strategy_version: Version
    strategy_digest: Digest
    parameter_digest: Digest
    config_digest: Digest
    data_digest: Digest
    cost_digest: Digest
    validation_digest: Digest


class NavPoint(DomainModel):
    """Caller-supplied NAV/cost facts; fees are already reflected in net_nav."""

    at: UtcDateTime
    gross_nav: Amount
    net_nav: Amount
    external_flow: Annotated[DecimalString, Field(ge=-1_000_000_000_000, le=1_000_000_000_000)]
    cost: Amount
    traded_notional: Amount
    source_digest: Digest


class BenchmarkPoint(DomainModel):
    at: UtcDateTime
    level: Annotated[PositiveDecimal, Field(le=1_000_000_000_000)]


class BenchmarkSeries(DomainModel):
    name: Version
    currency: CurrencyCode
    basis: Literal["price_return", "total_return"]
    data_digest: Digest
    points: Annotated[tuple[BenchmarkPoint, ...], Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def _clocks(self) -> Self:
        clocks = tuple(point.at for point in self.points)
        if tuple(sorted(set(clocks))) != clocks:
            raise ValueError("benchmark clocks must be ordered and unique")
        return self


class SampleInterval(DomainModel):
    start_at: UtcDateTime
    end_at: UtcDateTime

    @model_validator(mode="after")
    def _interval(self) -> Self:
        if self.end_at <= self.start_at:
            raise ValueError("sample interval must be positive")
        return self


class Annualization(DomainModel):
    """Explicit caller convention, not inferred exchange cadence or 252-day default."""

    seconds_per_period: Annotated[int, Field(strict=True, ge=1, le=31_557_600)]
    periods_per_year: Annotated[int, Field(strict=True, ge=1, le=1_000_000)]


class ReportRequest(DomainModel):
    identity: ReportIdentity
    currency: CurrencyCode
    cash_flow_timing: Literal["end_of_period"]
    points: Annotated[tuple[NavPoint, ...], Field(min_length=2, max_length=500)]
    interval: SampleInterval | None
    benchmark: BenchmarkSeries | None
    annualization: Annualization | None
    walk_forward: WalkForwardResult | None

    @model_validator(mode="after")
    def _points(self) -> Self:
        clocks = tuple(point.at for point in self.points)
        if tuple(sorted(set(clocks))) != clocks:
            raise ValueError("NAV clocks must be ordered and unique")
        first = self.points[0]
        if first.external_flow or first.cost or first.traded_notional:
            raise ValueError("opening valuation cannot carry unknown preceding-period flows/costs")
        return self


class CurveMetrics(DomainModel):
    total_return: DecimalString
    maximum_drawdown: Annotated[NonNegativeDecimal, Field(le=1)]
    period_volatility: NonNegativeDecimal | None
    annualized_volatility: NonNegativeDecimal | None
    return_observations: Annotated[int, Field(ge=1, le=499)]


class ReportSection(DomainModel):
    label: Version
    sample: Literal["overall", "in_sample", "out_of_sample"]
    status: Literal["complete", "unavailable"]
    interval: SampleInterval | None
    gross: CurveMetrics | None
    net: CurveMetrics | None
    total_cost: NonNegativeDecimal | None
    traded_notional: NonNegativeDecimal | None
    turnover: NonNegativeDecimal | None
    benchmark_return: DecimalString | None
    gross_minus_benchmark: DecimalString | None
    net_minus_benchmark: DecimalString | None
    limitations: tuple[MachineCode, ...]

    @model_validator(mode="after")
    def _status(self) -> Self:
        if self.status == "complete":
            if self.interval is None or self.gross is None or self.net is None:
                raise ValueError("complete section lacks interval/metrics")
        elif any(
            value is not None
            for value in (
                self.gross,
                self.net,
                self.total_cost,
                self.traded_notional,
                self.turnover,
                self.benchmark_return,
                self.gross_minus_benchmark,
                self.net_minus_benchmark,
            )
        ):
            raise ValueError("unavailable section contains fabricated metrics")
        if self.benchmark_return is None and (
            self.gross_minus_benchmark is not None or self.net_minus_benchmark is not None
        ):
            raise ValueError("comparison lacks benchmark")
        return self


class PerformanceReport(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    reporter_version: Literal["decimal-nav-metrics-v1"] = "decimal-nav-metrics-v1"
    input_digest: Digest
    identity: ReportIdentity
    currency: CurrencyCode
    cash_flow_timing: Literal["end_of_period"] = "end_of_period"
    metric_units: Literal["decimal_ratio"] = "decimal_ratio"
    volatility_convention: Literal["sample_standard_deviation_ddof_1"] = (
        "sample_standard_deviation_ddof_1"
    )
    annualization: Annualization | None
    sections: Annotated[tuple[ReportSection, ...], Field(min_length=1, max_length=129)]
    benchmark_digest: Digest | None
    benchmark_name: Version | None
    benchmark_basis: Literal["price_return", "total_return"] | None
    walk_forward_digest: Digest | None
    disclosure: Disclosure = DISCLOSURE
    source_kind: Literal["caller_supplied_historical_nav"] = "caller_supplied_historical_nav"
    turnover_convention: Literal["gross_traded_notional_over_mean_net_nav"] = (
        "gross_traded_notional_over_mean_net_nav"
    )
    execution_enabled: Literal[False] = False
    scheduled_paper_eligible: Literal[False] = False
    report_digest: Digest

    @model_validator(mode="after")
    def _digest(self) -> Self:
        if self.report_digest != digest_json(
            self.model_dump(mode="json", exclude={"report_digest"})
        ):
            raise ValueError("report digest differs")
        return self


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_QUANTUM)


def _curve(
    values: tuple[Decimal, ...], flows: tuple[Decimal, ...], annual: Annualization | None
) -> CurveMetrics:
    returns: list[Decimal] = []
    index = peak = Decimal(1)
    drawdown = Decimal(0)
    for previous, current, flow in zip(values, values[1:], flows[1:], strict=False):
        if previous <= 0:
            raise ReportError("REPORT_ZERO_BASE")
        adjusted = current - flow
        if adjusted < 0:
            raise ReportError("REPORT_CASH_FLOW_INVALID")
        period_return = adjusted / previous - 1
        returns.append(period_return)
        index *= 1 + period_return
        if index > Decimal("1e24") or abs(period_return) > Decimal("1e24"):
            raise ReportError("REPORT_METRIC_LIMIT")
        peak = max(peak, index)
        drawdown = max(drawdown, 1 - index / peak)
    volatility = None
    annual_volatility = None
    if len(returns) >= 2:
        mean = sum(returns, Decimal(0)) / len(returns)
        variance = sum(((value - mean) ** 2 for value in returns), Decimal(0)) / (len(returns) - 1)
        volatility = variance.sqrt()
        if annual is not None:
            annual_volatility = volatility * Decimal(annual.periods_per_year).sqrt()
    return CurveMetrics(
        total_return=_quantize(index - 1),
        maximum_drawdown=_quantize(drawdown),
        period_volatility=_quantize(volatility) if volatility is not None else None,
        annualized_volatility=_quantize(annual_volatility)
        if annual_volatility is not None
        else None,
        return_observations=len(returns),
    )


def _section(
    request: ReportRequest,
    interval: SampleInterval | None,
    label: str,
    sample: Literal["overall", "in_sample", "out_of_sample"],
) -> ReportSection:
    points = tuple(
        point
        for point in request.points
        if interval is not None and interval.start_at <= point.at <= interval.end_at
    )
    if (
        interval is None
        or len(points) < 2
        or points[0].at != interval.start_at
        or points[-1].at != interval.end_at
    ):
        return ReportSection(
            label=label,
            sample=sample,
            status="unavailable",
            interval=interval,
            gross=None,
            net=None,
            total_cost=None,
            traded_notional=None,
            turnover=None,
            benchmark_return=None,
            gross_minus_benchmark=None,
            net_minus_benchmark=None,
            limitations=("SAMPLE_INTERVAL_UNAVAILABLE",),
        )
    limitations: list[str] = []
    annual = request.annualization
    if annual is None:
        limitations.append("ANNUALIZATION_UNSPECIFIED")
    elif any(
        (right.at - left.at).total_seconds() != annual.seconds_per_period
        for left, right in pairwise(points)
    ):
        limitations.append("ANNUALIZATION_CADENCE_MISMATCH")
        annual = None
    flows = tuple(point.external_flow for point in points)
    gross = _curve(tuple(point.gross_nav for point in points), flows, annual)
    net = _curve(tuple(point.net_nav for point in points), flows, annual)
    if net.period_volatility is None:
        limitations.append("INSUFFICIENT_VOLATILITY_OBSERVATIONS")
    total_cost = sum((point.cost for point in points[1:]), Decimal(0))
    traded = sum((point.traded_notional for point in points[1:]), Decimal(0))
    average = sum((point.net_nav for point in points), Decimal(0)) / len(points)
    benchmark_return = None
    benchmark = request.benchmark
    if benchmark is None:
        limitations.append("BENCHMARK_UNAVAILABLE")
    elif benchmark.currency != request.currency:
        limitations.append("BENCHMARK_CURRENCY_MISMATCH")
    elif benchmark.basis != "total_return":
        limitations.append("BENCHMARK_PRICE_RETURN_ONLY")
    else:
        reference = tuple(
            point for point in benchmark.points if interval.start_at <= point.at <= interval.end_at
        )
        if tuple(point.at for point in reference) != tuple(point.at for point in points):
            limitations.append("BENCHMARK_UNALIGNED")
        else:
            relative = reference[-1].level / reference[0].level - 1
            if abs(relative) > Decimal("1e24"):
                raise ReportError("REPORT_BENCHMARK_LIMIT")
            benchmark_return = _quantize(relative)
    turnover = traded / average if average > 0 else None
    if turnover is not None and turnover > Decimal("1e24"):
        raise ReportError("REPORT_METRIC_LIMIT")
    return ReportSection(
        label=label,
        sample=sample,
        status="complete",
        interval=interval,
        gross=gross,
        net=net,
        total_cost=total_cost,
        traded_notional=traded,
        turnover=_quantize(turnover) if turnover is not None else None,
        benchmark_return=benchmark_return,
        gross_minus_benchmark=_quantize(gross.total_return - benchmark_return)
        if benchmark_return is not None
        else None,
        net_minus_benchmark=_quantize(net.total_return - benchmark_return)
        if benchmark_return is not None
        else None,
        limitations=tuple(limitations),
    )


def generate_report(request: ReportRequest) -> PerformanceReport:
    """Compute one bounded reproducible report; never infer NAV from decision-only replay."""
    try:
        if len(request.model_dump_json().encode()) > 1_048_576:
            raise ValueError("input size")
        request = ReportRequest.model_validate_json(request.model_dump_json())
    except ValueError:
        raise ReportError("REPORT_INPUT_INVALID") from None
    if request.walk_forward is not None and any(
        fold.parameter_digest != request.identity.parameter_digest
        for fold in request.walk_forward.folds
    ):
        raise ReportError("REPORT_PARAMETER_BINDING")
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        sections = [_section(request, request.interval, "overall", "overall")]
        if request.walk_forward is not None:
            for fold in request.walk_forward.folds:
                sections.extend(
                    (
                        _section(
                            request,
                            SampleInterval(start_at=fold.train_start, end_at=fold.train_end),
                            f"fold_{fold.index}_in",
                            "in_sample",
                        ),
                        _section(
                            request,
                            SampleInterval(start_at=fold.test_start, end_at=fold.test_end),
                            f"fold_{fold.index}_out",
                            "out_of_sample",
                        ),
                    )
                )
        # Construct the complete wire shape through the model's own defaults, then hash it.
        payload = {
            "schema_version": "1.0",
            "reporter_version": "decimal-nav-metrics-v1",
            "input_digest": digest_json(request.model_dump(mode="json")),
            "identity": request.identity.model_dump(mode="json"),
            "currency": request.currency,
            "cash_flow_timing": request.cash_flow_timing,
            "metric_units": "decimal_ratio",
            "volatility_convention": "sample_standard_deviation_ddof_1",
            "annualization": request.annualization.model_dump(mode="json")
            if request.annualization is not None
            else None,
            "sections": [section.model_dump(mode="json") for section in sections],
            "benchmark_digest": digest_json(request.benchmark.model_dump(mode="json"))
            if request.benchmark is not None
            else None,
            "benchmark_name": request.benchmark.name if request.benchmark is not None else None,
            "benchmark_basis": request.benchmark.basis if request.benchmark is not None else None,
            "walk_forward_digest": digest_json(request.walk_forward.model_dump(mode="json"))
            if request.walk_forward is not None
            else None,
            "disclosure": DISCLOSURE,
            "source_kind": "caller_supplied_historical_nav",
            "turnover_convention": "gross_traded_notional_over_mean_net_nav",
            "execution_enabled": False,
            "scheduled_paper_eligible": False,
        }
        return PerformanceReport.model_validate({**payload, "report_digest": digest_json(payload)})


def verify_report(request: ReportRequest, report: PerformanceReport) -> None:
    """Reject any altered metric/source binding by recomputing the retained input."""
    if generate_report(request) != report:
        raise ReportError("REPORT_REPLAY_MISMATCH")
