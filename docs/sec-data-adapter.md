# SEC primary filing adapter

`ainvest.data.providers.sec` implements a research-only `FundamentalsPort` and
cached filing metadata. It supplies no quote, broker-write, or generic invocation
capability. It does not promote Robinhood display data into trading evidence.

## Source and scope

The bounded HTTPX transport uses SEC EDGAR's official
[submissions and Company Facts APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).
These public read APIs need no API key. HTTPX is already in the optional research
profile; no dependency or lock changes are needed. This adapter intentionally
uses those JSON endpoints directly rather than SDK financial-statement
inference: exact accession, decimal encoding and source reporting periods remain
visible. The existing optional EdgarTools dependency is unchanged.

Supported metadata: 10-K, 10-Q, 8-K, Form 4 and their `/A` amendments. Metadata
retains accession, acceptance time, original archive document location and an
optional report date. A Form 4 without a report date remains metadata; an absent
date is never invented to make a fundamental observation.

Selected `us-gaap` concepts are RevenueFromContractWithCustomerExcludingAssessedTax,
NetIncomeLoss, OperatingIncomeLoss, Assets and Liabilities. Other concepts,
custom extensions and IFRS are not inferred or mapped to synonyms.
Current submissions do not necessarily contain all older filing metadata;
unbound archival facts are omitted, not assigned to another accession. Results
always carry `PARTIAL`. This is not a full archive, statement reconstruction,
earnings calendar, insider-transaction parser or durable database cache.

## Capture first, query later

`capture_company(instrument=..., cik=..., identity=SecretStr(...))` is an explicit
network action. It obtains the two fixed official documents and constructs one
immutable `SecEdgarAdapter`. The ordinary query methods never perform I/O.

- CIK must match both documents; submissions must list the injected symbol.
  Canonical `InstrumentIdentity` comes from the caller, not a ticker/CIK guess.
  This checks the supplied mapping, not broker tradability or live authority.
  Alias/share-class mappings are not guessed when SEC and canonical symbols differ.
- `captured_at` is actual receipt time, not the filing date. Both observation and
  receipt provenance conservatively use that capture time; the separate filing
  acceptance time remains intact. A later query never refreshes those fields.
- `get_fundamentals(FundamentalRequest(..., as_of=...))` rejects a cutoff earlier
  than capture; `get_filings(as_of=...)` returns no metadata before capture. Old
  evidence must come from a genuinely earlier recorded capture, not backdating
  a fresh response. Constructor `captured_at` is a trusted ingestion input;
  accepting an arbitrary caller's claimed timestamp would not establish history.
- Query cursors bind to capture content, identity, time and query filters. They
  are integrity checks for local pagination, not credentials or authorization.
  Missing requested symbols/facts add `MISSING_FIELDS`; no unqualified empty
  fundamentals result is produced.

The normalized immutable objects cache accession and citation locations in memory
for this adapter's lifetime. Persisting captures, retention and multi-company
cache refresh are later storage/composition work; no production database is
created or changed here.

## Fact comparability and citations

Money stays exact `Decimal`: the transport parses JSON decimal numbers directly,
and the normalizer rejects already-rounded Python floats and booleans. Each
observation is grouped by accession, exact start/end, fiscal metadata, currency
and instant/duration context. Currency is the fact's reporting currency, which
may differ from the instrument's trading currency; there is no FX conversion.

Assets/Liabilities use `CONSOLIDATED_INSTANT` with start equal to end. Duration
facts require a source start date and use `CONSOLIDATED_DURATION`; quarterly and
year-to-date durations remain separate. Do not interpret `fp=Q2` alone as a
three-month duration: inspect start/end. SEC fiscal labels describe a filing,
so older comparative periods are omitted instead of relabelled with that
filing's current fiscal year. Only facts ending on the bound filing's report
date are emitted. Missing units/periods, incompatible form/date, future evidence
and conflicting duplicate values fail closed. Equal repeated facts deduplicate.

Each result is a `SecFundamentalObservation` with `FilingReference` and an
accession-bound `EvidenceCitation`. The returned `SecFundamentalPage` subclasses
the ordinary port's page but retains SEC fields during JSON serialization.
Deserialize SEC pages with `SecFundamentalPage.model_validate_json`, not a base
observation parser that has no filing field. Citations point to the exact
accession; original archive URLs are on the filing. They do not claim that the
entire filing text was fetched or analyzed.

An 8-K or a financial reporting period is not proof of an earnings announcement
timestamp. `earnings_at=None` and `earnings_time_certainty=UNKNOWN` remain explicit.

## Safe public-data transport

SEC's [fair-access guidance](https://www.sec.gov/about/developer-resources) limits
aggregate traffic across machines. This adapter applies a conservative process-
wide ceiling of five requests per second; deployment must coordinate the total
budget across processes/hosts. Do not treat a process-local limiter as a shared
distributed quota.

The caller must supply a legitimate contact identity in the User-Agent. There is
no guessed name/email, environment auto-discovery, prompt or saved identity.
The value must remain in protected runtime configuration; never copy a real
email or header into fixtures, logs or Git. Shape validation does not prove
ownership of the supplied contact address.

Only fixed `https://data.sec.gov` URLs are requested; redirects and environment
proxies are disabled. There are no automatic retries. Each operation has a
1–120 second budget including rate waits and both requests, each response is
capped at 64 MiB, and selected raw fact arrays are capped at 100,000 total rows.
Malformed/duplicate-key JSON and non-finite constants are rejected. Normalization
is also checked against the deadline before returning (it is not preemptively
cancelled mid-parse). Error text never includes the response or contact header.

`403`, `404`, `429`, timeouts and other upstream failures map to stable data-error
classes. Unknown redirects/errors do not trigger a different source. Callers
must bound any later read retry; this adapter never retries automatically.

## Verification and activation

Fixtures under `tests/fixtures/sec/` are deliberately synthetic SEC-shaped data,
not a claim of a successful live download. Unit tests inject HTTPX transports;
contract tests prove typed serialization, deterministic results, provenance and
absence of live capabilities. Canonical `./scripts/dev verify` requires no SEC
contact identity or public requests.

Real-source acceptance still needs a legitimate owner-supplied contact identity
and an explicitly verified company/instrument mapping. Do not enable a research
runner or call SEC simply because this module was imported. No staging poller,
OpenAI budget, strategy, risk limit or Live switch is changed by this task.
