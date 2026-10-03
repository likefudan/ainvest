"""Synthetic SEC shapes; no public requests or real contact identity."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from ainvest.data.models import FundamentalRequest, SecFundamentalObservation, TimeCertainty
from ainvest.data.ports import (
    DataAuthError,
    DataInvalidRequestError,
    DataNotFoundError,
    DataRateLimitError,
    DataSchemaError,
    DataTimeoutError,
    DataUpstreamError,
)
from ainvest.data.providers import sec
from ainvest.data.providers.sec import SecEdgarAdapter, capture_company
from ainvest.schemas.common import AssetType, InstrumentIdentity, QualityFlag

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/sec"
CAPTURED = datetime(2026, 5, 4, tzinfo=UTC)


def documents() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        json.loads((FIXTURES / "company.json").read_text()),
        json.loads((FIXTURES / "facts.json").read_text()),
    )


def adapter(
    company: dict[str, Any] | None = None, facts: dict[str, Any] | None = None
) -> SecEdgarAdapter:
    default_company, default_facts = documents()
    return SecEdgarAdapter(
        instrument=InstrumentIdentity(
            instrument_id="synthetic-test",
            symbol="TEST",
            exchange="XNAS",
            currency="USD",
            asset_type=AssetType.EQUITY,
            identity_as_of=datetime(2025, 1, 1, tzinfo=UTC),
        ),
        cik="0000001234",
        submissions=default_company if company is None else company,
        companyfacts=default_facts if facts is None else facts,
        captured_at=CAPTURED,
    )


def test_filings_and_facts_keep_periods_units_and_accessions() -> None:
    provider = adapter()
    filings = provider.get_filings(as_of=CAPTURED)
    assert {filing.form_type for filing in filings} == {"10-K", "10-Q", "8-K", "4"}
    assert filings[-1].report_period_end is None
    page = provider.get_fundamentals(FundamentalRequest(symbols=("TEST",), as_of=CAPTURED))
    assert len(page.items) == 3
    annual = next(item for item in page.items if item.period.fiscal_period == "FY")
    assert annual.period.start_date.isoformat() == "2025-01-01"
    assert annual.snapshot.facts[0].unit == "USD"
    assert all(item.earnings_at is None for item in page.items)
    assert all(item.earnings_time_certainty is TimeCertainty.UNKNOWN for item in page.items)
    for item in page.items:
        assert isinstance(item, SecFundamentalObservation)
        assert item.filing.accession_number in item.citations[0].locator
    assert all(item.provenance.received_at == CAPTURED for item in page.items)


def test_no_lookahead_or_mutable_input_aliases() -> None:
    company, facts = documents()
    provider = adapter(company, facts)
    facts.clear()
    company.clear()
    before = datetime(2026, 5, 3, tzinfo=UTC)
    assert provider.get_filings(as_of=before) == ()
    with pytest.raises(DataInvalidRequestError):
        provider.get_fundamentals(FundamentalRequest(symbols=("TEST",), as_of=before))
    assert provider.get_filings(as_of=CAPTURED)


@pytest.mark.parametrize(
    "damage", ["unit", "start", "value", "cik", "symbol", "path", "time", "duplicate"]
)
def test_malformed_data_is_not_repaired(damage: str) -> None:
    company, facts = documents()
    units = facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]
    row = units["USD"][0]
    if damage == "unit":
        units[""] = units.pop("USD")
    elif damage == "start":
        del row["start"]
    elif damage == "value":
        row["val"] = 1.2
    elif damage == "cik":
        facts["cik"] = 9876
    elif damage == "symbol":
        company["tickers"] = ["OTHER"]
    elif damage == "path":
        company["filings"]["recent"]["primaryDocument"][0] = "../secret"
    elif damage == "time":
        company["filings"]["recent"]["acceptanceDateTime"][0] = "2026-02-01T15:00:00"
    else:
        conflict = deepcopy(row)
        conflict["val"] = 999
        units["USD"].append(conflict)
    with pytest.raises(DataSchemaError):
        adapter(company, facts)


def test_pagination_and_query_binding() -> None:
    provider = adapter()
    request = FundamentalRequest(symbols=("TEST",), as_of=CAPTURED, page_size=1)
    first = provider.get_fundamentals(request)
    second = provider.get_fundamentals(request.model_copy(update={"cursor": first.next_cursor}))
    assert first.items != second.items
    with pytest.raises(DataInvalidRequestError):
        provider.get_fundamentals(
            request.model_copy(update={"symbols": ("OTHER",), "cursor": first.next_cursor})
        )
    missing = provider.get_fundamentals(FundamentalRequest(symbols=("OTHER",), as_of=CAPTURED))
    assert missing.items == () and QualityFlag.MISSING_FIELDS in missing.provenance.quality_flags


def capture(
    transport: httpx.BaseTransport, identity: str = "Synthetic test test@example.invalid"
) -> SecEdgarAdapter:
    return capture_company(
        instrument=adapter()
        .get_fundamentals(FundamentalRequest(symbols=("TEST",), as_of=CAPTURED))
        .items[0]
        .instrument,
        cik="1234",
        identity=SecretStr(identity),
        transport=transport,
    )


def test_capture_pins_hosts_and_preserves_decimal_json() -> None:
    calls: list[httpx.Request] = []
    company, facts = documents()

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.host == "data.sec.gov"
        assert request.headers["user-agent"] == "Synthetic test test@example.invalid"
        content = json.dumps(company if "/submissions/" in request.url.path else facts)
        return httpx.Response(200, content=content.replace("-1250", "-1250.125"))

    provider = capture(httpx.MockTransport(respond))
    page = provider.get_fundamentals(FundamentalRequest(symbols=("TEST",), as_of=datetime.now(UTC)))
    net_income = next(
        fact for item in page.items for fact in item.snapshot.facts if fact.key == "net_income"
    )
    assert str(net_income.decimal_value) == "-1250.125"
    assert len(calls) == 2
    assert calls[0].url.path == "/submissions/CIK0000001234.json"
    assert calls[1].url.path == "/api/xbrl/companyfacts/CIK0000001234.json"
    assert "test@example.invalid" not in page.model_dump_json()


@pytest.mark.parametrize(
    "status,error",
    [
        (403, DataAuthError),
        (404, DataNotFoundError),
        (429, DataRateLimitError),
        (500, DataUpstreamError),
        (302, DataUpstreamError),
    ],
)
def test_capture_failures_do_not_retry_or_follow_redirect(
    status: int, error: type[Exception]
) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://example.invalid/stolen"})

    with pytest.raises(error):
        capture(httpx.MockTransport(respond))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "identity", ["", "no-contact", "Name bad@example.invalid\r\nInjected: bad"]
)
def test_bad_identity_never_calls_transport(identity: str) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must reject before I/O")

    with pytest.raises(DataInvalidRequestError):
        capture(httpx.MockTransport(forbidden), identity)


def test_transport_timeout_hides_exception_details() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private-contact-test@example.invalid", request=request)

    with pytest.raises(DataTimeoutError) as caught:
        capture(httpx.MockTransport(timeout))
    assert "private-contact" not in str(caught.value)


@pytest.mark.parametrize("body", [b'{"cik":1234,"cik":5678}', b"not-json", b"[]"])
def test_invalid_or_ambiguous_json_fails_closed(body: bytes) -> None:
    with pytest.raises(DataSchemaError):
        capture(httpx.MockTransport(lambda request: httpx.Response(200, content=body)))


def test_response_body_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sec, "_MAX_BODY", 8)
    with pytest.raises(DataSchemaError):
        capture(httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 20)))


def test_comparatives_are_not_relabelled_as_current_fiscal_period() -> None:
    company, facts = documents()
    rows = facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]
    older = deepcopy(rows[0])
    older.update(start="2025-01-01", end="2025-03-31", val=999)
    rows.append(older)
    page = adapter(company, facts).get_fundamentals(
        FundamentalRequest(symbols=("TEST",), as_of=CAPTURED)
    )
    assert len(page.items) == 3
    assert all(item.period.end_date.isoformat() != "2025-03-31" for item in page.items)


def test_instant_and_duration_and_currencies_stay_separate() -> None:
    company, facts = documents()
    units = facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]
    units["CAD"] = deepcopy(units["USD"])
    page = adapter(company, facts).get_fundamentals(
        FundamentalRequest(symbols=("TEST",), as_of=CAPTURED)
    )
    assert len(page.items) == 4
    assert {item.reporting_currency for item in page.items} == {"USD", "CAD"}
    instant = next(item for item in page.items if item.reporting_context == "CONSOLIDATED_INSTANT")
    assert instant.period.start_date == instant.period.end_date


def test_rate_wait_honors_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    sleeps: list[float] = []
    monkeypatch.setattr(sec, "time", SimpleNamespace(monotonic=lambda: 10.0, sleep=sleeps.append))
    monkeypatch.setattr(sec, "_LAST_REQUEST", 10.0)
    with pytest.raises(TimeoutError):
        sec._reserve_request(10.1)
    assert sleeps == []
    sec._reserve_request(11.0)
    assert sleeps == [0.2]


def test_nonfinite_json_rejected() -> None:
    with pytest.raises(DataSchemaError):
        capture(httpx.MockTransport(lambda request: httpx.Response(200, content=b'{"unused":NaN}')))


@pytest.mark.parametrize("damage", ["form", "filed", "period", "future", "columns", "instant"])
def test_evidence_context_cannot_be_silently_repaired(damage: str) -> None:
    company, facts = documents()
    row = facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"][0]
    if damage == "form":
        row["form"] = "10-K"
    elif damage == "filed":
        row["filed"] = "2026-10-01"
    elif damage == "period":
        row["end"] = "2027-01-01"
    elif damage == "future":
        company["filings"]["recent"]["acceptanceDateTime"][0] = "2027-01-01T00:00:00Z"
    elif damage == "columns":
        company["filings"]["recent"]["form"].pop()
    else:
        facts["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["start"] = "2026-01-01"
    with pytest.raises(DataSchemaError):
        adapter(company, facts)
