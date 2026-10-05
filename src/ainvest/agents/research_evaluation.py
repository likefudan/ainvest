"""Versioned offline evaluation reports, never production release approval."""

from decimal import Decimal, localcontext
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ainvest.agents.research_archive import ResearchArchive
from ainvest.agents.research_budget import ResearchCostRates, Version
from ainvest.agents.research_builder import replay_research_archive
from ainvest.data.indicators import Digest
from ainvest.schemas.common import DomainModel, MachineCode, Money, StableId, Weight

Status = Literal["complete", "partial", "error"]
Count = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]


class ResearchEvalObservation(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    case_id: StableId
    archive: ResearchArchive
    expected_status: Status
    expected_schema_success: bool
    expected_error_code: MachineCode | None
    expected_last_price: Money | None
    latency_ms: Annotated[int, Field(strict=True, ge=0, le=1_000_000)]


class ResearchEvalThresholds(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    version: Version
    max_latency_ms: Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
    max_input_tokens: Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
    max_output_tokens: Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
    max_case_cost_usd: Annotated[Money, Field(gt=0)]
    # Safety/quality requirements cannot be relaxed through report configuration.
    required_outcome_rate: Literal["1.0"] = "1.0"
    required_evidence_coverage: Literal["1.0"] = "1.0"
    max_unsupported_claims_accepted: Literal[0] = 0


class ResearchEvalCaseResult(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    case_id: StableId
    archive_digest: Digest
    input_digest: Digest
    output_digest: Digest | None
    status: Status
    schema_success: bool
    evidence_coverage: Weight | None
    unsupported_claim_attempts: Count
    unsupported_claims_accepted: Count
    numeric_consistency: bool | None
    latency_ms: Count
    input_tokens: Count
    output_tokens: Count
    requests: Count
    usage_complete: bool
    cost_usd: Money | None
    error_code: MachineCode | None
    failures: Annotated[tuple[MachineCode, ...], Field(max_length=16)]
    passed: bool

    @model_validator(mode="after")
    def _outcome(self) -> Self:
        if self.passed != (not self.failures):
            raise ValueError("case outcome differs from failures")
        return self


class ResearchEvalReport(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    evaluator_version: Literal["research-eval-v1"] = "research-eval-v1"
    qualification: Literal["offline_software_only"] = "offline_software_only"
    model_id: Literal["gpt-5.6-sol"] = "gpt-5.6-sol"
    prompt_version: Literal["research-narrative-v1"] = "research-narrative-v1"
    tool_version: Literal["research-tools-v1"] = "research-tools-v1"
    prompt_digest: Digest
    tool_schema_digest: Digest
    suite_version: Version
    suite_digest: Digest
    thresholds: ResearchEvalThresholds
    rates: ResearchCostRates
    cases: Annotated[tuple[ResearchEvalCaseResult, ...], Field(min_length=1, max_length=32)]
    outcome_rate: Weight
    total_known_cost_usd: Money
    cost_complete: bool
    offline_checks_passed: bool
    scheduled_paper_eligible: Literal[False] = False
    real_ai_eligible: Literal[False] = False

    @model_validator(mode="after")
    def _summary(self) -> Self:
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate evaluation case")
        with localcontext() as context:
            context.prec = 50
            expected_rate = Decimal(sum(case.passed for case in self.cases)) / len(self.cases)
            expected_cost = sum((case.cost_usd or Decimal(0) for case in self.cases), Decimal(0))
        if (
            self.outcome_rate != expected_rate
            or self.total_known_cost_usd != expected_cost
            or self.cost_complete != all(case.cost_usd is not None for case in self.cases)
            or self.offline_checks_passed != all(case.passed for case in self.cases)
        ):
            raise ValueError("inconsistent evaluation summary")
        return self


def _case(
    observation: ResearchEvalObservation,
    thresholds: ResearchEvalThresholds,
    rates: ResearchCostRates,
) -> ResearchEvalCaseResult:
    archive = observation.archive
    payload = archive.payload
    result, record = payload.agent, payload.agent.record
    failures: set[str] = set()
    replay_valid = True
    try:
        packet = replay_research_archive(archive)
    except ValueError:
        replay_valid = False
        packet = None
        failures.add("REPLAY_INVALID")
    schema_success = result.narrative is not None
    if payload.status != observation.expected_status:
        failures.add("OUTCOME_MISMATCH")
    if schema_success != observation.expected_schema_success:
        failures.add("SCHEMA_OUTCOME_MISMATCH")
    if payload.error_code != observation.expected_error_code:
        failures.add("ERROR_CODE_MISMATCH")
    claims = result.narrative.claims() if result.narrative else ()
    coverage = None
    numeric = None
    unsupported = 0
    if payload.packet is not None:
        ids = {item.evidence_id for item in result.evidence}
        cited = sum(all(key in ids for key in claim.evidence_ids) for claim in claims)
        with localcontext() as context:
            context.prec = 50
            coverage = Decimal(cited) / len(claims) if claims and replay_valid else Decimal(0)
        unsupported = 0 if replay_valid and coverage == 1 else max(1, len(claims) - cited)
        numeric = (
            packet is not None
            and observation.expected_last_price is not None
            and packet.market.last_price == observation.expected_last_price
        )
        if coverage != 1:
            failures.add("EVIDENCE_COVERAGE")
        if not numeric:
            failures.add("NUMERIC_INCONSISTENCY")
    elif observation.expected_last_price is not None:
        failures.add("EXPECTED_PACKET_MISSING")
    if unsupported:
        failures.add("UNSUPPORTED_CLAIMS_ACCEPTED")
    if observation.latency_ms > thresholds.max_latency_ms:
        failures.add("LATENCY_LIMIT")
    if (
        record.input_tokens > thresholds.max_input_tokens
        or record.output_tokens > thresholds.max_output_tokens
    ):
        failures.add("TOKEN_LIMIT")
    cost = (
        rates.estimate(record.input_tokens, record.output_tokens) if record.usage_complete else None
    )
    if cost is None:
        failures.add("USAGE_UNKNOWN")
    elif cost > thresholds.max_case_cost_usd:
        failures.add("COST_LIMIT")
    return ResearchEvalCaseResult(
        case_id=observation.case_id,
        archive_digest=archive.digest,
        input_digest=record.input_digest,
        output_digest=record.output_digest,
        status=payload.status,
        schema_success=schema_success,
        evidence_coverage=coverage,
        numeric_consistency=numeric,
        unsupported_claim_attempts=int(
            result.error_code
            in {"UNSUPPORTED_CLAIM", "UNSUPPORTED_EVIDENCE", "PROHIBITED_NARRATIVE"}
        ),
        unsupported_claims_accepted=unsupported,
        latency_ms=observation.latency_ms,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        requests=record.requests,
        usage_complete=record.usage_complete,
        cost_usd=cost,
        error_code=payload.error_code,
        failures=tuple(sorted(failures)),
        passed=not failures,
    )


def evaluate_research(
    observations: tuple[ResearchEvalObservation, ...],
    *,
    suite_version: str,
    suite_digest: str,
    thresholds: ResearchEvalThresholds,
    rates: ResearchCostRates,
) -> ResearchEvalReport:
    if not 1 <= len(observations) <= 32:
        raise ValueError("evaluation suite size invalid")
    observations = tuple(
        ResearchEvalObservation.model_validate_json(item.model_dump_json()) for item in observations
    )
    thresholds = ResearchEvalThresholds.model_validate_json(thresholds.model_dump_json())
    rates = ResearchCostRates.model_validate_json(rates.model_dump_json())
    prompt_digests = {item.archive.payload.agent.record.prompt_digest for item in observations}
    tool_digests = {item.archive.payload.agent.record.tool_schema_digest for item in observations}
    if len(prompt_digests) != 1 or len(tool_digests) != 1:
        raise ValueError("mixed evaluation configurations")
    cases = tuple(_case(item, thresholds, rates) for item in observations)
    with localcontext() as context:
        context.prec = 50
        outcome = Decimal(sum(case.passed for case in cases)) / len(cases)
        cost = sum((case.cost_usd or Decimal(0) for case in cases), Decimal(0))
    return ResearchEvalReport(
        suite_version=suite_version,
        suite_digest=suite_digest,
        thresholds=thresholds,
        rates=rates,
        prompt_digest=next(iter(prompt_digests)),
        tool_schema_digest=next(iter(tool_digests)),
        cases=cases,
        outcome_rate=outcome,
        total_known_cost_usd=cost,
        cost_complete=all(case.cost_usd is not None for case in cases),
        offline_checks_passed=all(case.passed for case in cases),
    )


def comparable_reports(left: ResearchEvalReport, right: ResearchEvalReport) -> bool:
    """Compare metrics only for identical suite/rates/threshold definitions.

    Model/prompt/tool metadata is retained separately for explicit version review;
    this does not authorize an upgrade or a real-model qualification.
    """
    left = ResearchEvalReport.model_validate_json(left.model_dump_json())
    right = ResearchEvalReport.model_validate_json(right.model_dump_json())
    return (
        left.suite_version == right.suite_version
        and left.suite_digest == right.suite_digest
        and left.rates == right.rates
        and left.thresholds == right.thresholds
        and tuple(case.case_id for case in left.cases)
        == tuple(case.case_id for case in right.cases)
    )
