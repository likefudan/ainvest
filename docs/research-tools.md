# Deterministic research tools

The offline narrative consumer is described in [research-agent.md](research-agent.md).

`ainvest.agents.tools` exposes eight named operations: quote, price book,
history, indicators, filings, news, concentration and buying power. Each run
has a fixed `ToolScope`: run ID, full instrument identity, UTC knowledge cutoff,
freshness limit and timeout. The scope is immutable. `HistoryInput` additionally
supplies the exact expected bar grid and adjustment from P04-T4.

`ReadSources` is a trusted composition interface containing only six named
normalized read functions. The model does not select an arbitrary capability,
provider, account or URL. Composition must inject the reviewed Robinhood read
projection for its capabilities, SEC captures for filings and the news adapter
for news. This library neither opens those integrations nor promotes their
display-only output into verified domain data. There is no default provider or
automatic fallback. Tests inject synthetic immutable data.

## Results and evidence

`ToolResult` uses local schema version 1.0. A result is `complete`, `partial` or
`error`. Error results contain a stable code, PARTIAL/MISSING_FIELDS and no data
or evidence. Partial results retain useful normalized data and all source flags.
Future metadata, identity/currency conflicts, malformed results and unknown
extensions are rejected. Only fresh, validated, unflagged results are complete.
An empty news result or paginated news is partial. Filing metadata is always a
partial view, not proof that every filing or disclosure has been read.

For example, an unavailable quote source produces:

```json
{"schema_version":"1.0","tool":"quote","run_id":"research_example_001","as_of":"2026-07-23T14:31:00Z","status":"error","data":null,"evidence":[],"quality_flags":["PARTIAL","MISSING_FIELDS"],"error_code":"UNAVAILABLE"}
```

Successful results include deterministic run-bound evidence IDs and locators.
Original news citations and sources remain intact; SEC accession locators are
added for filing metadata. Portfolio weights use Decimal division and fixed
half-even rounding to eight decimal places. Their citations contain the numeric
value and calculation source; inputs are digest-bound and the calculation is
versioned. Buying power is the observed amount, not a recommended order budget.
Portfolio outputs omit account/snapshot identifiers and order details.

`resolve_evidence(ids)` accepts only IDs actually returned in that run. It
rejects unknown, repeated or oversized reference sets. `is_complete(required)`
requires every named tool in a nonempty, unique required set to have succeeded.
Any partial or failed call permanently prevents completion of that run, even
if a later call succeeds. This gate does not establish trading eligibility or
validate AI claims; narrative validation belongs to P04-T6/P04-T7.

## Bounds and lifetime

Use `ResearchTools` as a context manager. One worker handles reads per run,
with a 1–30 second timeout. Concurrent calls fail with RUN_BUSY; there is no
unbounded queue. A timeout closes the run and prevents additional reads.
The timed-out reader may finish later, but its data and citations are discarded.
Closing explicitly also prevents a pending result from being published.

Python threads cannot forcibly stop a reader. Trusted readers must set bounded
network/SDK timeouts and release resources when those expire. A stuck reader
can delay Python process exit despite the tool returning TIMEOUT; the library
is not a sandbox for arbitrary adapters. Operational integration must enforce
reader lifetimes and deployment concurrency separately.

Each run permits at most 32 calls and 512 unique evidence IDs. A result permits
at most 128 citations and 256 KiB serialized bytes. Scanning is limited to
20,000 nodes, depth 20 and tuple length 500. History is capped at 500 bars,
books at 50 levels per side, filings at 100 records and portfolio inputs at
500 positions/orders before calculation. Oversized results fail rather than
truncate. Provider exception text and payloads never appear in error results.

No real AI request, recurring collection, storage activation, account binding,
strategy enablement or broker mutation is performed by this task. DEC-009 still
requires the owner's API project and approved budget before real AI use.
