"""Packet numbers are deterministic tool data, not model text."""

import json
from decimal import Decimal

import pytest
from research_builder_fixtures import assembly_inputs

from ainvest.agents.research_archive import ResearchArchive
from ainvest.agents.research_builder import build_research_archive, replay_research_archive
from ainvest.agents.research_capture import CapturedToolResult
from ainvest.data.indicators import digest_bytes


def test_fixed_tools_yield_stable_packet_and_replay() -> None:
    with assembly_inputs() as (request, result, tools):
        archive = build_research_archive(request, result, tools=tools)
        assert archive.payload.status == "complete", archive.payload.error_code
        packet = archive.payload.packet
        assert packet is not None
        assert packet.market.last_price == Decimal("160")
        assert packet.technical is not None and packet.technical.sma_20 == Decimal("150.5")
        assert packet.portfolio is not None
        assert packet.portfolio.market_value == Decimal(320)
        assert packet.portfolio.portfolio_weight == Decimal("0.32")
        assert packet.portfolio.buying_power == Decimal(680)
        assert packet.thesis.bull_case == ("Deterministic research tool: quote",)
        assert archive == ResearchArchive.model_validate_json(archive.model_dump_json())
        assert replay_research_archive(archive) == packet
        assert build_research_archive(request, result, tools=tools) == archive


def test_cross_run_scope_cannot_be_promoted() -> None:
    with assembly_inputs() as (request, result, tools):
        changed = request.model_copy(
            update={"scope": request.scope.model_copy(update={"run_id": "research_other_001"})}
        )
        archive = build_research_archive(changed, result, tools=tools)
        assert archive.payload.status == "error" and archive.payload.packet is None


def test_modified_number_even_with_new_capture_digest_rejected() -> None:
    with assembly_inputs() as (request, result, tools):
        captures = list(result.captures)
        quote = json.loads(captures[0].json_result)
        quote["data"]["last_price"] = "999"
        captures[0] = CapturedToolResult.capture_json("quote", json.dumps(quote))
        forged = result.model_copy(
            update={
                "captures": tuple(captures),
                "record": result.record.model_copy(
                    update={"tool_output_digests": tuple(item.digest for item in captures)}
                ),
            }
        )
        archive = build_research_archive(request, forged, tools=tools)
        assert archive.payload.status == "error" and archive.payload.packet is None


def test_digest_tamper_cannot_load_or_replay() -> None:
    with assembly_inputs() as (request, result, tools):
        archive = build_research_archive(request, result, tools=tools)
        body = json.loads(archive.model_dump_json())
        body["payload"]["packet"]["market"]["last_price"] = "999"
        with pytest.raises(ValueError):
            ResearchArchive.model_validate(body)


def test_rehashed_packet_tamper_still_fails_deterministic_replay() -> None:
    with assembly_inputs() as (request, result, tools):
        archive = build_research_archive(request, result, tools=tools)
        packet = archive.payload.packet
        assert packet is not None and packet.technical is not None
        forged_packet = packet.model_copy(
            update={"technical": packet.technical.model_copy(update={"sma_20": Decimal("999")})}
        )
        forged = ResearchArchive.create(
            archive.payload.model_copy(update={"packet": forged_packet})
        )
        with pytest.raises(ValueError, match="RESEARCH_REPLAY_INVALID"):
            replay_research_archive(forged)


def test_forged_claim_reference_rejected_even_with_new_narrative_digest() -> None:
    with assembly_inputs() as (request, result, tools):
        narrative = result.narrative
        assert narrative is not None
        claim = narrative.bull_case[0].model_copy(update={"evidence_ids": ("not_returned_001",)})
        narrative = narrative.model_copy(update={"bull_case": (claim,)})
        forged = result.model_copy(
            update={
                "narrative": narrative,
                "record": result.record.model_copy(
                    update={"output_digest": digest_bytes(narrative.model_dump_json().encode())}
                ),
            }
        )
        with pytest.raises(ValueError, match="BUILD_INPUT_INVALID"):
            build_research_archive(request, forged, tools=tools)


def test_missing_tool_capture_or_input_digest_rejected() -> None:
    with assembly_inputs() as (request, result, tools):
        forged = result.model_copy(update={"captures": result.captures[1:]})
        assert build_research_archive(request, forged, tools=tools).payload.packet is None
        changed = request.model_copy(update={"config_version": "different-config"})
        # Code/config provenance is caller metadata; changing scope/limits is not.
        assert build_research_archive(changed, result, tools=tools).payload.packet is not None
        bad = request.model_copy(
            update={"limits": request.limits.model_copy(update={"duration_seconds": 30})}
        )
        assert build_research_archive(bad, result, tools=tools).payload.packet is None


def test_missing_optional_numbers_remain_none_and_partial() -> None:
    with assembly_inputs(include_context=False) as (request, result, tools):
        archive = build_research_archive(request, result, tools=tools)
        assert archive.payload.status == "partial"
        packet = archive.payload.packet
        assert packet is not None and packet.technical is None and packet.portfolio is None
        assert packet.quality_flags
        assert replay_research_archive(archive) == packet


def test_stale_required_quote_is_withheld_not_flagged_into_packet() -> None:
    with assembly_inputs(include_context=False, age_seconds=121) as (request, result, tools):
        archive = build_research_archive(request, result, tools=tools)
        assert archive.payload.error_code == "REQUIRED_QUOTE_INVALID"
        assert archive.payload.packet is None
        assert replay_research_archive(archive) is None


def test_hypotheses_keep_packet_partial_and_claim_mapping_is_retained() -> None:
    with assembly_inputs(hypothesis=True) as (request, result, tools):
        archive = build_research_archive(request, result, tools=tools)
        assert archive.payload.status == "partial"
        assert archive.payload.packet is not None and archive.payload.packet.quality_flags
        assert archive.payload.agent.narrative is not None
        claim = archive.payload.agent.narrative.bull_case[0]
        assert claim.evidence_ids
        assert claim.kind == "hypothesis"
        assert replay_research_archive(archive) == archive.payload.packet
