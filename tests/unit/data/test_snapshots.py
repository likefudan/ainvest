"""Deterministic captured-input replay."""

from datetime import timedelta

import pytest
from indicator_fixtures import snapshot
from pydantic import AnyUrl

from ainvest.data.indicators import digest_bytes
from ainvest.data.models import NewsEventRequest
from ainvest.data.ports import DataConflictError, DataIncompleteError
from ainvest.data.providers.news import NewsAdapter, SourceRecord
from ainvest.data.snapshots import (
    ResearchSnapshot,
    ResponseDigest,
    build_snapshot,
    replay_snapshot,
)


def test_roundtrip_and_replay() -> None:
    original = snapshot()
    loaded = ResearchSnapshot.model_validate_json(original.model_dump_json())
    assert loaded == original
    assert replay_snapshot(loaded) == original.packet
    assert loaded.inputs.cache_key == original.inputs.cache_key


def test_changed_runtime_rejected() -> None:
    original = snapshot()
    changed = original.model_copy(
        update={"calculation": original.calculation.model_copy(update={"library_version": "wrong"})}
    )
    with pytest.raises(DataConflictError, match="replay mismatch"):
        replay_snapshot(changed)


def test_missing_source_digest_rejected() -> None:
    original = snapshot()
    inputs = original.inputs.model_copy(update={"raw_digests": ()})
    with pytest.raises(DataIncompleteError, match="binding invalid"):
        build_snapshot(inputs, research_id=original.packet.research_id)


def test_news_sources_and_partial_flags_survive_replay() -> None:
    original = snapshot()
    now = original.inputs.expected.as_of
    record = SourceRecord(
        url=AnyUrl("https://issuer.example/news"),
        publisher="Issuer",
        headline="Synthetic announcement",
        published_at=now - timedelta(minutes=1),
        received_at=now,
        symbols=("TEST",),
        source_kind="COMPANY_IR",
        license_name="Metadata only",
        event_type="COMPANY_ANNOUNCEMENT",
    )
    page = NewsAdapter(records=(record,), captured_at=now).get_news_events(
        NewsEventRequest(start_at=now - timedelta(days=1), end_at=now)
    )
    inputs = original.inputs.model_copy(
        update={
            "news": (page,),
            "raw_digests": (
                *original.inputs.raw_digests,
                ResponseDigest(
                    source=page.provenance.source,
                    role="news",
                    raw_response_digest=digest_bytes(b"synthetic news"),
                ),
            ),
        }
    )
    result = build_snapshot(inputs, research_id=original.packet.research_id)
    assert set(page.items[0].citations).issubset(set(result.packet.evidence))
    assert result.inputs.news[0].items[0].sources == page.items[0].sources
    assert "PARTIAL" in result.packet.quality_flags
    assert (
        replay_snapshot(ResearchSnapshot.model_validate_json(result.model_dump_json()))
        == result.packet
    )
