"""Read-only discovery and immutable news captures; no article crawling."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from ainvest.data.models import (
    ExternalHttpsUrl,
    NewsEventObservation,
    NewsEventRequest,
    ObservationPage,
    TimeCertainty,
)
from ainvest.data.ports import (
    DataInvalidRequestError,
    DataOperation,
    DataRateLimitError,
    DataSchemaError,
    DataTimeoutError,
    DataUpstreamError,
)
from ainvest.data.providers.sec import SecFilingMetadata
from ainvest.schemas.common import (
    DomainModel,
    MachineCode,
    Provenance,
    QualityFlag,
    SchemaVersion,
    Symbol,
    UtcDateTime,
    ensure_utc,
)
from ainvest.schemas.market import MarketEvent
from ainvest.schemas.research import EvidenceCitation, EvidenceKind, EvidenceLocator

if TYPE_CHECKING:
    from httpx import BaseTransport

SOURCE = "news.events.v1"
Text = Annotated[str, StringConstraints(min_length=1, max_length=512)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=256)]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class DiscoveryRecord(DomainModel):
    """GDELT seen time is not verified publisher time; do not coerce to an event."""

    schema_version: SchemaVersion = "1.0"
    url: ExternalHttpsUrl
    headline: Text
    publisher: Label
    seen_at: UtcDateTime
    received_at: UtcDateTime
    published_at: None = None
    symbols: tuple[()] = ()
    license_name: Literal["Publisher rights unspecified; metadata only"] = (
        "Publisher rights unspecified; metadata only"
    )

    @model_validator(mode="after")
    def _time_order(self) -> Self:
        if self.seen_at > self.received_at:
            raise ValueError("future discovery")
        return self


class SourceRecord(DomainModel):
    """Trusted ingestion input, not a remote authorization or crawler request.

    IR host/symbol ownership and publisher timestamps must be checked by the
    caller. event_key is an explicit reviewed event association, never fuzzy text.
    """

    schema_version: SchemaVersion = "1.0"
    url: ExternalHttpsUrl
    publisher: Label
    headline: Text
    published_at: UtcDateTime
    received_at: UtcDateTime
    symbols: Annotated[tuple[Symbol, ...], Field(max_length=100)] = ()
    source_kind: Literal["DISCOVERY", "COMPANY_IR", "SEC"]
    license_name: Label
    quotation_policy: Literal["metadata_only"] = "metadata_only"
    event_type: MachineCode
    event_key: EvidenceLocator | None = None
    filing_locator: EvidenceLocator | None = None

    @model_validator(mode="after")
    def _bindings(self) -> Self:
        if self.published_at > self.received_at or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("invalid source time/symbols")
        if self.source_kind == "SEC":
            if (
                self.url.host != "www.sec.gov"
                or not self.filing_locator
                or not self.filing_locator.startswith("filing:sec.edgar/")
            ):
                raise ValueError("SEC citation required")
        elif self.filing_locator is not None:
            raise ValueError("non-SEC filing locator")
        return self


class NewsObservation(NewsEventObservation):
    """Retain all source URLs, timestamps, trust labels and restrictions."""

    sources: Annotated[tuple[SourceRecord, ...], Field(min_length=1, max_length=100)]


class NewsPage(ObservationPage[NewsEventObservation]):
    items: tuple[NewsObservation, ...]


def normalize_ir(
    rows: tuple[Mapping[str, Any], ...],
    *,
    expected_host: str,
    publisher: str,
    symbols: tuple[str, ...],
    received_at: datetime,
    license_name: str,
    event_type: str = "COMPANY_ANNOUNCEMENT",
) -> tuple[SourceRecord, ...]:
    """Normalize caller-captured IR metadata against a reviewed exact host.

    No arbitrary URL is fetched. Host ownership, symbol mapping, timestamp
    extraction and source permission belong to the trusted ingestion caller.
    """
    try:
        if len(rows) > 250 or not expected_host or not symbols:
            raise ValueError("IR bounds")
        result = tuple(
            SourceRecord(
                url=row["url"],
                headline=row["title"],
                published_at=row["published_at"],
                publisher=publisher,
                symbols=symbols,
                received_at=received_at,
                source_kind="COMPANY_IR",
                license_name=license_name,
                event_type=event_type,
            )
            for row in rows
        )
        if any(
            item.url.host != expected_host or item.url.port not in (None, 443) for item in result
        ):
            raise ValueError("IR host mismatch")
        return result
    except (ValueError, KeyError, TypeError):
        raise DataSchemaError(
            "Invalid IR metadata",
            operation=DataOperation.NEWS_EVENTS,
            reason_code="IR_CAPTURE_INVALID",
        ) from None


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def normalize_gdelt(
    payload: Mapping[str, Any], *, received_at: datetime
) -> tuple[DiscoveryRecord, ...]:
    """Parse at most 250 discovery records; unsupported HTTP URLs fail closed."""
    try:
        rows = payload["articles"]
        if not isinstance(rows, list) or len(rows) > 250:
            raise ValueError("article bound")
        unique: dict[str, DiscoveryRecord] = {}
        for row in rows:
            item = DiscoveryRecord(
                url=row["url"],
                headline=row["title"],
                publisher=row["domain"],
                seen_at=datetime.strptime(row["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC),
                received_at=received_at,
            )
            unique[item.model_dump_json()] = item
        return tuple(unique[key] for key in sorted(unique))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise DataSchemaError(
            "Invalid GDELT discovery",
            operation=DataOperation.NEWS_EVENTS,
            reason_code="GDELT_INVALID",
            source="gdelt.doc.v2",
        ) from None


def sec_events(filings: tuple[SecFilingMetadata, ...], *, symbol: str) -> tuple[SourceRecord, ...]:
    """Project verified SEC metadata; filing time does not imply earnings time."""
    if len(filings) > 10_000:
        raise DataInvalidRequestError(
            "SEC event bound", operation=DataOperation.NEWS_EVENTS, reason_code="NEWS_LIMIT"
        )
    return tuple(
        SourceRecord(
            url=filing.primary_document_url,
            publisher="SEC EDGAR",
            headline=f"SEC {filing.form_type} filing",
            published_at=filing.filed_at,
            received_at=filing.provenance.received_at,
            symbols=(symbol,),
            source_kind="SEC",
            license_name="SEC filing metadata; attachments not licensed by this adapter",
            event_type="SEC_FILING",
            filing_locator=f"filing:sec.edgar/{filing.accession_number}",
        )
        for filing in filings
        if filing.form_type in {"8-K", "8-K/A", "4", "4/A"}
    )


class NewsAdapter:
    """Bounded in-memory NewsEventPort; no network during queries."""

    source_id = SOURCE

    def __init__(self, *, records: tuple[SourceRecord, ...], captured_at: datetime) -> None:
        self._captured = ensure_utc(captured_at)
        if len(records) > 10_000 or any(item.received_at > self._captured for item in records):
            raise DataSchemaError(
                "Invalid news capture",
                operation=DataOperation.NEWS_EVENTS,
                reason_code="NEWS_CAPTURE_INVALID",
                source=SOURCE,
            )
        groups: dict[str, dict[str, SourceRecord]] = {}
        for item in records:
            key = item.event_key or str(item.url)
            groups.setdefault(key, {})[item.model_dump_json()] = item
        observations = []
        for key, unique in sorted(groups.items()):
            sources = tuple(
                sorted(
                    unique.values(),
                    key=lambda item: (item.source_kind == "DISCOVERY", item.model_dump_json()),
                )
            )
            if len(sources) > 100 or len({item.event_type for item in sources}) != 1:
                raise DataSchemaError(
                    "Ambiguous event association",
                    operation=DataOperation.NEWS_EVENTS,
                    reason_code="NEWS_ASSOCIATION_INVALID",
                    source=SOURCE,
                )
            representative = sources[0]
            flags = {QualityFlag.PARTIAL}
            if any(item.source_kind == "DISCOVERY" for item in sources):
                flags.add(QualityFlag.UNVERIFIED)
            if len({(item.headline, item.published_at) for item in sources}) > 1:
                flags.add(QualityFlag.CONFLICTING_SOURCES)
            provenance = Provenance(
                source=SOURCE,
                observed_at=self._captured,
                received_at=self._captured,
                quality_flags=tuple(sorted(flags)),
            )
            citations = tuple(
                EvidenceCitation(
                    evidence_id="news_" + _hash(item.model_dump_json()),
                    kind=EvidenceKind.FILING if item.source_kind == "SEC" else EvidenceKind.EVENT,
                    summary="Source metadata reference; article text not captured",
                    provenance=Provenance(
                        source={
                            "SEC": "sec.edgar.v1",
                            "COMPANY_IR": "company.ir",
                            "DISCOVERY": "gdelt.doc.v2",
                        }[item.source_kind],
                        observed_at=item.published_at,
                        received_at=item.received_at,
                        quality_flags=(QualityFlag.PARTIAL, QualityFlag.UNVERIFIED)
                        if item.source_kind == "DISCOVERY"
                        else (QualityFlag.PARTIAL,),
                    ),
                    locator=item.filing_locator or "event:news/" + _hash(item.model_dump_json()),
                )
                for item in sources
            )
            symbols = tuple(sorted({symbol for item in sources for symbol in item.symbols}))
            observations.append(
                NewsObservation(
                    event=MarketEvent(
                        event_id="news_" + _hash(key),
                        symbol=symbols[0] if len(symbols) == 1 else None,
                        event_type=representative.event_type,
                        headline=representative.headline,
                        occurred_at=representative.published_at,
                        provenance=provenance,
                    ),
                    symbols=symbols,
                    url=representative.url,
                    publisher=representative.publisher,
                    published_at=representative.published_at,
                    license_name="Per-source restrictions apply; metadata only",
                    event_time_certainty=TimeCertainty.UNKNOWN,
                    citations=citations,
                    sources=sources,
                )
            )
        self._items = tuple(observations)
        self._digest = _hash(
            "".join(item.model_dump_json() for item in self._items) + self._captured.isoformat()
        )

    def get_news_events(self, request: NewsEventRequest) -> NewsPage:
        """Filter publication window and knowledge cutoff at end_at; no lookahead."""
        selected = tuple(
            item
            for item in self._items
            if item.provenance.received_at <= request.end_at
            and request.start_at <= item.published_at < request.end_at
            and (not request.symbols or set(request.symbols).intersection(item.symbols))
            and (not request.event_types or item.event.event_type in request.event_types)
        )
        prefix = _hash(
            self._digest
            + request.model_dump_json(exclude={"cursor", "page_size", "timeout_seconds"})
        )
        offset = 0
        if request.cursor:
            try:
                token, raw_offset = request.cursor.split(":")
                offset = int(raw_offset)
                if token != prefix or str(offset) != raw_offset or not 0 < offset < len(selected):
                    raise ValueError
            except ValueError:
                raise DataInvalidRequestError(
                    "Invalid news cursor",
                    operation=DataOperation.NEWS_EVENTS,
                    reason_code="NEWS_CURSOR_INVALID",
                    source=SOURCE,
                ) from None
        items = selected[offset : offset + request.page_size]
        flags = {QualityFlag.PARTIAL}
        flags.update(flag for item in items for flag in item.provenance.quality_flags)
        if not items:
            flags.add(QualityFlag.MISSING_FIELDS)
        following = offset + len(items)
        return NewsPage(
            items=items,
            next_cursor=f"{prefix}:{following}" if following < len(selected) else None,
            provenance=Provenance(
                source=SOURCE,
                observed_at=self._captured,
                received_at=self._captured,
                quality_flags=tuple(sorted(flags)),
            ),
        )


def capture_gdelt(
    *, query: str, timeout_seconds: int = 30, transport: BaseTransport | None = None
) -> tuple[DiscoveryRecord, ...]:
    """One bounded official-host request; no redirects, crawling or retries."""
    import httpx

    if (
        not isinstance(query, str)
        or not 1 <= len(query.strip()) <= 512
        or type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= 120
    ):
        raise DataInvalidRequestError(
            "Invalid discovery request",
            operation=DataOperation.NEWS_EVENTS,
            reason_code="GDELT_CONFIG_INVALID",
        )
    deadline = time.monotonic() + timeout_seconds
    try:
        with httpx.Client(transport=transport, trust_env=False, follow_redirects=False) as client:  # noqa: SIM117
            with client.stream(
                "GET",
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params={
                    "query": query,
                    "mode": "ArtList",
                    "format": "json",
                    "maxrecords": "250",
                    "sort": "DateDesc",
                },
                timeout=timeout_seconds,
            ) as response:
                if response.status_code == 429:
                    raise DataRateLimitError(
                        "Discovery rate limited",
                        operation=DataOperation.NEWS_EVENTS,
                        reason_code="GDELT_RATE_LIMIT",
                    )
                if response.status_code != 200:
                    raise DataUpstreamError(
                        "Discovery rejected",
                        operation=DataOperation.NEWS_EVENTS,
                        reason_code="GDELT_HTTP_FAILED",
                    )
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise ValueError
                payload = json.loads(body, object_pairs_hook=_json_object)
                if not isinstance(payload, dict):
                    raise ValueError
        result = normalize_gdelt(payload, received_at=datetime.now(UTC))
        if time.monotonic() >= deadline:
            raise TimeoutError
        return result
    except (TimeoutError, httpx.TimeoutException):
        raise DataTimeoutError(
            "Discovery timeout", operation=DataOperation.NEWS_EVENTS, reason_code="GDELT_TIMEOUT"
        ) from None
    except httpx.HTTPError:
        raise DataUpstreamError(
            "Discovery transport failure",
            operation=DataOperation.NEWS_EVENTS,
            reason_code="GDELT_TRANSPORT_FAILED",
        ) from None
    except (ValueError, RecursionError):
        raise DataSchemaError(
            "Invalid discovery response",
            operation=DataOperation.NEWS_EVENTS,
            reason_code="GDELT_INVALID",
        ) from None
