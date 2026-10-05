"""Fixed offline research-to-Paper evidence, not production composition."""

import asyncio
import json
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

from indicator_fixtures import snapshot
from pydantic import model_validator
from research_tool_fixtures import fixtures

from ainvest.agents.research_agent import AgentContext, ResearchAgent
from ainvest.agents.research_archive import ResearchArchive, ResearchBuildRequest
from ainvest.agents.research_builder import build_research_archive, replay_research_archive
from ainvest.agents.research_evaluation import ResearchEvalReport
from ainvest.agents.research_models import ModelReply, ResearchLimits
from ainvest.agents.tools import HistoryInput, ResearchTools
from ainvest.agents.tools.models import PortfolioMetrics, ToolScope
from ainvest.data.indicators import Digest, IndicatorRun, digest_bytes
from ainvest.execution import PaperCostModel
from ainvest.execution.robinhood.mappers import map_equity_quotes
from ainvest.execution.robinhood.pins import EXPECTED_MANIFEST_DIGEST, PINNED_MANIFEST_VERSION
from ainvest.execution.robinhood.prose import contains_provider_prose, discard_provider_prose
from ainvest.execution.robinhood.read_client import GatewayReadResult
from ainvest.orchestrator import run_paper_flow
from ainvest.orchestrator.fixtures import (
    make_exposure_inputs,
    make_instrument,
    make_risk_config,
    make_sizing_config,
)
from ainvest.orchestrator.paper_loop import PaperFlowConfig
from ainvest.risk.models import AllowlistEntry
from ainvest.schemas.common import DomainModel, MachineCode, NonNegativeDecimal
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.portfolio import AccountScope, ExposureSnapshot, PortfolioSnapshot
from ainvest.schemas.strategy import (
    StrategyContext,
    StrategyState,
    StrategyStateItem,
    StrategyStateValueKind,
)
from ainvest.strategies.worker.digests import digest_json

ROOT = Path(__file__).resolve().parents[2]


def build_gate2_archive(
    mode: str = "valid",
) -> tuple[ResearchArchive, PortfolioSnapshot, MarketQuote]:
    """Use only synthetic readers/fake model; failures return archived no-packet outcomes."""
    if mode not in {"valid", "stale", "timeout", "unsupported", "forged"}:
        raise ValueError("GATE2_MODE_INVALID")
    scope, sources = fixtures()
    scope = scope.model_copy(update={"run_id": "research_gate2_" + mode + "_0001"})
    saved = snapshot()
    # Fresh delivery of a synthetic observation at this test cutoff. Do not
    # widen the existing 30-second risk receipt-skew limit to fit a stale receipt.
    quote = saved.inputs.quote.model_copy(
        update={
            "provenance": saved.inputs.quote.provenance.model_copy(
                update={"received_at": scope.as_of}
            )
        }
    )
    portfolio = PortfolioSnapshot(
        snapshot_id="portfolio_gate2_0001",
        account_scope=AccountScope.PAPER,
        as_of=scope.as_of,
        cash=Decimal(10_000),
        buying_power=Decimal(10_000),
        equity=Decimal(10_000),
        positions=(),
        open_orders=(),
        exposure=ExposureSnapshot(
            cash=Decimal(10_000),
            equity=Decimal(10_000),
            gross_market_value=Decimal(0),
            net_market_value=Decimal(0),
            largest_position_weight=Decimal(0),
        ),
        provenance=quote.provenance,
    )
    if mode == "stale":
        old = scope.as_of - timedelta(minutes=5)
        quote = quote.model_copy(
            update={
                "provenance": quote.provenance.model_copy(
                    update={"observed_at": old, "received_at": old}
                )
            }
        )

    def quote_reader(request: ToolScope) -> MarketQuote:
        if mode == "timeout":
            raise TimeoutError("private_canary")
        return quote

    sources = replace(sources, quote=quote_reader, portfolio=lambda request: portfolio)
    limits = ResearchLimits(required_tools=("quote", "history", "indicators", "concentration"))
    history = HistoryInput(expected=saved.inputs.expected)

    async def model(context: AgentContext) -> ModelReply:
        observed = await context.tools.quote()
        await context.tools.history()
        await context.tools.indicators()
        await context.tools.concentration()
        citation = observed.evidence[0]
        return ModelReply(
            output_json=json.dumps(
                {
                    "schema_version": "1.0",
                    "bull_case": [
                        {
                            "text": "Unsupported statement."
                            if mode == "unsupported"
                            else citation.summary,
                            "evidence_ids": ["tool_forged_0001"]
                            if mode == "forged"
                            else [citation.evidence_id],
                            "kind": "observation",
                        }
                    ],
                    "bear_case": [],
                    "risks": [],
                    "open_questions": [],
                }
            ),
            request_ids=("req_gate2_synthetic",),
            response_ids=("resp_gate2_synthetic",),
            input_tokens=100,
            output_tokens=30,
            requests=1,
        )

    request = ResearchBuildRequest(
        scope=scope,
        limits=limits,
        history=history,
        started_at=scope.as_of + timedelta(seconds=1),
        completed_at=scope.as_of + timedelta(seconds=2),
        code_version="gate2-offline-v1",
        config_version="synthetic-gate2-v1",
    )
    with ResearchTools(scope, sources) as tools:
        result = asyncio.run(ResearchAgent(model, limits=limits).run(tools, history=history))
        archive = build_research_archive(request, result, tools=tools)
    return archive, portfolio, quote


class Gate2Report(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    harness_version: Literal["gate2-offline-v1"] = "gate2-offline-v1"
    qualification: Literal["offline_software_evidence"] = "offline_software_evidence"
    model_port: Literal["synthetic_only"] = "synthetic_only"
    offline_checks_passed: bool
    trace_verified: bool
    archive_replayed: bool
    archive_digest: Digest
    packet_digest: Digest
    code_digest: Digest
    tool_capture_digests: tuple[Digest, ...]
    pending_terminal: str
    pending_filled_quantity: NonNegativeDecimal
    explicit_test_approval_terminal: str
    test_ledger_conservation: bool
    paper_semantic_digest: Digest
    saved_contract_fixture_digest: Digest
    recorded_read_trading_eligible: Literal[False] = False
    real_provider_capture_authenticated: Literal[False] = False
    eval_suite_digest: Digest
    eval_case_count: int
    eval_checks_passed: bool
    eval_semantic_digest: Digest
    full_gate_accepted: Literal[False] = False
    real_ai_eligible: Literal[False] = False
    scheduled_paper_eligible: Literal[False] = False
    owner_action_required: Literal["DEC009_PROJECT_BUDGET_SECRET_REFERENCE"] = (
        "DEC009_PROJECT_BUDGET_SECRET_REFERENCE"
    )
    unresolved: tuple[MachineCode, ...] = (
        "DEC009_UNRESOLVED",
        "REAL_MODEL_EVALUATION_PENDING",
        "AUTHENTICATED_PROVIDER_CAPTURE_PENDING",
        "DURABLE_BUDGET_NOTIFIER_PENDING",
    )

    @model_validator(mode="after")
    def _checks(self) -> Self:
        passed = (
            self.trace_verified
            and self.archive_replayed
            and self.test_ledger_conservation
            and self.pending_terminal == "APPROVAL_PENDING"
            and self.pending_filled_quantity == 0
            and self.explicit_test_approval_terminal == "FILLED"
            and self.eval_checks_passed
            and self.eval_case_count == 12
        )
        if self.offline_checks_passed != passed:
            raise ValueError("gate summary differs from checks")
        return self


def _read_contract_fixture() -> Digest:
    path = ROOT / "tests/fixtures/rh_mcp/v0.4.4/p06-t1-part1/get_equity_quotes.json"
    body = path.read_bytes()
    if len(body) > 262_144:
        raise ValueError("GATE2_FIXTURE_LIMIT")
    raw = json.loads(body)
    clean = discard_provider_prose(raw)
    if not isinstance(clean, dict) or contains_provider_prose(clean):
        raise ValueError("GATE2_PROSE_BOUNDARY")
    # Locally computed fixture hashes, not a claim of authenticated real response capture.
    normalized = map_equity_quotes(
        GatewayReadResult(
            capability="get_equity_quotes",
            manifest_version=PINNED_MANIFEST_VERSION,
            manifest_digest=EXPECTED_MANIFEST_DIGEST,
            schema_digest=digest_json({"fixture": "schema-shaped"}),
            result_digest=digest_json(clean),
            observed_at="2026-08-08T15:00:02Z",
            payload=clean,
            warnings=(),
        ),
        received_at="2026-08-08T15:00:03Z",
        max_quote_age_seconds=15,
    )
    if not normalized.quotes or any(quote.live_eligible for quote in normalized.quotes):
        raise ValueError("GATE2_READ_ONLY_BOUNDARY")
    return digest_bytes(body)


def _evaluations() -> ResearchEvalReport:
    completed = subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts/run_research_evals.py")],
        cwd=ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "UTC",
            "LANG": "C.UTF-8",
        },
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode or len(completed.stdout) > 1_048_576:
        raise ValueError("GATE2_EVAL_FAILED")
    return ResearchEvalReport.model_validate_json(completed.stdout)


def run_gate2() -> Gate2Report:
    """Repeatable offline checks only. Never load runtime configuration/credentials."""
    archive, portfolio, quote = build_gate2_archive()
    packet = archive.payload.packet
    if archive.payload.status != "complete" or packet is None:
        raise ValueError("GATE2_PACKET_INCOMPLETE")
    replayed = replay_research_archive(archive)
    capture = {item.decode().tool: item.decode().data for item in archive.payload.agent.captures}
    indicator, metrics = capture.get("indicators"), capture.get("concentration")
    trace = (
        packet.market.last_price == quote.last_price
        and packet.market.bid == quote.bid
        and packet.market.ask == quote.ask
        and isinstance(indicator, IndicatorRun)
        and packet.technical == indicator.technical
        and isinstance(metrics, PortfolioMetrics)
        and packet.portfolio is not None
        and packet.portfolio.quantity == metrics.quantity
        and packet.portfolio.market_value == metrics.market_value
        and packet.portfolio.portfolio_weight == metrics.portfolio_weight
        and packet.portfolio.buying_power == metrics.buying_power
    )
    scope = archive.payload.request.scope
    identity = scope.instrument
    context = StrategyContext(
        as_of=scope.as_of,
        research=packet,
        portfolio=portfolio,
        strategy_state=StrategyState(
            strategy="moving_average",
            strategy_version="1.0.0",
            updated_at=scope.as_of - timedelta(minutes=1),
            entries=(
                StrategyStateItem(
                    key="fast_above_slow", kind=StrategyStateValueKind.BOOLEAN, boolean_value=False
                ),
            ),
        ),
    )
    config = PaperFlowConfig(
        context=context,
        quote=quote,
        portfolio=portfolio,
        risk_config=make_risk_config(
            allowlist=(
                AllowlistEntry(
                    instrument_id=identity.instrument_id,
                    symbol=identity.symbol,
                    exchange=identity.exchange,
                    currency=identity.currency,
                    asset_type=identity.asset_type,
                ),
            )
        ),
        sizing_config=make_sizing_config(),
        instrument=make_instrument(
            instrument_id=identity.instrument_id,
            symbol=identity.symbol,
            exchange=identity.exchange,
            currency=identity.currency,
            asset_type=identity.asset_type,
        ),
        as_of=scope.as_of,
        opening_cash=portfolio.cash,
        exposure_inputs=make_exposure_inputs(instrument_id=identity.instrument_id),
        cost_model=PaperCostModel(
            fee_bps=Decimal(1), half_spread_bps=Decimal(1), slippage_bps=Decimal(1)
        ),
    )
    pending = run_paper_flow(config)
    filled = run_paper_flow(replace(config, inject_approval=True))
    evaluation = _evaluations()
    code_paths = (
        "src/ainvest/agents/research_agent.py",
        "src/ainvest/agents/research_builder.py",
        "src/ainvest/agents/tools/runner.py",
        "src/ainvest/data/indicators.py",
        "src/ainvest/orchestrator/paper_loop.py",
        "tests/unit/gate2_fixtures.py",
    )
    code_digest = digest_json(
        {path: digest_bytes((ROOT / path).read_bytes()) for path in code_paths}
    )
    conservation = filled.conservation_ok is True
    checks_passed = (
        trace
        and replayed == packet
        and conservation
        and pending.terminal.value == "APPROVAL_PENDING"
        and pending.filled_quantity == 0
        and filled.terminal.value == "FILLED"
        and evaluation.offline_checks_passed
        and len(evaluation.cases) == 12
    )
    return Gate2Report(
        offline_checks_passed=checks_passed,
        trace_verified=trace,
        archive_replayed=replayed == packet,
        archive_digest=archive.digest,
        packet_digest=digest_json(packet.model_dump(mode="json")),
        code_digest=code_digest,
        tool_capture_digests=archive.payload.agent.record.tool_output_digests,
        pending_terminal=pending.terminal.value,
        pending_filled_quantity=pending.filled_quantity,
        explicit_test_approval_terminal=filled.terminal.value,
        test_ledger_conservation=conservation,
        paper_semantic_digest=digest_json({"pending": pending.digests, "filled": filled.digests}),
        saved_contract_fixture_digest=_read_contract_fixture(),
        eval_suite_digest=evaluation.suite_digest,
        eval_case_count=len(evaluation.cases),
        eval_checks_passed=evaluation.offline_checks_passed,
        eval_semantic_digest=digest_json(
            [case.model_dump(mode="json", exclude={"latency_ms"}) for case in evaluation.cases]
        ),
    )
