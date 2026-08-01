# Data adapter contracts

`ainvest.data` is the read-only normalization boundary between third-party
providers and the research, strategy, and risk layers. Provider SDK objects,
sessions, credentials, response dictionaries, and provider exceptions must not
cross this boundary.

## Ports and models

P04-T0 defines one synchronous port per capability:

- `QuotePort`
- `PriceBookPort`
- `OhlcvPort`
- `FundamentalsPort`
- `CorporateActionPort`
- `NewsEventPort`
- `InstrumentMetadataPort`

Every method accepts a frozen, versioned request model with an explicit timeout.
Potentially large result sets use `page_size` plus an opaque `cursor`; callers
must not parse or construct cursors. Each cursor is bound to a digest of the
normalized request filters, so it cannot be replayed against another symbol,
window, interval, adjustment, or event filter. Historical requests and their
returned `OhlcvPage` carry an explicit price-adjustment convention and a
timezone-aware UTC knowledge window.

Responses contain only versioned Pydantic models. Each observation and its
response envelope contain `Provenance`: source, observed and received times,
source timezone, delayed status, and machine-readable quality flags. A response
cannot silently mix sources, source timezones, delayed status, or quality flags.
Empty pages retain envelope provenance so “no items” is still attributable to a
specific provider request. Envelopes preserve the provider's source timezone;
they do not silently relabel it as UTC.

`FundamentalObservation` is provider-independent and composes the existing
`FundamentalSnapshot`, `InstrumentIdentity`, `Provenance`, and
`EvidenceCitation` contracts. It retains reporting period, normalized reporting
context, reporting currency, earnings-time certainty, and optional non-filing
citations, so normalized sources such as Robinhood MCP are not required to
manufacture SEC evidence. `reporting_currency` describes the issuer's financial
statements and is deliberately independent from the instrument's trading
currency; every decimal fact still carries its own explicit unit. A unitless
numeric fact is rejected at this normalization boundary and cannot be assumed
comparable. The deterministic fixture includes a USD-traded foreign issuer with
EUR reporting currency and EUR-denominated facts.

`SecFundamentalObservation` is the stricter filing-derived subtype used by an
SEC/XBRL adapter. It requires a `FilingReference` plus a filing citation that
binds the exact accession number. Binding parses the locator as exactly
`filing:<source>/<accession>[#fragment]`; wrong schemes, malformed separators,
extra path components, and accession prefixes/suffixes do not match. Every
matching citation must have been observed at or after the filing's `filed_at`
time, so neither fundamentals nor related news can cite a filing before it
exists. A generic observation may not carry a `FILING` citation, which prevents
non-SEC providers from presenting generic data as primary SEC evidence. One
filing may contain facts for multiple reporting periods or normalized contexts. Fake-dataset
identity therefore includes the instrument, accession/source snapshot, period
dates, and reporting context: distinct annual, quarterly, comparative, or
segment contexts stay separate, while an exact duplicate period/context is
rejected instead of merged.

Knowledge time is fail-closed. A filing cannot be observed before `filed_at`;
filing provenance and every fundamental citation must be observed and received
no later than the fundamental snapshot's `as_of`.

SEC form names use a bounded, provider-independent grammar (one to 24 uppercase
alphanumeric groups separated by a single space or hyphen, with an optional
`/A` amendment suffix) rather than an enumerated taxonomy, so base forms,
amendments such as `10-Q/A` and `10-K/A`, and names such as `DEF 14A` remain
representable.

`CorporateActionPort` is a separate provider-independent capability for the
offline adapter in P04-T1. A closed-open effective-date request returns
discriminated `SplitObservation` and `DividendObservation` records. Splits use
an explicit positive new-shares-per-old-share ratio. Cash dividends use an
explicit positive amount and currency. Both retain canonical instrument
identity, effective date, declaration date when available, and provenance;
dividends also retain pay date when available. Missing applicable declaration
or pay dates require `MISSING_FIELDS`, and item quality flags propagate to the
page envelope. A declaration date cannot be later than the action provenance's
observed date.

`NewsEventObservation` composes the existing `MarketEvent` and
`EvidenceCitation` contracts. It retains HTTPS URL, publisher, publication
time, license, zero or more affected symbols, event-time certainty, multiple
citations, and any related filing references. The underlying single-symbol
field remains consistent with the affected-symbol set while industry and macro
events may legitimately have no symbol.

Filing-document and news URLs share one Pydantic-parsed external-HTTPS type.
The policy requires an `https` scheme and a syntactically valid host, forbids
embedded username/password credentials, rejects fragments to keep the stored
resource identity canonical, and caps the full input at 2,048 characters.

Instrument metadata composes the existing canonical `InstrumentIdentity` rather
than using a symbol as identity or duplicating the risk engine's evaluation
model. The port adds observed tradability and price/quantity increments needed
to normalize provider data safely.

## Stable failures

Adapters translate failures into a concrete `DataProviderError` subclass.
Callers branch on `DataErrorCode`, `DataOperation`, or the exception class—never
on message text. The taxonomy distinguishes authorization, timeout, rate limit,
not found, invalid request, unsupported capability, upstream failure, schema
incompatibility, incomplete data, stale data, and conflicting data.

Only read-only timeout and rate-limit failures are marked retryable. A retrying
caller still owns its attempt bound and deadline.

Missing requested quotes, books, metadata, or an unknown OHLCV/corporate-action
instrument raises stable typed errors. A known OHLCV series or corporate-action
instrument may return a provenanced empty page for a valid window with no
observations. Partially or completely missing requested fundamentals return
`PARTIAL` plus `MISSING_FIELDS` on the response envelope; an unqualified empty
fundamentals page is forbidden. One-sided or empty price books likewise require
`PARTIAL` or `MISSING_FIELDS`.

## Live market data

Under DEC-003, live quotes and live price books are separate pinned capability
ports:

- `LiveQuotePort` identifies `robinhood.mcp.get_equity_quotes`.
- `LivePriceBookPort` identifies `robinhood.mcp.get_equity_price_book`.

These protocols expose no provider list, fallback chain, or automatic fallback
method. A future Robinhood read gateway must fail closed when its pinned tool
schema or result is unavailable, incomplete, stale, or inconsistent. Offline
providers such as yfinance may implement the ordinary research ports but must
not implement or be passed as either live port.

## Deterministic provider tests

`DeterministicFakeDataProvider` uses the immutable dataset returned by
`fixture_dataset()`. It has no network or clock, supports stable operation-level
failure injection, and uses dataset/operation/query-scoped cursors. Dataset
source/timezone inconsistencies are rejected at construction and normalized as
`DataSchemaError`; provider calls never leak Pydantic validation failures.
Repeated identical requests return identical serialized models.

Key-addressed fixture collections reject duplicate identities before the fake
builds lookup maps: instrument ID for quotes/books,
instrument/interval/start-time for OHLCV bars, normalized
source/period/context for fundamentals, action ID for corporate actions, event
ID for news, and both instrument ID and symbol for metadata. Invalid raw
fixture mappings surface the stable `FAKE_DATASET_INVALID` schema error; no
lookup uses last-write-wins behavior.

The fake also builds one canonical identity map across quotes, books, bars,
fundamentals, corporate actions, and metadata. Reusing an `instrument_id` with
a conflicting symbol, exchange, trading currency, or asset type rejects the
entire dataset as `FAKE_DATASET_INVALID`.

The shared contract suite is `tests/contract/data/test_provider_ports.py`.
Factories are registered independently for quote, book, OHLCV, fundamentals,
corporate actions, events, and metadata. A provider implements and joins only
the capability fixtures it supports; it is never required to implement every
capability. New adapters should add a recorded/no-network factory for each
implemented capability, then add provider-specific response-normalization
tests. Contract tests must never depend on public network availability or real
credentials.

## Yahoo development-only adapter

`YahooDevelopmentAdapter` is an optional `yfinance` adapter for local
development, offline research, and historical replay. Install it with the
`offline-data` extra. The import is lazy, so core, production, and test
profiles do not load `yfinance` merely by importing `ainvest.data`.

The adapter is explicitly `development_only`, implements only the ordinary
`QuotePort`, `OhlcvPort`, and `CorporateActionPort` shapes, and is not a
`LiveQuotePort`. Construction in `TradingMode.LIVE` fails before optional
dependency loading or transport access. It must never be configured as a
Robinhood fallback or supply live risk and execution inputs.

Canonical instrument identity and the IANA exchange timezone are injected by
the caller; Yahoo symbols are never treated as broker instrument IDs. Every
result is marked delayed and unverified and retains the exchange timezone and
trading currency. Quotes retain the source bar timestamp as `observed_at`,
including when yfinance serves a cached bar; `received_at` records this
adapter's retrieval time. The adapter has no additional result cache.

Historical requests make adjustment semantics explicit: `RAW` maps to
unadjusted Yahoo prices, while `SPLIT_AND_DIVIDEND` maps to Yahoo's total
adjustment. `SPLIT` is rejected because yfinance does not expose a trustworthy
split-only price series without dividend-adjustment ambiguity. Supported
intervals and lookback windows are bounded, pages contain at most 500 records,
and the adapter rejects oversized responses, malformed values, naive,
duplicate, or out-of-order timestamps instead of repairing them silently.
One monotonic deadline covers the complete port request: sequential symbol
calls receive only the remaining budget, and expiry discards all partial work.

Yahoo action rows normalize only positive splits and cash dividends. Yahoo's
history response does not provide authoritative declaration or payment dates,
so those fields remain absent with `MISSING_FIELDS`; values are never guessed.
Empty quote responses are errors. Empty historical and corporate-action
windows remain valid, provenanced pages for an explicitly configured
instrument. Tests use a checked-in recording behind an injected transport and
make no public requests. Corporate-action requests reject windows over 3,660
days and future effective-date windows, using the single configured exchange
timezone's calendar date, before the first transport call, then
apply an independent 10,000-row/result cap as defense in depth. The same
recorded factory participates in the shared quote, OHLCV, and corporate-action
provider contract suite.
