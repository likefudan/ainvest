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
- `NewsEventPort`
- `InstrumentMetadataPort`

Every method accepts a frozen, versioned request model with an explicit timeout.
Potentially large result sets use `page_size` plus an opaque `cursor`; callers
must not parse or construct cursors. Historical requests and their returned
`OhlcvPage` carry an explicit price-adjustment convention and a timezone-aware
UTC knowledge window.

Responses contain only versioned Pydantic models. Each observation and its
response envelope contain `Provenance`: source, observed and received times,
source timezone, delayed status, and machine-readable quality flags. A response
cannot silently mix sources, source timezones, delayed status, or quality flags.
Empty pages retain envelope provenance so “no items” is still attributable to a
specific provider request.

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
failure injection, and uses dataset/operation-scoped cursors. Repeated identical
requests return identical serialized models.

The shared contract suite is
`tests/contract/data/test_provider_ports.py`. New adapters should add a
recorded/no-network provider factory to that suite for every capability they
implement, then add provider-specific response-normalization tests. Contract
tests must never depend on public network availability or real credentials.
