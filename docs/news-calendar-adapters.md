# News discovery and regular-session calendar

These are read-only library components, not a running news collector. No
staging service, database, retention job, strategy or Live gate is activated.

## Capture boundaries

`capture_gdelt(query=...)` makes one bounded request to the official
[GDELT DOC API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/).
It requests at most 250 article metadata records, caps decoded content at 2 MiB,
uses a 1–120 second deadline and disables redirects and environment proxies.
It never fetches returned URLs, article text or images and never retries.
Repeated capture scheduling and aggregate throttling belong to future ingestion.

The result is `DiscoveryRecord`, not a verified event: `seendate` is retained
as `seen_at`, `published_at` stays null, and symbols stay empty. Search terms
do not establish company identity. Only separately verified publication metadata
may enter `SourceRecord` and the NewsEventPort. Unsupported HTTP-only URLs fail
closed rather than being silently upgraded. Search results are partial.

`normalize_ir` accepts already captured title/URL/publication metadata against
an exact, caller-reviewed IR hostname, explicit symbols, publisher and license.
It does not scrape arbitrary HTML/RSS or discover host ownership. The trusted
ingestion caller verifies those inputs and receipt times; hostname comparison
alone is not authentication. `sec_events` projects P04-T2 8-K/Form 4 metadata
and amendments, retaining accession citations, archive URLs and receipt times.
The caller supplies the verified company/symbol association.

Macro discoveries can use empty symbol sets and `MACRO_EVENT` on verified
metadata. No economic release calendar, consensus figures, scheduled future
release predictions or commercial feed integration is claimed.

## Immutable event projection

`NewsAdapter(records=..., captured_at=...)` holds at most 10,000 metadata
records in memory. Ordinary queries do no I/O. Exact URLs deduplicate by default;
an explicit reviewed `event_key` can associate multiple sources. Similar
headlines alone never establish a match. Mixed event types fail closed.
Distinct sources retain URLs, headlines, times, source kinds and restrictions
in `NewsObservation.sources`; identical source records collapse. Conflicting
headlines/times are flagged. Groups are bounded to 100 citations. Primary
metadata is preferred for display without upgrading discovery citations.

Observation provenance identifies the aggregator; each citation retains its
original source. All responses are PARTIAL; discovery adds UNVERIFIED. Queries
select a closed-open publication window and exclude captures not yet known by
`end_at`. Before-capture queries return no items; their envelope still honestly
records actual capture time. Cursors bind to the capture and filters.

Deserialize with `NewsPage.model_validate_json` to retain provider-local source
details; shared schemas are unchanged. Publication/filing time does not prove
the underlying business-event time, so event-time certainty remains UNKNOWN.
Discovery grants no rights in linked content: only metadata is retained,
quotation policy is `metadata_only`, and per-source restrictions must survive
downstream display, export and persistence. No full article text is collected.

## Calendar and risk

`ExchangeCalendar(valid_from=..., valid_through=...)` implements the existing
MarketCalendar port using locked
[pandas-market-calendars schedules](https://pandas-market-calendars.readthedocs.io/en/latest/usage.html).
Only XNYS/NYSE and XNAS/NASDAQ are explicitly mapped. Unknown venues, naive
times, dates outside the reviewed horizon, dependency failures and malformed
or interrupted schedules return UNKNOWN, never OPEN.

UTC and aware local times are accepted; opening is inclusive and closing is
exclusive. Holidays, DST and early closes use library schedules, cached for at
most 512 venue/days. This is not a halt feed and cannot predict subsequently
announced closures. Runtime composition must review the horizon and version.
Risk consumes the existing protocol; integration tests reject at early close.

The optional research runtime dependency is additionally installed for development
so canonical CI tests actual schedules. Tests use synthetic news and mock HTTP;
no real contact information or external network access is needed. Persistent
storage, actual IR feed wiring and scheduled collection remain later work.

## Linux process-composition limitation

The first full Linux CI run exposed an interaction with the existing strategy
worker's peak-RSS watchdog: after pandas is loaded in the test parent, later
exec'd children can inherit a high-water memory measurement and fail closed
as OOM even for healthy strategies. Actual calendar/risk tests now run in
separate subprocesses; malformed schedule tests use lightweight frame doubles.
All assertions and worker memory limits remain intact. The separate worker
correction now reads the Linux address space's own `VmHWM` from bounded
`/proc/self/status`, with conservative lifetime-rusage fallback if proc is
unavailable or malformed. If neither counter can be read reliably, the watchdog
terminates the worker rather than silently abandoning monitoring. macOS units,
memory limits, hard resource limits and polling cadence are unchanged.

[Linux documents VmHWM as resident high water](https://docs.kernel.org/filesystems/proc.html),
while [getrusage accounting survives exec](https://man7.org/linux/man-pages/man2/getrusage.2.html).
The regression launches healthy and genuinely oversized workers from a parent
holding 320 MiB; healthy execution must succeed and oversized execution must
still fail. The subprocess calendar tests remain isolated for test hygiene.
This addresses inherited-memory false positives, not general production sandbox
hardening: kernel/container limits and deployment verification are still needed.
No combined runtime deployment is claimed here.
