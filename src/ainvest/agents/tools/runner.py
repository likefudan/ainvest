"""Run-scoped bounded reads with deterministic quality and evidence handling."""

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from threading import Lock
from types import TracebackType
from typing import Self

from ainvest.agents.tools.models import (
    TOOL_NAMES,
    BuyingPowerData,
    FilingPage,
    HistoryData,
    HistoryInput,
    PortfolioMetrics,
    ToolName,
    ToolResult,
    ToolScope,
)
from ainvest.data.indicators import IndicatorRun, compute_indicators, digest_bytes
from ainvest.data.models import OhlcvPage, PriceBook
from ainvest.data.ports import DataProviderError
from ainvest.data.providers.news import NewsPage, SourceRecord
from ainvest.data.quality import assess_bars
from ainvest.schemas.common import DomainModel, InstrumentIdentity, Provenance, QualityFlag
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.research import EvidenceCitation, EvidenceKind


@dataclass(frozen=True, slots=True)
class ReadSources:
    """Trusted composition injects only these named, normalized read functions.

    Readers must honor the supplied timeout and cutoff. No token, session,
    capability name or mutation method is accepted by this interface.
    """

    quote: Callable[[ToolScope], MarketQuote] | None = None
    price_book: Callable[[ToolScope], PriceBook] | None = None
    history: Callable[[ToolScope, HistoryInput], OhlcvPage] | None = None
    filings: Callable[[ToolScope], FilingPage] | None = None
    news: Callable[[ToolScope], NewsPage] | None = None
    portfolio: Callable[[ToolScope], PortfolioSnapshot] | None = None


class _Rejected(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _scan(value: DomainModel, scope: ToolScope) -> tuple[set[QualityFlag], list[EvidenceCitation]]:
    """Bound recursion, propagate source flags and reject all future metadata."""
    flags: set[QualityFlag] = set()
    citations: list[EvidenceCitation] = []
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 20_000 or depth > 20:
            raise _Rejected("RESULT_TOO_LARGE")
        if isinstance(item, Provenance):
            if item.received_at > scope.as_of or item.observed_at > scope.as_of:
                raise _Rejected("FUTURE_DATA")
            flags.update(item.quality_flags)
            if item.is_delayed:
                flags.add(QualityFlag.DELAYED)
        if isinstance(item, InstrumentIdentity) and item.identity_as_of > scope.as_of:
            raise _Rejected("FUTURE_DATA")
        if isinstance(item, SourceRecord) and (
            item.published_at > scope.as_of or item.received_at > scope.as_of
        ):
            raise _Rejected("FUTURE_DATA")
        if isinstance(item, EvidenceCitation):
            citations.append(item)
            if len(citations) > 127:
                raise _Rejected("RESULT_TOO_LARGE")
        if isinstance(item, DomainModel):
            for name in type(item).model_fields:
                visit(getattr(item, name), depth + 1)
        elif isinstance(item, tuple):
            if len(item) > 500:
                raise _Rejected("RESULT_TOO_LARGE")
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)
    return flags, citations


class ResearchTools:
    """One bounded worker and evidence registry per research run.

    A timeout closes the run to additional reads. The trusted reader may finish
    in the background; late results never enter the evidence registry.
    """

    def __init__(self, scope: ToolScope, sources: ReadSources) -> None:
        self._scope = ToolScope.model_validate_json(scope.model_dump_json())
        self._sources = sources
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-read")
        self._lock = Lock()
        self._closed = False
        self._failed = False
        self._calls = 0
        self._evidence: dict[str, EvidenceCitation] = {}
        self._completed: set[ToolName] = set()

    @property
    def scope(self) -> ToolScope:
        return self._scope

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _error[T: DomainModel](self, tool: ToolName, code: str) -> ToolResult[T]:
        self._failed = True
        return ToolResult[T](
            tool=tool,
            run_id=self.scope.run_id,
            as_of=self.scope.as_of,
            status="error",
            error_code=code,
            quality_flags=(QualityFlag.PARTIAL, QualityFlag.MISSING_FIELDS),
        )

    def _execute[T: DomainModel](
        self,
        tool: ToolName,
        reader: Callable[[], T] | None,
        model: type[T],
        provenance: Callable[[T], Provenance],
        validate: Callable[[T], set[QualityFlag]],
        kind: EvidenceKind,
    ) -> ToolResult[T]:
        if not self._lock.acquire(blocking=False):
            return self._error(tool, "RUN_BUSY")
        try:
            if self._closed:
                return self._error(tool, "RUN_CLOSED")
            self._calls += 1
            if self._calls > 32:
                self.close()
                return self._error(tool, "CALL_LIMIT")
            if reader is None:
                return self._error(tool, "UNAVAILABLE")
            deadline = time.monotonic() + self.scope.timeout_seconds
            future = self._executor.submit(reader)
            try:
                raw = future.result(timeout=self.scope.timeout_seconds)
                if not isinstance(raw, model):
                    raise _Rejected("INVALID_RESULT")
                # Revalidate even when a caller supplied an unchecked model_copy.
                flags, citations = _scan(raw, self.scope)
                body = raw.model_dump_json().encode()
                if len(body) > 262_144:
                    raise _Rejected("RESULT_TOO_LARGE")
                data = model.model_validate_json(body)
                flags.update(validate(data))
                observed = provenance(data)
                if (
                    self.scope.as_of - observed.observed_at
                ).total_seconds() > self.scope.max_age_seconds:
                    flags.add(QualityFlag.STALE)
                if time.monotonic() > deadline:
                    raise TimeoutError
                digest = digest_bytes((self.scope.run_id + tool).encode() + body).split(":")[1]
                citation = EvidenceCitation(
                    evidence_id="tool_" + digest,
                    kind=kind,
                    summary="Deterministic research tool: " + tool,
                    provenance=observed.model_copy(update={"quality_flags": tuple(sorted(flags))}),
                    locator="tool:ainvest." + tool + "/" + digest,
                )
                unique = {citation.evidence_id: citation}
                if isinstance(data, FilingPage):
                    for filing in data.items:
                        item = EvidenceCitation(
                            evidence_id="filing_"
                            + digest_bytes(
                                (self.scope.run_id + filing.accession_number).encode()
                            ).split(":")[1],
                            kind=EvidenceKind.FILING,
                            summary="Captured SEC filing metadata: " + filing.form_type,
                            provenance=filing.provenance,
                            locator="filing:sec.edgar/" + filing.accession_number,
                        )
                        unique[item.evidence_id] = item
                if isinstance(data, PortfolioMetrics):
                    for name in ("portfolio_weight", "largest_position_weight"):
                        metric = EvidenceCitation(
                            evidence_id="calc_"
                            + digest_bytes((digest + name).encode()).split(":")[1],
                            kind=EvidenceKind.CALCULATED,
                            summary="Decimal portfolio calculation: " + name,
                            provenance=citation.provenance,
                            locator="calc:ainvest.concentration/" + digest + "/" + name,
                            numeric_value=getattr(data, name),
                            calculation_source="ainvest.tools.concentration.v1",
                        )
                        unique[metric.evidence_id] = metric
                for item in citations:
                    if item.evidence_id in unique and unique[item.evidence_id] != item:
                        raise _Rejected("EVIDENCE_CONFLICT")
                    unique[item.evidence_id] = item
                for key, item in unique.items():
                    if key in self._evidence and self._evidence[key] != item:
                        raise _Rejected("EVIDENCE_CONFLICT")
                if len(self._evidence.keys() | unique.keys()) > 512:
                    raise _Rejected("EVIDENCE_LIMIT")
                evidence = tuple(unique[key] for key in sorted(unique))
                result = ToolResult[T](
                    tool=tool,
                    run_id=self.scope.run_id,
                    as_of=self.scope.as_of,
                    status="partial" if flags else "complete",
                    data=data,
                    evidence=evidence,
                    quality_flags=tuple(sorted(flags)),
                )
                if len(result.model_dump_json().encode()) > 262_144:
                    raise _Rejected("RESULT_TOO_LARGE")
                if self._closed:
                    raise _Rejected("RUN_CLOSED")
                self._evidence.update(unique)
                if flags:
                    self._failed = True
                else:
                    self._completed.add(tool)
                return result
            except TimeoutError:
                future.cancel()
                self.close()
                return self._error(tool, "TIMEOUT")
            except _Rejected as exc:
                return self._error(tool, exc.code)
            except DataProviderError as exc:
                return self._error(tool, exc.code.value)
            except Exception:
                # Never expose provider exception messages, credentials or payloads.
                return self._error(tool, "READ_FAILED")
        finally:
            self._lock.release()

    def _identity(self, instrument: InstrumentIdentity) -> None:
        if instrument != self.scope.instrument:
            raise _Rejected("IDENTITY_MISMATCH")

    def quote(self) -> ToolResult[MarketQuote]:
        """One normalized quote for this run's fixed instrument."""

        def check(data: MarketQuote) -> set[QualityFlag]:
            self._identity(data.instrument)
            return (
                set()
                if data.bid is not None and data.ask is not None
                else {QualityFlag.MISSING_FIELDS}
            )

        reader = self._sources.quote
        return self._execute(
            "quote",
            (lambda: reader(self.scope)) if reader else None,
            MarketQuote,
            lambda data: data.provenance,
            check,
            EvidenceKind.QUOTE,
        )

    def price_book(self) -> ToolResult[PriceBook]:
        """Bounded normalized bid/ask depth."""

        def check(data: PriceBook) -> set[QualityFlag]:
            self._identity(data.instrument)
            if len(data.bids) > 50 or len(data.asks) > 50:
                raise _Rejected("RESULT_TOO_LARGE")
            return set() if data.bids and data.asks else {QualityFlag.MISSING_FIELDS}

        reader = self._sources.price_book
        return self._execute(
            "price_book",
            (lambda: reader(self.scope)) if reader else None,
            PriceBook,
            lambda data: data.provenance,
            check,
            EvidenceKind.QUOTE,
        )

    def _history(self, request: HistoryInput) -> HistoryData:
        request = HistoryInput.model_validate_json(request.model_dump_json())
        self._identity(request.expected.instrument)
        if (
            request.expected.as_of != self.scope.as_of
            or request.expected.max_age_seconds != self.scope.max_age_seconds
        ):
            raise _Rejected("SCOPE_MISMATCH")
        reader = self._sources.history
        if reader is None:
            raise _Rejected("UNAVAILABLE")
        return HistoryData(expected=request.expected, bars=reader(self.scope, request))

    def history(self, request: HistoryInput) -> ToolResult[HistoryData]:
        """Capture at most 500 explicitly expected bars; retain quality failures."""

        def check(data: HistoryData) -> set[QualityFlag]:
            report = assess_bars(data.bars, data.expected)
            return set(report.flags)

        return self._execute(
            "history",
            lambda: self._history(request),
            HistoryData,
            lambda data: data.bars.provenance,
            check,
            EvidenceKind.OHLCV,
        )

    def indicators(self, request: HistoryInput) -> ToolResult[IndicatorRun]:
        """Compute the versioned TA-Lib result from the exact expected grid."""

        def read() -> IndicatorRun:
            data = self._history(request)
            with localcontext() as ctx:
                ctx.rounding = ROUND_HALF_EVEN
                return compute_indicators(data.bars, data.expected)

        return self._execute(
            "indicators",
            read,
            IndicatorRun,
            lambda data: data.technical.provenance,
            lambda data: set(data.technical.provenance.quality_flags),
            EvidenceKind.TECHNICAL,
        )

    def filings(self) -> ToolResult[FilingPage]:
        """Captured SEC metadata; no document crawling or financial parsing."""

        def check(data: FilingPage) -> set[QualityFlag]:
            self._identity(data.instrument)
            if any(item.primary_document_url.host != "www.sec.gov" for item in data.items):
                raise _Rejected("INVALID_FILING_SOURCE")
            if any(item.filed_at > self.scope.as_of for item in data.items):
                raise _Rejected("FUTURE_DATA")
            # Metadata is a partial view of the company's filing record.
            return {QualityFlag.PARTIAL}

        reader = self._sources.filings
        return self._execute(
            "filings",
            (lambda: reader(self.scope)) if reader else None,
            FilingPage,
            lambda data: data.provenance,
            check,
            EvidenceKind.FILING,
        )

    def news(self) -> ToolResult[NewsPage]:
        """Preserve returned source citations and knowledge-cutoff metadata."""

        def check(data: NewsPage) -> set[QualityFlag]:
            if any(
                item.symbols and self.scope.instrument.symbol not in item.symbols
                for item in data.items
            ):
                raise _Rejected("IDENTITY_MISMATCH")
            return (
                {QualityFlag.PARTIAL} if data.next_cursor is not None or not data.items else set()
            )

        reader = self._sources.news
        return self._execute(
            "news",
            (lambda: reader(self.scope)) if reader else None,
            NewsPage,
            lambda data: data.provenance,
            check,
            EvidenceKind.EVENT,
        )

    def _portfolio(self) -> PortfolioMetrics:
        reader = self._sources.portfolio
        if reader is None:
            raise _Rejected("UNAVAILABLE")
        raw = reader(self.scope)
        if len(raw.positions) > 500 or len(raw.open_orders) > 500:
            raise _Rejected("RESULT_TOO_LARGE")
        if len(raw.model_dump_json().encode()) > 262_144:
            raise _Rejected("RESULT_TOO_LARGE")
        portfolio = PortfolioSnapshot.model_validate_json(raw.model_dump_json())
        if (
            portfolio.as_of > self.scope.as_of
            or portfolio.currency != self.scope.instrument.currency
        ):
            raise _Rejected("SCOPE_MISMATCH")
        selected = [
            position
            for position in portfolio.positions
            if position.instrument.symbol == self.scope.instrument.symbol
        ]
        if len(selected) > 1:
            raise _Rejected("IDENTITY_MISMATCH")
        for position in selected:
            self._identity(position.instrument)
        flags, _ = _scan(portfolio, self.scope)
        with localcontext() as ctx:
            ctx.prec = 256
            ctx.rounding = ROUND_HALF_EVEN
            value = selected[0].market_value if selected else Decimal(0)
            weight = (
                (value / portfolio.equity).quantize(Decimal("0.00000001"))
                if portfolio.equity
                else Decimal(0)
            )
            largest = max(
                (position.market_value for position in portfolio.positions), default=Decimal(0)
            )
            largest_weight = (
                (largest / portfolio.equity).quantize(Decimal("0.00000001"))
                if portfolio.equity
                else Decimal(0)
            )
        return PortfolioMetrics(
            input_digest=digest_bytes(portfolio.model_dump_json().encode()),
            instrument=self.scope.instrument,
            currency=portfolio.currency,
            quantity=selected[0].quantity if selected else Decimal(0),
            market_value=value,
            portfolio_weight=weight,
            largest_position_weight=largest_weight,
            position_count=len(portfolio.positions),
            cash=portfolio.cash,
            equity=portfolio.equity,
            buying_power=portfolio.buying_power,
            provenance=portfolio.provenance.model_copy(
                update={"quality_flags": tuple(sorted(flags))}
            ),
        )

    def concentration(self) -> ToolResult[PortfolioMetrics]:
        """Decimal-only position weight and largest-position concentration."""
        return self._execute(
            "concentration",
            self._portfolio,
            PortfolioMetrics,
            lambda data: data.provenance,
            lambda data: set(),
            EvidenceKind.TECHNICAL,
        )

    def buying_power(self) -> ToolResult[BuyingPowerData]:
        """Observed aggregate buying power; no order sizing or recommendation."""

        def read() -> BuyingPowerData:
            data = self._portfolio()
            return BuyingPowerData(
                currency=data.currency,
                buying_power=data.buying_power,
                cash=data.cash,
                equity=data.equity,
                provenance=data.provenance,
            )

        return self._execute(
            "buying_power",
            read,
            BuyingPowerData,
            lambda data: data.provenance,
            lambda data: set(),
            EvidenceKind.TECHNICAL,
        )

    def is_complete(self, required: tuple[ToolName, ...]) -> bool:
        """A failed/partial call permanently prevents completion for this run."""
        if (
            not required
            or len(set(required)) != len(required)
            or any(name not in TOOL_NAMES for name in required)
        ):
            return False
        return not self._failed and set(required).issubset(self._completed)

    def resolve_evidence(self, ids: tuple[str, ...]) -> tuple[EvidenceCitation, ...]:
        """Only evidence actually returned by this run can support its narrative."""
        if (
            not ids
            or len(ids) > 128
            or len(set(ids)) != len(ids)
            or any(key not in self._evidence for key in ids)
        ):
            raise ValueError("unknown, duplicated or oversized research evidence references")
        return tuple(self._evidence[key] for key in ids)
