# Research snapshots

This library is opt-in, research-only and not connected to staging, Telegram,
broker execution or scheduled collection. It does not confer verified identity,
account binding, market-session evidence or trading eligibility.

## Calculations and quality

`BarExpectation` supplies the full instrument, provider, interval, adjustment,
as-of time, maximum age and exact expected bar starts. The caller must supply a
closed-bar grid from its reviewed session policy; no trading calendar, fill,
sorting or resampling is inferred here. Gaps, duplicates, disorder, mismatches,
future or stale observations, delayed inputs and quality flags reject calculation.

TA-Lib computes SMA(20), SMA(50), RSI(14) and ATR(14). SMA requires its full
window; RSI and ATR require 15 observations. Missing warm-up values are null,
with PARTIAL/MISSING_FIELDS, never fabricated zeroes. Compatibility and unstable
period settings must remain at defaults. Use in a trusted process that does not
mutate TA-Lib global settings concurrently.

The explicit technical-only precision boundary converts Decimal prices to
NumPy binary64, then serializes finite outputs as Decimal rounded to eight
places. This is not money arithmetic or an order-sizing API. The input digest,
parameters, numeric mode, TA-Lib Python/C versions and NumPy version are retained.
See the upstream [TA-Lib wrapper](https://github.com/TA-Lib/ta-lib-python) for
binary wheels and the [indicator API](https://ta-lib.github.io/ta-lib-python/)
for lookback behavior.

## Snapshot and replay

`build_snapshot` takes normalized captured inputs and source/role-bound raw
response digests. These digests are supplied by the capture layer: they are not
proof of source authenticity, and raw response bodies are not stored here.
Quotes must pass freshness/binding checks. Optional news and SEC fundamentals
retain citations, source metadata and quality flags; partial evidence is not
promoted to complete. Snapshots include normalized inputs, normalization version,
calculation metadata and the assembled ResearchPacket.

`replay_snapshot` performs no network requests. It rebuilds the packet and
compares the complete snapshot, including runtime versions. Replay therefore
requires the recorded calculation environment; dependency upgrades do not
silently accept old results.

## Opt-in storage and capacity

`SnapshotStore(root, max_total_bytes=..., max_entry_bytes=..., max_entries=...)`
requires all three positive quotas. No operational quota is selected here.
Only the private root itself is created (0700); its parent must already exist.
Files are owner-only (0600). POSIX local filesystems with flock, dir-fd operations
and hard links are required; this is not a multi-host or hostile-owner sandbox.

The key includes the full instrument (including currency), provider, timeframe,
adjustment, as-of, expected bar grid and age policy. Different content under the
same key is a conflict, not an overwrite. Identical writes are idempotent even
when full. Writers serialize under a five-second lock deadline and publish a
fully flushed record atomically without replacing existing records. Reads are
size bounded, check private regular files, canonical encoding and content digest,
then replay calculations. Symlink entries and arbitrary path keys are rejected.

Capacity exhaustion returns an error. There is **no automatic deletion, eviction,
collection or retention policy**. Quotas count serialized records, excluding the
small lock file and filesystem overhead; a new write temporarily needs one
record's additional space. A process crash can leave a private pending file;
unknown files fail closed and need operator inspection, not automatic removal.
Hashes detect corruption, not malicious rewriting by the same OS user. Disk
failure, backups, retention and RPO/RTO still require separate operational work.
All tests use temporary synthetic data; no production cache is provisioned.
