# Evidence-bound research assembly and replay

P04-T7 is an offline library. It does not enable a model, schedule collection,
provision storage, or alter staging. Real AI remains gated by DEC-009; trading
and strategy execution are not part of this library.

[Offline evaluations](research-evaluations.md) provide repeatable safety/quality
reports and a synthetic explicit-budget admission boundary, not release approval.

`build_research_archive(request, agent_result, tools=...)` reconciles run ID,
instrument/cutoff, request limits, input/prompt/schema/output digests, actually
returned tool captures and citations. With the originating tool registry it also
checks registry membership. Financial fields come only from typed tool data,
never model prose: quotes supply market values, portfolio metrics supply account
context, and indicators are recomputed from retained history under fixed decimal
rounding. Required stale/future/malformed inputs yield an error with no packet.
Missing optional technical/portfolio data stays absent and yields a partial
packet. Hypotheses, partial tools or unknown model usage cannot become complete.

An immutable archive retains the packet, claim-to-evidence mapping, normalized
tool-return JSON, versions, timestamps, request/response IDs, observed token
usage and digests. Captures are limited to 256 KiB each and 1 MiB per run; the
archive payload is limited to 2 MiB. These are the exact normalized tool returns,
not original broker/provider wire bodies. Original transport evidence remains
the responsibility of a separately reviewed data composition; none is invented.

`replay_research_archive(archive)` verifies the archive digest and rebuilds a
successful packet solely from retained inputs, without a model/provider call.
Changed packet fields or inconsistent technical results are rejected. Error
archives replay to no packet. Digests detect corruption and inconsistent edits,
not a privileged attacker replacing both content and every digest; private
storage access and independent audit controls still matter.

## Optional persistence boundary

Inside an active `UnitOfWork`, `research_repository(quota)` exposes only domain
archives, not ORM rows. Append stores the run, optional packet and audit event in
one transaction using existing tables. Identical append is idempotent; a
different archive with the same run ID conflicts. Reads reconcile indexed rows,
archive content and the audit checkpoint and replay before returning data.
There is no update, delete, automatic eviction or retention selection.

The caller must explicitly supply both `ResearchStorageQuota.max_records` and
`max_record_bytes`; there are no operational defaults. The byte quota measures
the archive plus duplicated packet JSON. It is a logical content limit, not a
disk reservation: database pages, indexes, WAL, audit rows and backups add
overhead. Quota exhaustion rejects new data without deleting old records.
DEC-013/014 retention, backup and recovery decisions remain open.

Synthetic SQLite tests cover concurrent quota enforcement, transaction rollback,
idempotency, conflict, tamper detection and network-free reload/replay. PostgreSQL
uses a cooperating-writer table lock but has not been integration-tested here;
that path requires validation before deployment. No operational database,
credential, Telegram poller or live account was used or changed.
