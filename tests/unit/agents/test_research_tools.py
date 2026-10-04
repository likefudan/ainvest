"""Bounded research reads and evidence belong to one run."""

from datetime import timedelta
from decimal import ROUND_UP, Decimal, getcontext
from threading import Event

import pytest
from indicator_fixtures import snapshot
from research_tool_fixtures import fixtures

from ainvest.agents.tools import HistoryInput, ReadSources, ResearchTools, ToolScope
from ainvest.agents.tools.models import ToolResult
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.research import EvidenceKind


def test_quote_evidence_and_run_isolation() -> None:
    saved = snapshot()
    scope = ToolScope(
        run_id="research_tools_001",
        instrument=saved.inputs.expected.instrument,
        as_of=saved.inputs.expected.as_of,
        max_age_seconds=120,
    )
    sources = ReadSources(quote=lambda request: saved.inputs.quote)
    with ResearchTools(scope, sources) as tools:
        result = tools.quote()
        assert result.status == "complete"
        assert result.data == saved.inputs.quote
        assert tools.resolve_evidence((result.evidence[0].evidence_id,)) == result.evidence
        assert tools.is_complete(("quote",))
        assert not tools.is_complete(("quote", "news"))
    with (
        ResearchTools(scope.model_copy(update={"run_id": "research_other_001"}), sources) as other,
        pytest.raises(ValueError),
    ):
        other.resolve_evidence((result.evidence[0].evidence_id,))


def test_stale_and_provider_error_prevent_complete() -> None:
    saved = snapshot()
    scope = ToolScope(
        run_id="research_tools_001",
        instrument=saved.inputs.expected.instrument,
        as_of=saved.inputs.expected.as_of + timedelta(days=1),
        max_age_seconds=120,
    )
    with ResearchTools(scope, ReadSources(quote=lambda request: saved.inputs.quote)) as tools:
        result = tools.quote()
        assert result.status == "partial"
        assert "STALE" in result.quality_flags
        assert not tools.is_complete(("quote",))
        failure = tools.news()
        assert failure.status == "error"
        assert failure.data is None and failure.evidence == ()


def test_timeout_ignores_late_result_and_stops_further_reads() -> None:
    saved = snapshot()
    scope = ToolScope(
        run_id="research_tools_001",
        instrument=saved.inputs.expected.instrument,
        as_of=saved.inputs.expected.as_of,
        max_age_seconds=120,
        timeout_seconds=1,
    )
    release = Event()

    def slow(request: ToolScope) -> MarketQuote:
        release.wait(5)
        return saved.inputs.quote

    try:
        with ResearchTools(scope, ReadSources(quote=slow)) as tools:
            result = tools.quote()
            assert result.error_code == "TIMEOUT"
            assert tools.quote().error_code == "RUN_CLOSED"
            assert not tools.is_complete(("quote",))
    finally:
        release.set()


def test_all_named_tools_and_decimal_calculations() -> None:
    scope, sources = fixtures()
    expected = snapshot().inputs.expected
    with ResearchTools(scope, sources) as tools:
        assert tools.price_book().status == "complete"
        assert tools.history(HistoryInput(expected=expected)).status == "complete"
        calculated = tools.indicators(HistoryInput(expected=expected))
        assert calculated.data is not None
        assert calculated.data.technical.sma_20 == Decimal("150.5")
        concentration = tools.concentration()
        assert concentration.data is not None
        assert concentration.data.portfolio_weight == Decimal("0.32")
        assert concentration.data.largest_position_weight == Decimal("0.32")
        assert concentration.data.quantity == Decimal(2)
        assert any(
            item.kind == EvidenceKind.CALCULATED and item.numeric_value == Decimal("0.32")
            for item in concentration.evidence
        )
        assert "snapshot_id" not in concentration.model_dump_json()
        buying_power = tools.buying_power()
        assert buying_power.data is not None and buying_power.data.buying_power == Decimal(680)
        assert tools.is_complete(
            ("price_book", "history", "indicators", "concentration", "buying_power")
        )
        filing = tools.filings()
        assert filing.status == "partial" and filing.data is not None
        assert filing.data.items[0].accession_number == "0000000001-26-000001"
        assert any(
            item.locator == "filing:sec.edgar/0000000001-26-000001" for item in filing.evidence
        )
        news = tools.news()
        assert news.status == "partial" and news.data is not None
        assert set(news.data.items[0].citations).issubset(set(news.evidence))
        assert not tools.is_complete(("price_book",))


@pytest.mark.parametrize(
    "damage,code",
    [
        ("future", "FUTURE_DATA"),
        ("identity", "IDENTITY_MISMATCH"),
        ("invalid", "READ_FAILED"),
        ("exception", "READ_FAILED"),
    ],
)
def test_invalid_provider_results_sanitized(damage: str, code: str) -> None:
    scope, _ = fixtures()
    quote = snapshot().inputs.quote

    def read(request: ToolScope) -> MarketQuote:
        if damage == "exception":
            raise RuntimeError("SECRET-like-fixture-MUST-NOT-LEAK")
        if damage == "future":
            return quote.model_copy(
                update={
                    "provenance": quote.provenance.model_copy(
                        update={"received_at": request.as_of + timedelta(seconds=1)}
                    )
                }
            )
        if damage == "identity":
            return quote.model_copy(
                update={"instrument": quote.instrument.model_copy(update={"exchange": "XNYS"})}
            )
        return quote.model_copy(update={"last_price": Decimal(-1)})

    with ResearchTools(scope, ReadSources(quote=read)) as tools:
        result = tools.quote()
        assert result.status == "error" and result.error_code == code
        assert "SECRET" not in result.model_dump_json()
        assert not tools.is_complete(("quote",))


def test_scope_grid_mismatch_and_gap_rejected() -> None:
    scope, sources = fixtures()
    expected = snapshot().inputs.expected
    with ResearchTools(scope, sources) as tools:
        bad = HistoryInput(
            expected=expected.model_copy(update={"as_of": scope.as_of + timedelta(seconds=1)})
        )
        assert tools.history(bad).error_code == "SCOPE_MISMATCH"
    gapped = ReadSources(
        history=lambda request, history: snapshot().inputs.bars.model_copy(
            update={"items": snapshot().inputs.bars.items[:-1]}
        )
    )
    with ResearchTools(scope, gapped) as tools:
        history = tools.history(HistoryInput(expected=expected))
        assert history.status == "partial"
        assert tools.indicators(HistoryInput(expected=expected)).status == "error"
        assert not tools.is_complete(("history", "indicators"))


def test_result_roundtrip_and_false_complete_rejected() -> None:
    scope, sources = fixtures()
    with ResearchTools(scope, sources) as tools:
        result = tools.quote()
        assert ToolResult[MarketQuote].model_validate_json(result.model_dump_json()) == result
        payload = result.model_dump()
        payload["quality_flags"] = ["STALE"]
        with pytest.raises(ValueError):
            ToolResult[MarketQuote].model_validate(payload)


def test_result_size_limit_withholds_data_and_evidence() -> None:
    scope, sources = fixtures()
    assert sources.price_book is not None
    book = sources.price_book(scope)
    oversized = book.model_copy(update={"bids": book.bids * 501})
    with ResearchTools(scope, ReadSources(price_book=lambda request: oversized)) as tools:
        result = tools.price_book()
        assert result.error_code == "RESULT_TOO_LARGE"
        assert result.data is None and not result.evidence


def test_call_limit_and_sticky_failure() -> None:
    scope, sources = fixtures()
    with ResearchTools(scope, sources) as tools:
        for _ in range(32):
            assert tools.quote().status == "complete"
        assert tools.quote().error_code == "CALL_LIMIT"
        assert tools.quote().error_code == "RUN_CLOSED"
        assert not tools.is_complete(("quote",))
    with ResearchTools(scope, sources) as tools:
        assert tools.quote().status == "complete"
        with pytest.raises(ValueError):
            tools.resolve_evidence(("invented_citation",))


def test_extra_fields_cannot_cross_tool_boundary() -> None:
    class ExtendedQuote(MarketQuote):
        private_payload: str

    scope, _ = fixtures()
    quote = ExtendedQuote.model_validate(
        dict(snapshot().inputs.quote.model_dump(), private_payload="SECRET-like-fixture")
    )
    with ResearchTools(scope, ReadSources(quote=lambda request: quote)) as tools:
        result = tools.quote()
        assert result.status == "error"
        assert "SECRET" not in result.model_dump_json()


def test_portfolio_rounding_is_independent_of_reader_context() -> None:
    scope, sources = fixtures()
    assert sources.portfolio is not None
    original = sources.portfolio(scope)
    position = original.positions[0].model_copy(
        update={
            "market_value": Decimal(1),
            "portfolio_weight": Decimal("0.3333333333333333333333333333"),
        }
    )
    portfolio = original.model_copy(
        update={
            "cash": Decimal(2),
            "buying_power": Decimal(2),
            "equity": Decimal(3),
            "positions": (position,),
            "exposure": original.exposure.model_copy(
                update={
                    "cash": Decimal(2),
                    "equity": Decimal(3),
                    "gross_market_value": Decimal(1),
                    "net_market_value": Decimal(1),
                    "largest_position_weight": position.portfolio_weight,
                }
            ),
        }
    )

    def read(request: ToolScope) -> PortfolioSnapshot:
        getcontext().rounding = ROUND_UP
        return portfolio

    with ResearchTools(scope, ReadSources(portfolio=read)) as tools:
        result = tools.concentration()
        assert result.data is not None
        assert result.data.portfolio_weight == Decimal("0.33333333")
        assert result.data.largest_position_weight == Decimal("0.33333333")
