"""Tools operate offline and publish only run-bound normalized evidence."""

import socket

import pytest
from indicator_fixtures import snapshot
from research_tool_fixtures import fixtures

from ainvest.agents.tools import HistoryInput, ResearchTools


def test_offline_read_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    scope, sources = fixtures()

    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("unexpected research network access")

    monkeypatch.setattr(socket, "socket", denied)
    with ResearchTools(scope, sources) as tools:
        results = (
            tools.quote(),
            tools.price_book(),
            tools.history(HistoryInput(expected=snapshot().inputs.expected)),
            tools.indicators(HistoryInput(expected=snapshot().inputs.expected)),
            tools.concentration(),
            tools.buying_power(),
        )
        assert all(result.status == "complete" for result in results)
        assert tools.is_complete(tuple(result.tool for result in results))
        for result in results:
            assert (
                tools.resolve_evidence(tuple(item.evidence_id for item in result.evidence))
                == result.evidence
            )
        assert not hasattr(tools, "invoke")
        assert not hasattr(tools, "submit_order")
