"""Bounded SEC EDGAR capture and immutable, point-in-time research projection.

The official public JSON APIs supply primary evidence, never live quotes or
broker identities. Capture is explicit and separate from historical selection.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, NoReturn

from pydantic import AnyUrl, SecretStr

from ainvest.data.models import (
    AccessionNumber,
    ExternalHttpsUrl,
    FilingReference,
    FundamentalObservation,
    FundamentalRequest,
    ObservationPage,
    ReportingPeriod,
    SecFormType,
    SecFundamentalObservation,
    TimeCertainty,
)
from ainvest.data.ports import (
    DataAuthError,
    DataInvalidRequestError,
    DataNotFoundError,
    DataOperation,
    DataRateLimitError,
    DataSchemaError,
    DataTimeoutError,
    DataUpstreamError,
)
from ainvest.schemas.common import (
    DomainModel,
    InstrumentIdentity,
    Provenance,
    QualityFlag,
    SchemaVersion,
    UtcDateTime,
    ensure_utc,
)
from ainvest.schemas.market import FactValueKind, FundamentalFact, FundamentalSnapshot
from ainvest.schemas.research import EvidenceCitation, EvidenceKind

if TYPE_CHECKING:
    from httpx import BaseTransport

SOURCE: Final = "sec.edgar.v1"
_FORMS = frozenset({"10-K", "10-Q", "8-K", "4", "10-K/A", "10-Q/A", "8-K/A", "4/A"})
_CONCEPTS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": ("revenue", False),
    "NetIncomeLoss": ("net_income", False),
    "OperatingIncomeLoss": ("operating_income", False),
    "Assets": ("assets", True),
    "Liabilities": ("liabilities", True),
}
_RATE_LOCK = threading.Lock()
_LAST_REQUEST = 0.0
_MAX_BODY = 64 * 1024 * 1024


def _cik(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("invalid CIK")
    text = str(value)
    if not re.fullmatch(r"[0-9]{1,10}", text) or int(text) == 0:
        raise ValueError("invalid CIK")
    return text.zfill(10)


def _object(value: object) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def _array(value: object) -> list[Any]:
    if not isinstance(value, list) or len(value) > 100_000:
        raise ValueError("invalid or oversized array")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("missing text")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("non-finite JSON constant")


def _reserve_request(deadline: float) -> None:
    global _LAST_REQUEST
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not _RATE_LOCK.acquire(timeout=remaining):
        raise TimeoutError
    try:
        delay = max(0.0, 0.2 - (time.monotonic() - _LAST_REQUEST))
        if time.monotonic() + delay >= deadline:
            raise TimeoutError
        if delay:
            time.sleep(delay)
        _LAST_REQUEST = time.monotonic()
    finally:
        _RATE_LOCK.release()


class SecFilingMetadata(DomainModel):
    """Metadata only; absent report dates do not become invented reporting periods."""

    schema_version: SchemaVersion = "1.0"
    accession_number: AccessionNumber
    form_type: SecFormType
    filed_at: UtcDateTime
    report_period_end: date | None
    primary_document_url: ExternalHttpsUrl
    provenance: Provenance


class SecFundamentalPage(ObservationPage[FundamentalObservation]):
    """Keep accession evidence in JSON instead of serializing only base fields."""

    items: tuple[SecFundamentalObservation, ...]


class SecEdgarAdapter:
    """One verified company mapping and one immutable capture of SEC evidence.

    Current submissions may omit older archives, and only selected standard
    concepts are normalized: all results explicitly retain PARTIAL quality.
    """

    source_id = SOURCE

    def __init__(
        self,
        *,
        instrument: InstrumentIdentity,
        cik: str,
        submissions: Mapping[str, Any],
        companyfacts: Mapping[str, Any],
        captured_at: datetime,
    ) -> None:
        try:
            self._captured = ensure_utc(captured_at)
            self._instrument = instrument
            self._cik = _cik(cik)
            if instrument.identity_as_of > self._captured:
                raise ValueError("future identity")
            if (
                _cik(submissions.get("cik")) != self._cik
                or _cik(companyfacts.get("cik")) != self._cik
            ):
                raise ValueError("conflicting CIK")
            if instrument.symbol not in _array(submissions.get("tickers")):
                raise ValueError("symbol not confirmed by submissions")
            self._provenance = Provenance(
                source=SOURCE,
                observed_at=self._captured,
                received_at=self._captured,
                quality_flags=(QualityFlag.PARTIAL,),
            )
            self._filings = self._normalize_filings(submissions)
            self._items = self._normalize_facts(companyfacts)
            canonical = "\n".join(item.model_dump_json() for item in (*self._filings, *self._items))
            canonical += instrument.model_dump_json() + self._cik + self._captured.isoformat()
            self._digest = hashlib.sha256(canonical.encode()).hexdigest()
        except (ValueError, KeyError, TypeError, OverflowError):
            raise DataSchemaError(
                "SEC capture is incomplete, conflicting or incompatible",
                operation=DataOperation.FUNDAMENTALS,
                reason_code="SEC_CAPTURE_INVALID",
                source=SOURCE,
            ) from None

    def _normalize_filings(self, submissions: Mapping[str, Any]) -> tuple[SecFilingMetadata, ...]:
        recent = _object(_object(submissions["filings"])["recent"])
        keys = ("accessionNumber", "form", "acceptanceDateTime", "reportDate", "primaryDocument")
        columns = {key: _array(recent[key]) for key in keys}
        if len({len(value) for value in columns.values()}) != 1:
            raise ValueError("misaligned submission columns")
        result: dict[str, SecFilingMetadata] = {}
        for index, form in enumerate(columns["form"]):
            if form not in _FORMS:
                continue
            accession = _text(columns["accessionNumber"][index])
            document = _text(columns["primaryDocument"][index])
            if not re.fullmatch(
                r"[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*", document
            ):
                raise ValueError("unsafe document path")
            filed_at = ensure_utc(_text(columns["acceptanceDateTime"][index]))
            if filed_at > self._captured:
                raise ValueError("future filing")
            period_raw = columns["reportDate"][index]
            period = date.fromisoformat(period_raw) if period_raw else None
            if period is not None and period > filed_at.date():
                raise ValueError("future report period")
            archive = f"https://www.sec.gov/Archives/edgar/data/{int(self._cik)}"
            url = f"{archive}/{accession.replace('-', '')}/{document}"
            filing = SecFilingMetadata(
                accession_number=accession,
                form_type=form,
                filed_at=filed_at,
                report_period_end=period,
                primary_document_url=AnyUrl(url),
                provenance=self._provenance,
            )
            if accession in result:
                raise ValueError("duplicate accession")
            result[accession] = filing
        return tuple(
            sorted(result.values(), key=lambda item: (item.filed_at, item.accession_number))
        )

    def _normalize_facts(
        self, companyfacts: Mapping[str, Any]
    ) -> tuple[SecFundamentalObservation, ...]:
        filings = {item.accession_number: item for item in self._filings}
        groups: dict[tuple[str, date, date, int, str, str, str], dict[str, FundamentalFact]] = {}
        taxonomy = _object(_object(companyfacts["facts"]).get("us-gaap", {}))
        count = 0
        for concept, (key, instant) in _CONCEPTS.items():
            if concept not in taxonomy:
                continue
            units = _object(_object(taxonomy[concept])["units"])
            if not units:
                raise ValueError("missing units")
            for currency, values in units.items():
                if not re.fullmatch(r"[A-Z]{3}", currency):
                    raise ValueError("monetary concept requires currency")
                for raw in _array(values):
                    count += 1
                    if count > 100_000:
                        raise ValueError("too many facts")
                    row = _object(raw)
                    accession = _text(row.get("accn"))
                    filing = filings.get(accession)
                    if filing is None:
                        continue  # Cannot bind archived facts without their exact filing metadata.
                    if row.get("form") != filing.form_type:
                        raise ValueError("form conflict")
                    filed_date = date.fromisoformat(_text(row.get("filed")))
                    if (filing.filed_at.date() - filed_date).days not in (0, 1):
                        raise ValueError("fact filing date conflicts with acceptance time")
                    if filing.report_period_end is None:
                        raise ValueError("missing filing period")
                    end = date.fromisoformat(_text(row.get("end")))
                    if end < filing.report_period_end:
                        # SEC fy/fp describe the filing, not necessarily a comparative
                        # fact's fiscal year. Do not relabel prior-period comparisons.
                        continue
                    if end > filing.report_period_end:
                        raise ValueError("fact exceeds filing period")
                    start = end if instant else date.fromisoformat(_text(row.get("start")))
                    if instant and "start" in row:
                        raise ValueError("instant fact unexpectedly has duration")
                    year, period = row.get("fy"), _text(row.get("fp"))
                    if isinstance(year, bool) or not isinstance(year, int):
                        raise ValueError("invalid fiscal year")
                    amount = row.get("val")
                    if isinstance(amount, bool) or not isinstance(amount, (int, Decimal)):
                        raise ValueError("fact must retain exact decimal representation")
                    fact = FundamentalFact(
                        key=key,
                        kind=FactValueKind.DECIMAL,
                        decimal_value=Decimal(amount),
                        unit=currency,
                    )
                    context = "CONSOLIDATED_INSTANT" if instant else "CONSOLIDATED_DURATION"
                    group_key = (accession, start, end, year, period, currency, context)
                    group = groups.setdefault(group_key, {})
                    if key in group and group[key] != fact:
                        raise ValueError("conflicting fact")
                    group[key] = fact
        result = []
        for (accession, start, end, year, period, currency, context), facts in sorted(
            groups.items()
        ):
            metadata = filings[accession]
            reference = FilingReference.model_validate(
                metadata.model_dump(exclude={"schema_version"})
            )
            citation = EvidenceCitation(
                evidence_id="ev_"
                + hashlib.sha256(
                    (accession + context + str(start) + currency).encode()
                ).hexdigest()[:32],
                kind=EvidenceKind.FILING,
                summary="SEC standard taxonomy facts; exact accession and period",
                provenance=self._provenance,
                locator=f"filing:sec.edgar/{accession}",
            )
            result.append(
                SecFundamentalObservation(
                    instrument=self._instrument,
                    snapshot=FundamentalSnapshot(
                        symbol=self._instrument.symbol,
                        as_of=self._captured,
                        facts=tuple(facts[key] for key in sorted(facts)),
                        provenance=self._provenance,
                    ),
                    period=ReportingPeriod(
                        start_date=start, end_date=end, fiscal_year=year, fiscal_period=period
                    ),
                    reporting_context=context,
                    reporting_currency=currency,
                    earnings_at=None,
                    earnings_time_certainty=TimeCertainty.UNKNOWN,
                    filing=reference,
                    citations=(citation,),
                )
            )
        return tuple(result)

    def get_filings(self, *, as_of: datetime) -> tuple[SecFilingMetadata, ...]:
        """Return cached metadata only when this capture was known by the cutoff."""
        return self._filings if self._captured <= ensure_utc(as_of) else ()

    def get_fundamentals(
        self, request: FundamentalRequest
    ) -> ObservationPage[FundamentalObservation]:
        """Select from the capture without I/O, clock changes or look-ahead."""
        query = request.model_dump_json(exclude={"cursor", "page_size", "timeout_seconds"})
        prefix = self._digest + ":" + hashlib.sha256(query.encode()).hexdigest() + ":"
        offset = 0
        if request.as_of < self._captured or (
            request.cursor and not request.cursor.startswith(prefix)
        ):
            self._invalid_request()
        if request.cursor:
            suffix = request.cursor.removeprefix(prefix)
            if not re.fullmatch(r"[0-9]{1,6}", suffix):
                self._invalid_request()
            offset = int(suffix)
        items = self._items if self._instrument.symbol in request.symbols else ()
        if offset > len(items):
            self._invalid_request()
        flags = {QualityFlag.PARTIAL}
        if not items or set(request.symbols) != {self._instrument.symbol}:
            flags.add(QualityFlag.MISSING_FIELDS)
        end = offset + request.page_size
        return SecFundamentalPage(
            items=items[offset:end],
            provenance=self._provenance.model_copy(update={"quality_flags": tuple(sorted(flags))}),
            next_cursor=prefix + str(end) if end < len(items) else None,
        )

    @staticmethod
    def _invalid_request() -> None:
        raise DataInvalidRequestError(
            "SEC query cutoff or cursor invalid",
            operation=DataOperation.FUNDAMENTALS,
            reason_code="SEC_QUERY_INVALID",
            source=SOURCE,
        )


def capture_company(
    *,
    instrument: InstrumentIdentity,
    cik: str,
    identity: SecretStr,
    timeout_seconds: int = 30,
    transport: BaseTransport | None = None,
) -> SecEdgarAdapter:
    """Explicitly capture two official endpoints with no retries or redirects.

    Process-local 5 req/s ceiling; operators must coordinate the SEC-wide budget
    across processes/hosts. A contact identity is mandatory, never inferred.
    """
    import httpx

    try:
        normalized = _cik(cik)
        declared = identity.get_secret_value()
        if not re.fullmatch(r"[ -~]{3,200}", declared) or not re.search(r"\S+@\S+\.\S+", declared):
            raise ValueError("missing contact identity")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
            raise ValueError("invalid timeout")
    except ValueError:
        raise DataInvalidRequestError(
            "SEC capture configuration invalid",
            operation=DataOperation.FUNDAMENTALS,
            reason_code="SEC_CONFIG_INVALID",
            source=SOURCE,
        ) from None
    deadline = time.monotonic() + timeout_seconds
    paths = (f"submissions/CIK{normalized}.json", f"api/xbrl/companyfacts/CIK{normalized}.json")
    results = []
    try:
        with httpx.Client(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            headers={"User-Agent": declared, "Accept": "application/json"},
        ) as client:
            for path in paths:
                _reserve_request(deadline)
                with client.stream(
                    "GET",
                    "https://data.sec.gov/" + path,
                    timeout=max(0.001, deadline - time.monotonic()),
                ) as response:
                    error = {
                        403: DataAuthError,
                        404: DataNotFoundError,
                        429: DataRateLimitError,
                    }.get(response.status_code)
                    if error:
                        raise error(
                            "SEC request rejected",
                            operation=DataOperation.FUNDAMENTALS,
                            reason_code="SEC_HTTP_REJECTED",
                            source=SOURCE,
                        )
                    if response.status_code != 200:
                        raise DataUpstreamError(
                            "SEC request failed",
                            operation=DataOperation.FUNDAMENTALS,
                            reason_code="SEC_HTTP_FAILED",
                            source=SOURCE,
                        )
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        if time.monotonic() >= deadline:
                            raise TimeoutError
                        body.extend(chunk)
                        if len(body) > _MAX_BODY:
                            raise ValueError("oversized response")
                    results.append(
                        _object(
                            json.loads(
                                body,
                                parse_float=Decimal,
                                object_pairs_hook=_unique_object,
                                parse_constant=_reject_constant,
                            )
                        )
                    )
        if time.monotonic() >= deadline:
            raise TimeoutError
        adapter = SecEdgarAdapter(
            instrument=instrument,
            cik=normalized,
            submissions=results[0],
            companyfacts=results[1],
            captured_at=datetime.now(UTC),
        )
        if time.monotonic() >= deadline:
            raise TimeoutError
        return adapter
    except (TimeoutError, httpx.TimeoutException):
        raise DataTimeoutError(
            "SEC deadline exceeded",
            operation=DataOperation.FUNDAMENTALS,
            reason_code="SEC_TIMEOUT",
            source=SOURCE,
        ) from None
    except httpx.HTTPError:
        raise DataUpstreamError(
            "SEC transport failed",
            operation=DataOperation.FUNDAMENTALS,
            reason_code="SEC_TRANSPORT_FAILED",
            source=SOURCE,
        ) from None
    except (ValueError, KeyError, TypeError):
        raise DataSchemaError(
            "SEC response invalid",
            operation=DataOperation.FUNDAMENTALS,
            reason_code="SEC_RESPONSE_INVALID",
            source=SOURCE,
        ) from None
