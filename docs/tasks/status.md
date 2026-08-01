# Implementation Task Status

This file is the cross-agent coordination record for implementation work. It
records who owns a task, the exact source state they inherited, their permitted
write scope, dependencies, verification contract, blockers, and handoff. It is
not a substitute for the task card in `IMPLEMENTATION_TODO.md`.

Last updated: 2026-08-01

## Status vocabulary

- `not_started`: unclaimed and no implementation is in progress.
- `in_progress`: claimed by one owner who is actively implementing it.
- `blocked`: unable to proceed; the blocker and the evidence needed to clear it
  are recorded in the task envelope.
- `in_review`: implementation and owner checks are complete, but integration,
  review, or required CI is pending.
- `merged`: the task's PR or integration commit is on `main` and the resulting
  commit is recorded.
- `superseded`: the task was replaced; the replacement task/decision is linked.

## Update and concurrency rules

1. Read `design.md`, `IMPLEMENTATION_TODO.md` section 1, the complete task card,
   the decision register, this file, and current Git state before claiming work.
2. One task has one active owner. Set `owner`, `status`, `branch`, `base commit`,
   allowed paths, and dependencies before editing.
3. The base commit is immutable for an execution envelope. If the branch moves
   or a dependency is replaced, record the new commit and revalidate before
   continuing; do not silently copy an unmerged dependency.
4. Ownership is path-based as well as task-based. Do not edit another active
   task's allowed paths. Coordinate shared or unexpected files first and record
   the agreement under handoff notes.
5. A shared integration branch may be used only when the batch coordinator
   explicitly assigns disjoint paths. Sub-agents do not commit, rebase, push, or
   merge unless the coordinator explicitly delegates that action.
6. Move a task to `blocked` as soon as progress requires an unresolved external
   choice, missing dependency artifact, scope expansion, or weaker safety rule.
   Record a fail-closed fallback; never guess credentials or owner values.
7. Before `in_review`, inspect the scoped diff, check for secrets, run the
   canonical verification commands plus task-specific checks, and fill in the
   handoff notes. Before `merged`, record the final commit and PR.
8. Never delete completed history. If a task is replaced, mark it `superseded`
   and link the replacement.

## Canonical verification commands

`P01-T2` established `./scripts/dev` as the repository command wrapper. Agents
must use these commands rather than invent tool-specific variants:

| Purpose | Canonical command |
|---|---|
| Locked environment setup | `./scripts/dev setup` |
| Lock-file consistency | `./scripts/dev lock-check` |
| Formatting check | `./scripts/dev format-check` |
| Lint | `./scripts/dev lint` |
| Type check | `./scripts/dev type-check` |
| Unit tests | `./scripts/dev unit` |
| Contract tests | `./scripts/dev contract` |
| Integration tests | `./scripts/dev integration` |
| Full test suite with coverage | `./scripts/dev test` |
| Complete local merge gate | `./scripts/dev verify` |

Only `./scripts/dev lock` and `./scripts/dev format` intentionally modify
tracked project artifacts. See `docs/development.md` for dependency profiles and
the dependency-change workflow.

## Batch naming

Batch IDs match `IMPLEMENTATION_TODO.md` section 12 (`Batch A`, `Batch B`,
…). When a plan batch is delivered in more than one integration part, record
parts as `Batch <Letter> — Part N` (or the plan's `B1`/`B2` labels). Mark the
plan batch complete only when every card in that section has merged.

| Record | Plan section | Cards | Status |
|---|---|---|---|
| Batch A — Part 1 | Batch A | `P01-T0`, `P01-T2` | complete (merged) |
| Batch A — Part 2 | Batch A | `P01-T1`, `P01-T3`, `P01-T4`, `P01-T5` | complete (merged) |
| Batch A complete | Batch A | all of the above | complete |
| Batch B — Part 1 (B1) | Batch B | `P02-T0`, `P02-T1` | complete (merged) |
| Batch B — Part 2 (B2) | Batch B | `P02-T2` | complete (merged) |
| Batch B — Part 3 (B3) | Batch B | `P02-T3`, `P02-T4` | complete (merged) |
| Batch B — Part 4 (B4) | Batch B | `P02-T5` | complete (merged) |
| Batch B complete | Batch B | all of the above | complete |
| Batch C — Part 1 (C1) | Batch C | `P02-T6`–`P02-T8` | complete (merged) |
| Batch C — Part 2 (C2) | Batch C | `P03-T0`–`P03-T3` | complete (merged) |
| Batch C — Part 3a (C3a) | Batch C | `P03-T13` | complete (merged) |
| Batch C — Part 3b (C3b) | Batch C | `P03-T14` | merged |
| Batch C — Part 4a (C4a) | Batch C | `P03-T8`, `P03-T10`, `P03-T11` | merged |
| Batch C — Part 4b (C4b) | Batch C | `P03-T9` | merged |
| Batch C complete | Batch C | all of the above | complete |
| Batch D — Part 2a (D2a) | Batch D | `P03-T6` | complete (merged) |
| Batch D — Part 3a (D3a) | Batch D | `P02-T9` | complete (merged) |
| Batch D — Part 1a (D1a) | Batch D | `P03-T4` | complete (merged) |
| Batch D — Part 1b (D1b) | Batch D | `P03-T5` | complete (merged) |
| Batch D — Part 2b (D2b) | Batch D | `P03-T7` | complete (merged) |
| Batch D — Part 2c (D2c) | Batch D | `P03-T12` | merged (#67) |
| Batch D — Part 3b (D3b) | Batch D | `P02-T10` | complete (merged) |
| Batch D — Part 3c (D3c) | Batch D | `P03-T15` | complete (merged) |
| Batch D — Part 4a (D4a) | Batch D | `P03-T16` | complete (merged) |
| Batch D — Part 4b (D4b) | Batch D | `P03-T17` | complete (merged) |
| **Batch D** | Batch D | `P03-T4`–`T17`, `P02-T9`–`T10` | **complete (Gate 1 accepted)** |
| Batch E — Research | Batch E | `P04-T0`–`P04-T12` | `in_progress` (`P04-T0` merged) |
| Batch E — Paper approval | Batch E | `P05-T0`, `T1`, `T4`–`T6`, `T8` | `in_progress` (`P05-T0` merged) |
| Batch E — Deferred live approval | Batch E | `P05-T7`, `P08-T14`, `P05-T2`, `P05-T3` | `not_started`; owner decisions remain deferred |
| Batch E — Cross-cutting foundation | Batch E | `P08-T0`, `T3`–`T9`, `T12`–`T14` | `in_progress` (`P08-T0`, `P08-T3`, `P08-T7` merged) |
| Robinhood Read-only Preview | Batch E/F priority lane | external `rh-mcp` release, `P08-T7`, `P06-T0`–`P06-T2` | `blocked` (`P08-T7` merged; `P06-T0` unclaimed pending reviewed `rh-mcp` release/manifest); serial merge queue |

Do not invent numeric variants such as `1A` or `Batch 1A`.

## Active batch

**Batch E is active.** Its immutable starting point is
`3781fc165096aff1d4827b7e9c232e5c330b1e9e`, after Batch D / Gate 1 and its
tracker cleanup were merged. Gate 1 remains accepted; see
[`docs/releases/phase-1-acceptance.md`](../releases/phase-1-acceptance.md).

### Batch E canonical coordination index

This section is the canonical dispatch index for Batch E. The complete
requirements and acceptance criteria remain the task cards in
`IMPLEMENTATION_TODO.md`; this index records ownership, safe parallelism, write
scope, and integration order.

#### Integration policy

1. Every task is implemented by its own sub-agent in a Git worktree under the
   main repository's `.worktrees/` directory. One active task has one branch and
   one owner.
2. Implementations may run concurrently when their dependencies and allowed
   paths do not overlap. PRs are integrated one at a time.
3. Immediately before a PR enters review, its branch must rebase onto the
   latest `main`, resolve conflicts without weakening safety contracts, rerun
   `./scripts/dev verify`, and update the PR branch.
4. A sub-agent independent of the implementation must review functionality,
   fail-closed behavior, tests, readability, duplication, and dependency use.
   Review findings must be fixed and re-reviewed until no actionable finding
   remains.
5. Required GitHub checks must pass after the final rebase. Merge by squash
   only; record the PR and resulting `main` commit here before reviewing the
   next PR.
6. `docs/tasks/status.md` is coordinator-owned while task agents are active.
   `pyproject.toml`, `uv.lock`, `src/ainvest/config/**`, shared schema exports,
   the Alembic head, package `__init__.py` files, and CI workflows are shared
   surfaces: an agent must obtain coordinator assignment before changing them.
7. Owner-controlled values from `DEC-009`–`DEC-019` are never invented.
   Deterministic fakes and disabled adapters are the required fallback. No
   Batch E task enables live broker writes.

Tracker claim changes merge before their implementation PRs. The initial queue
(`P04-T0`, `P05-T0`, `P08-T0`, and `P08-T3`) and priority-lane prerequisite
`P08-T7` are merged. No P06 implementation is active. The remaining
earliest-safe **Robinhood Read-only Preview** queue is: reviewed immutable
`likefudan/rh-mcp` implementation/release → tracker records its immutable
version and expected full-manifest digest → `P06-T0` → `P06-T1` →
`P06-T2`. Later candidates enter the merge queue only after their recorded
dependencies are on `main`. The coordinator may reorder independent ready
branches to reduce conflicts, but may not bypass the rebase, review, checks, or
squash-merge rules above.

#### Initial claims

| Task | Status | Owner | Branch / worktree | Immutable base | Dependencies |
|---|---|---|---|---|---|
| `P04-T0` | `merged` | `batch_e_p04_t0` | `agent/p04-t0-data-ports` / `.worktrees/p04-t0` | `81344f5ac224c8879784db516062a8868758d230` | `P02-T1`, `P03-T13` (merged) |
| `P05-T0` | `merged` | `batch_e_p05_t0` | `agent/p05-t0-approval-challenges` / `.worktrees/p05-t0` | `e210d9601678f0abf750cf38590fd55ba10a873a` | `P02-T3`, `P02-T4`, `P02-T6`–`P02-T9` (merged) |
| `P08-T0` | `merged` | `batch_e_p08_t0` | `agent/p08-t0-runtime` / `.worktrees/p08-t0` | `62feb5d2c93ccde9eb651b79404296a39531818d` | `P01-T4`, `P03-T13` (merged) |
| `P08-T3` | `merged` | `batch_e_p08_t3` | `agent/p08-t3-logging` / `.worktrees/p08-t3` | `d9840a4ef80a380a26d7be4b0892d774fd8d43a8` | `P01-T2`, `P02-T8` (merged) |

Initial worktree evidence verified 2026-07-28: `.worktrees/p08-t0` was clean on
`agent/p08-t0-runtime`, and `.worktrees/p08-t3` was clean on
`agent/p08-t3-logging`; both initially resolved to
`263f777c0b9fc438aa8f5ab87b3a8dd108765cbd`. Before implementation, P08-T0
was rebased to its assigned implementation base, then rebased again to the
post-P05-T0 `main` commit recorded in its envelope before integration review;
P08-T3 was first rebased to
`81344f5ac224c8879784db516062a8868758d230` before implementation, then
rebased to post-P08-T0 `main`
`d9840a4ef80a380a26d7be4b0892d774fd8d43a8` before integration review.

`P04-T0` may write `src/ainvest/data/{models,ports,fakes}.py`,
`src/ainvest/data/__init__.py`, `tests/unit/data/test_models.py`,
`tests/contract/data/**`,
`tests/unit/architecture/test_package_boundaries.py`, and
`docs/data-adapters.md`. It must not add a provider SDK, live fallback,
Robinhood implementation, or Research Agent behavior.

`P04-T0` handoff:

- **Rebased main/base:** `81344f5ac224c8879784db516062a8868758d230`
- **Initial implementation tip:** `513a440002041b096d821363834aedbef207b312`
- **First review remediation:** `90e00c488733d54270df403aea3005b50b143c24`
- **Second review remediation:** `0b7cf3f965d192c54abd1c22cb518077e37aed6a`
- **Third review remediation:** `83f3a02931b52ae89a2062e06cb69d5e9c11f217`
- **Fourth review remediation:** `d4af0a027313460a956ce6eb6e5e3904e8415922`
- **Fifth review remediation:** `7a4eb71e5f55d68677c3f6d8cd0838dbfa36d2cd`
- **Final files:** `src/ainvest/data/{models,ports,fakes}.py`,
  `src/ainvest/data/__init__.py`, `tests/unit/data/test_models.py`,
  `tests/contract/data/test_provider_ports.py`,
  `tests/unit/architecture/test_package_boundaries.py`,
  `docs/data-adapters.md`, and this P04-T0 tracker metadata.
- **Verification:** focused data/architecture contracts passed (150 tests).
  `./scripts/dev verify` passed after fifth review remediation: format, lint,
  mypy, and schema snapshots passed; unit 588, contract 97, integration 15,
  aggregate 700 tests; 86.25% branch coverage.
- **Handoff notes:** Defines synchronous provider-independent quote,
  price-book, OHLCV, fundamentals, corporate-action, news/event, and
  instrument-metadata ports; bounded timeout and query-bound opaque pagination;
  stable typed failures; source/timezone/delay/quality-preserving result
  envelopes; and explicit OHLCV adjustment/empty-window semantics. Generic
  fundamentals retain period/context/unit/reporting-currency/certainty without
  requiring SEC evidence; trading and reporting currency are independent, and
  the foreign-issuer fixture is USD-traded with EUR reports/facts. The SEC
  subtype parses an exact `filing:source/accession` path and rejects substring
  or malformed bindings. A matching filing citation cannot be observed before
  the filing's `filed_at`; filing/citation knowledge time cannot exceed snapshot
  `as_of`, and corporate declarations cannot postdate observation.
  Duplicate fundamental identity includes source/accession, period, and
  normalized context, allowing multiple filing periods without merging
  conflicts. The minimal corporate-action contract covers splits and cash
  dividends with effective/applicable dates, provenance, partial/missing
  semantics, stable errors, deterministic pagination, and no provider-specific
  Yahoo types. Licensed, published, multi-symbol/multi-citation events remain
  supported. Unitless decimal facts are rejected as non-comparable; SEC forms
  use a bounded grammar that includes amendment forms. Filing and event URLs
  share a Pydantic-parsed HTTPS-only type that requires a valid host and rejects
  credentials and fragments. Partial fundamentals and one-sided books carry
  explicit quality flags.
  Capability-scoped provider contract factories allow partial adapters. Invalid
  fake datasets, including duplicate identity keys or cross-collection
  instrument-ID conflicts in symbol/exchange/trading-currency/asset-type, become
  stable schema errors; source timezones are preserved. Failure contracts cover
  timeout, rate-limit, stale, incomplete, and upstream errors with explicit
  retryability.
  Pinned live quote/book capabilities still expose no fallback under `DEC-003`.
  The architecture test prevents provider SDK imports above the data/execution
  boundaries. No dependency, config, shared-schema, risk, broker, or live-mode
  behavior changed.
- **Blockers:** None.
- **PR:** [#76](https://github.com/likefudan/ainvest/pull/76)
- **Squash-merge commit:**
  `e210d9601678f0abf750cf38590fd55ba10a873a`

##### Execution envelope: P05-T0

- **Title:** Implement OrderProposal and One-Time Approval Challenges
- **Status/owner:** `merged` — `batch_e_p05_t0`
- **Branch/worktree:** `agent/p05-t0-approval-challenges` /
  `.worktrees/p05-t0`
- **Rebased main/base:** `e210d9601678f0abf750cf38590fd55ba10a873a`
- **Implementation tip before tracker handoff:**
  `b9a4f35ef935f2b5f514abcee44ff2f22187e05f`
- **Independent-review remediation:**
  `6bf2d69b9ed5a733012a7594b6a46efad02b1bbe`
- **Round-2 independent-review remediation:**
  `0df9309842522ea975bd26d18a2b72b7abaac1d2`
- **Round-3 independent-review remediation:**
  `bd46d1100bb74286c67e940ff0fd8d574746f57d`
- **Dependencies:** `P02-T3`, `P02-T4`, `P02-T6`–`P02-T9` (merged)
- **Design and task authority:** `design.md` sections 3.4–3.5, 5.5, 7, and
  15 Phase 3; `IMPLEMENTATION_TODO.md` sections 1 and 8 (`P05-T0`);
  `DEC-005`, `DEC-006`, and `DEC-007`
- **Final files:** `src/ainvest/approval/service.py`,
  `src/ainvest/approval/tokens.py`, `src/ainvest/approval/__init__.py`,
  `src/ainvest/schemas/approval.py`, `src/ainvest/db/repositories.py`,
  `src/ainvest/db/uow.py`, `src/ainvest/risk/engine.py`,
  `tests/unit/approval/test_approval_service.py`,
  `tests/unit/approval/test_tokens.py`, `tests/unit/risk/test_engine.py`,
  `src/ainvest/schemas/{examples,export}.py`,
  `schemas/json/v1/ApprovalChallenge{,V1_1}.json`,
  `schemas/json/v1/MANIFEST.json`,
  `tests/contract/test_schema_{fixtures,snapshots}.py`,
  `tests/contract/fixtures/ApprovalChallengeV1_1/`,
  `docs/schema-versioning.md`, and this P05-T0 tracker metadata.
  No model or Alembic migration changed because the existing proposal,
  challenge, event, hash, status, expiry, and optimistic-version columns cover
  the task.
- **Authorized review scope expansion:** The coordinator authorized changes
  only to `src/ainvest/risk/engine.py` and
  `tests/unit/risk/test_engine.py` outside the original P05-T0 envelope. The
  existing risk input digest now covers the complete canonical
  `CandidateOrder`, so approval can bind exact economics, account scope,
  strategy version, increments, and expiry. No risk rule, decision, config, or
  other risk-layer behavior changed.
- **Authorized round-2 scope expansion:** The coordinator authorized the
  schema export/example generators, schema contract tests, generated v1.1
  artifact/fixtures/manifest, and the narrow schema-versioning documentation
  update solely to preserve `ApprovalChallenge` 1.0 exactly while introducing
  an explicit cumulative 1.0/1.1 dispatcher. No unrelated schema or versioning
  policy changed.
- **Verification:** round-2 focused approval/token/risk-engine/schema-snapshot
  tests passed (177 tests); round-3 approval/hash/schema-snapshot tests passed
  (80 tests). On the rebased branch,
  `./scripts/dev verify` passed: format, lint, mypy, and schema snapshots;
  unit 680, contract 102, integration 15, aggregate 797 tests; 86.58% branch
  coverage.
- **Handoff notes:** Generates canonical URL-safe nonces from exactly 256
  CSPRNG bits, redacts token string representations, and persists only a
  versioned domain-separated digest. Proposal creation freezes the canonical
  order/hash and approved risk decision, requires account/method/scope binding,
  and constrains injected-clock TTL to 60–120 seconds. Conditional
  status+version updates make PENDING decisions single-winner under concurrent
  access and persist method/scope-bound events in the same UnitOfWork. New
  service challenges use schema 1.1 with explicit APPROVED, REJECTED, or
  EXPIRED states; the exact original 1.0 contract/artifact remains unchanged
  and its PENDING decisions transition through the original CONSUMED state.
  Repeated/non-canonical tokens, explicit-decision type confusion, changed
  proposal/risk payloads, invalid scope pairs, and expiry fail closed.
  Independent review remediation binds the APPROVED output to the complete,
  normalized `RiskContext`, requires the full default rule set and
  decision/result/violation/reason aggregate consistency, requires
  `decision.decided_at` to equal the evaluated context's `as_of`, and atomically
  claims each risk decision for exactly one proposal; validates every
  duplicated challenge field; relies on the canonical proposal parser as the
  single order-hash verification path; blocks
  raw-token dataclass/JSON/Pydantic/pickle/log-fallback serialization; restores
  v1.0 `CANCELLED` and general positive-lifetime compatibility while retaining
  the 60–120 second creation policy; dispatches exact 1.0 and 1.1 challenge
  contracts without widening 1.0; and wraps event insertion plus challenge
  transition in a savepoint so validation/uniqueness failures leave PENDING
  state intact even when caught inside the active UnitOfWork. No raw-token
  database/log exposure, broker/live enablement, or duplicate persistence
  framework remains.
- **Blockers:** None.
- **PR:** [#77](https://github.com/likefudan/ainvest/pull/77)
- **Squash-merge commit:**
  `62feb5d2c93ccde9eb651b79404296a39531818d`

##### Execution envelope: P08-T0

- **Title:** Define Runtime Modes and Startup Capability Gates
- **Status/owner:** `merged` — `batch_e_p08_t0`
- **Branch/worktree/base:** `agent/p08-t0-runtime` /
  `.worktrees/p08-t0` at
  `62feb5d2c93ccde9eb651b79404296a39531818d`
- **Design and task authority:** `design.md` sections 3.3, 3.5, 5.6, 7, 11,
  12, and 16; `IMPLEMENTATION_TODO.md` sections 1, 11 (`P08-T0`), 12
  (Batch E), and 16; `DEC-001`, `DEC-002`, `DEC-005`, and `DEC-006`
- **Dependencies:** `P01-T4` and `P03-T13`, both satisfied on the immutable
  base
- **Allowed paths:** `src/ainvest/runtime.py`,
  `tests/unit/test_runtime.py`, and `docs/runtime-modes.md`
- **Shared configuration/dependencies:** `src/ainvest/config/**`,
  `pyproject.toml`, and `uv.lock` are read-only. Reuse existing settings and
  broker ports. If the task cannot be completed without changing a shared
  surface, stop and obtain a new coordinator assignment first.
- **Forbidden paths/scope:** every other production, test, documentation,
  configuration, dependency, schema, database, migration, CI, and tracker
  path; scheduler implementation; Telegram/WebAuthn behavior; Robinhood
  clients; broker submission behavior; any permissive production `LiveGuard`
- **Verification:** `./scripts/dev unit`; `./scripts/dev verify`; inspect the
  scoped diff and secret signatures; assert the mode/capability matrix,
  redacted health summary, invalid combinations, and default-rejecting live
  startup in `tests/unit/test_runtime.py`
- **Handoff:** rebased implementation commits
  `2b99fe3ca86aa0676c3df6fb9f82bb7752efe43c` and
  `a0c07394be4ed4ea835937009b66c03ac23e40c4`; tracker handoff tip
  `321847ef0969cdd5e931a50b3454dd69b9e6bf6f`. Independent-review round-1
  remediation: `ac31cfe0598952277da478f91a42cc8ad5a52428`, with adversarial
  production-ordering follow-up
  `b16b28ccd1ee87866197c157cf8a32ca4526827c`. Provides one immutable
  capability matrix, concrete PaperBroker-only writes, optional isolated read
  port, unconditionally disabled production Live, a per-write reauthorizing
  LiveGuard proxy, factory-controlled Runtime construction, stable startup
  error codes, redacted health output, and explicit signal/approval-expiry plus
  order-monitoring scheduler capabilities. Focused adversarial runtime tests
  passed (17 tests). Independent-review round-2 remediation
  `9ac2305206e93f2825ac0bb159565e072fbd3bf1` replaces the payload-blind
  check/delegate split with request-aware guard-owned atomic delegation,
  retains only a non-secret immutable Live gate context, rejects Runtime/proxy
  copy and serialization, sanitizes write-factory failures, preserves broker
  delegate errors, and documents the trusted-process boundary. Focused
  adversarial runtime tests passed (22 tests). `./scripts/dev verify` passed
  after remediation: Ruff/format/mypy/schema snapshots, 702 unit, 102 contract,
  15 integration, and 819 total tests with 86.69% coverage.
  Independent-review round-3 remediation
  `b581906be9eea8881c944ff5d4e107b7a2006fef` replaces the replayable raw
  delegate with a thread-safe, call-scoped exactly-once capability; fails
  closed on late, omitted, repeated, concurrent, and result-substituting use;
  preserves established broker-domain errors while mapping any inconsistent
  post-delegate path to the existing reconciliation-required unknown-outcome
  taxonomy; sanitizes non-domain guard exceptions without retained
  cause/context; and makes submit/cancel authorization explicitly independent.
  Focused adversarial runtime tests passed (31 tests). Final
  `./scripts/dev verify` passed: Ruff/format/mypy/schema snapshots, 711 unit,
  102 contract, 15 integration, and 828 total tests with 86.79% coverage.
  Independent-review round-4 remediation
  `2ea9ff53928795d2f57a028b389b9209b488a818` binds every call-scoped
  delegate to its guard-call thread and rejects spawned-worker use before
  adapter preprocessing or broker access. The synchronous same-thread
  lock/lease contract is explicit in the API and runtime-mode documentation.
  The deterministic worker-thread regression returns stable guard rejection
  with zero raw touches after the public call, while the same-thread path
  remains green. Focused tests remained 31; canonical verification remained
  711 unit, 102 contract, 15 integration, 828 total, and 86.79% coverage.
  Scoped readability/duplication and secret-signature inspection found no
  copied broker/config logic or credential values.
  Independent review completed with no remaining actionable finding.
- **PR:** [#78](https://github.com/likefudan/ainvest/pull/78)
- **Squash-merge commit:**
  `d9840a4ef80a380a26d7be4b0892d774fd8d43a8`

##### Execution envelope: P08-T3

- **Title:** Add Structured Logging, Correlation, and Redaction
- **Status/owner:** `merged` — `batch_e_p08_t3`
- **Branch/worktree/base:** `agent/p08-t3-logging` /
  `.worktrees/p08-t3` at
  `d9840a4ef80a380a26d7be4b0892d774fd8d43a8`
- **Design and task authority:** `design.md` sections 3.5, 3.6, 9, 11, and 13;
  `IMPLEMENTATION_TODO.md` sections 1, 11 (`P08-T3`), 12 (Batch E), and 16;
  `DEC-005`, `DEC-006`, `DEC-009`, `DEC-010`, and `DEC-015`–`DEC-018`
- **Dependencies:** `P01-T2` and `P02-T8`, both satisfied on the immutable
  base
- **Allowed paths:** `src/ainvest/observability/__init__.py`,
  `src/ainvest/observability/logging.py`, and
  `tests/unit/observability/test_logging.py`;
  `tests/integration/test_paper_flow.py` for full-workflow correlation;
  `src/ainvest/orchestrator/paper_loop.py` only when required to propagate
  logging context, without changing domain behavior; `pyproject.toml`,
  `uv.lock`, and `docs/development.md` for the assigned dependency/setup
  contract; and `scripts/dev` only if a wrapper change proves necessary for
  the canonical clean setup and verification flow
- **Shared configuration/dependencies:** At claim time, `structlog` was
  available only through the optional observability profile. This branch
  promotes structured logging to the core runtime while keeping
  OpenTelemetry and Prometheus isolated in that optional profile; a clean
  `./scripts/dev setup` followed by `./scripts/dev verify` installs and
  exercises it. Edits to `pyproject.toml`, `uv.lock`, and
  `docs/development.md` are limited to that contract. `scripts/dev` and
  `src/ainvest/config/**` remain unchanged.
- **Forbidden paths/scope:** every other production, test, documentation,
  configuration, dependency, schema, database, migration, CI, and tracker
  path; metrics/tracing/health (`P08-T4`); alerting (`P08-T5`); secret loading
  (`P08-T7`); the complete `src/ainvest/workflow/**` package; any change to
  Paper-flow domain decisions, state transitions, risk, approval, execution,
  reconciliation, or ledger behavior; logging raw prompts, approval links,
  tokens, authorization headers, account numbers, or full money-moving
  payloads
- **Verification:** `./scripts/dev unit`; `./scripts/dev verify`; inspect the
  scoped diff and secret signatures; test the secret corpus,
  nested/exception/header redaction, stable correlation fields, JSON output,
  and preservation of funds-safety events in
  `tests/unit/observability/test_logging.py`
- **Handoff:** rebased implementation tip
  `83a8eb5a90fc7581bbea64bdab43ad1ddbff5f88` plus independent-review
  remediations `4f064f701c2bb9ffe55958f6933738a13962ec00` and
  `0d0e4b65ecd2c6e92d25b8493c2029f5caac6d7b`, followed by
  `8c4dee748ffbbea247330b5e91816eaeb5c9f515` on post-P08-T0 `main`.
  The remediations make recursive redaction bounded and cycle-aware, protect
  mapping keys and exception chains, enforce a centralized recursive contract
  for credentials and flattened financial/order/account fields, bound numeric
  extremes, and use a strict no-raise JSON renderer with a static last-resort
  fallback. Sampling-exempt money lifecycle retention is independent from
  natural severity. Overlength keys now become bounded hashed placeholders and
  fail closed. An explicit reachable `(event, outcome)` policy elevates
  `SUBMIT_UNKNOWN`, manual-review-after-unknown, and reconciliation mismatches
  to critical while classifying a successfully blocked blind retry as warning.
  Paper-flow entry resets stale IDs and progressively binds real identifiers.
  Focused logging/runtime/approval-token/Paper-flow verification passed 66
  tests. An isolated locked core-profile environment imported
  `ainvest.observability` and `structlog` while confirming OpenTelemetry and
  Prometheus remained absent. `./scripts/dev verify` passed format, lint,
  mypy, and schema snapshots; 727 unit, 102 contract, 19 integration, and 848
  aggregate tests at 86.12% coverage. Adversarial objects, boundary/overlength
  keys, hidden credential/financial suffixes, secret-looking and flattened
  fields, exception trees, sink/renderer failures, huge and non-finite
  numbers, strict JSON parsing, `sample_rate=0`, stale caller context,
  concurrent full Paper flows, and the real unknown-submit/manual-review path
  are covered.
  Readability/duplication inspection found focused helpers and no second
  logging, renderer, telemetry, or Runtime-health abstraction. The prior
  dependency audit found no known vulnerabilities. Final independent review
  completed with no remaining actionable finding.
- **PR:** [#79](https://github.com/likefudan/ainvest/pull/79)
- **Squash-merge commit:**
  `14467cdb9da999ff1f73697be9bc83c27371e7a1`

#### Priority lane — Robinhood Read-only Preview

This lane provides an early, useful Robinhood result without claiming Gate 4
or enabling broker writes. Its serial merge order is:

1. `P08-T7` establishes role-separated secret access and the read-broker
   identity boundary required by the preview. This step is merged.
2. `likefudan/rh-mcp` implements and independently reviews the external
   default-deny Read Gateway, then publishes an immutable release or commit
   with a committed reviewed read manifest and full-manifest digest.
3. An ainvest tracker PR records that exact immutable version/commit and
   expected full-manifest digest. A design-only `rh-mcp` commit does not satisfy
   this step.
4. `P06-T0` composes a thin adapter over the pinned SDK-neutral gateway
   contract and verifies its version and manifest digest fail closed.
5. `P06-T1` normalizes portfolio, positions, buying power, order history/open
   orders, quotes, price books, and the other accepted read data into versioned
   ainvest schemas.
6. `P06-T2` exposes those normalized reads through an ainvest CLI/read-only
   entry point, Paper workflows, and a later Telegram read-query adapter under
   an independent Read Broker deployment identity; it cannot construct a
   broker write client.

| Task | Status | Dependencies / unlock | Integration note |
|---|---|---|---|
| `P08-T7` | `merged` ([#82](https://github.com/likefudan/ainvest/pull/82)) | `P01-T4`, `P01-T1` (satisfied) | Squash commit `00a274e2ab0d7fabfcf8e9cb7c0ef32f90292b1e` |
| external `rh-mcp` gateway | `blocked` (implementation/release pending) | Design correction merged at `366e7556cc765a0742fed7d6e17e0b9ec8e20aec`; implementation, independent review, immutable release/commit, and reviewed full-manifest digest remain required | This is a cross-repository prerequisite, not an ainvest task completion claim |
| `P06-T0` | `blocked` (unclaimed) | `P03-T13`, `P01-T4`, `P08-T7` satisfied; reviewed immutable `rh-mcp` release/commit and tracker-recorded full-manifest digest missing | Do not use the old implementation branch/worktree; coordinator recreates or rebases only after the external release is recorded |
| `P06-T1` | `not_started` (queued/unclaimed) | `P06-T0`, `P02-T1`–`P02-T3`, `P02-T6` | Claim only after `P06-T0` merges; create from/rebase onto that latest `main` |
| `P06-T2` | `not_started` (queued/unclaimed) | `P06-T0`, `P06-T1`, `P03-T16`, `P08-T0` | Claim only after `P06-T1` merges; create from/rebase onto that latest `main`; Paper execution only |

##### Execution envelope: P08-T7

- **Title:** Isolate Secrets, Identities, and Least-Privilege Access
- **Status/owner:** `merged` — `p08_t7_secrets_iam`
- **Branch/worktree:** `agent/p08-t7-secrets-iam` / `.worktrees/p08-t7`
- **Immutable base:** `61636dd04037911b203726811f91ccabeaa9ecc1`
  (includes the Robinhood priority-lane scheduling change in #80)
- **Dependencies:** `P01-T1` and `P01-T4`, both merged and satisfied on the
  immutable base
- **Design and task authority:** `design.md` sections 3.5–3.6, 5.7, 9, 11,
  and 12; `IMPLEMENTATION_TODO.md` sections 1, 11 (`P08-T7`), 12 (Batch E),
  and 16; `docs/security/{threat-model,data-flow}.md`; `DEC-003`,
  `DEC-009`, `DEC-010`, `DEC-015`, `DEC-017`, and `DEC-018`
- **Expected dependency artifacts:** the fail-closed configuration loader and
  file-secret precedence from `P01-T4`; the accepted trust boundaries and
  strategy secret-isolation requirements from `P01-T1`; the runtime capability
  separation from `P08-T0`; and redacted structured logging from `P08-T3`
- **Allowed paths:** `src/ainvest/secrets.py`,
  `tests/unit/test_secrets.py`, and `docs/security/secrets.md`;
  `.env.example` only for empty/non-secret development placeholders;
  `tests/unit/strategies/test_worker.py` only for a narrow assertion that the
  strategy worker cannot observe role credentials. Provider-neutral identity
  policy fixtures may be added under `tests/fixtures/identity/`. Any production
  configuration, runtime integration, package export, dependency, or deployment
  path requires coordinator-approved scope expansion recorded here first.
- **Required behavior:** define role-scoped secret references and a provider
  boundary for Research, Approval, Read Broker, and Write Broker; default-deny
  cross-role reads; keep development `.env` access explicit and uncommitted;
  fail closed when production secret-manager/workload-identity configuration is
  absent; check only secret presence and permission at startup; support
  reference-based rotation without logging, tracing, auditing, serializing, or
  returning secret values. Research alone may obtain the OpenAI reference, and
  Read Broker must never obtain a write-broker credential.
- **Forbidden scope:** no real credential, token, account identifier, secret
  value, cloud account, IAM principal, or secret-manager choice; no provider-
  specific production deployment while `DEC-015` is deferred; no Robinhood MCP
  session/tool implementation (`P06-T0`); no broker-write client or capability;
  no remote operator endpoint; no Telegram/WebAuthn behavior; no edits to
  `src/ainvest/config/**`, `src/ainvest/runtime.py`, execution/data adapters,
  schemas, database/migrations, CI, dependencies, or other task-owned paths
  without prior coordinator authorization
- **Owner values/credentials:** none are required for implementation or offline
  tests. Use deterministic fake references and fake providers only. Real
  Robinhood authorization, production identity/IAM, and secret-manager values
  remain external owner actions under `DEC-015`, `DEC-017`, and `DEC-018`;
  their absence must leave the corresponding capability disabled.
- **Verification contract:** add positive, boundary, cross-role-denial,
  missing/permission-denied, rotation, serialization/repr, log/audit redaction,
  and strategy-isolation tests; run the focused unit tests, then
  `./scripts/dev verify`; run `git diff --check`; inspect the complete scoped
  diff and repository-visible outputs for secret-like values and duplicated
  config/runtime abstractions
- **Review/integration contract:** the implementation agent commits but does
  not merge, and its worktree uses the immutable base above. After this claim
  PR is on `main` and before integration review, the implementation branch
  rebases onto the then-latest `main`, reruns the full verification contract,
  and updates its PR. A
  separate sub-agent reviews functionality, fail-closed security, tests,
  readability, and duplication directly on the PR; every actionable finding
  is fixed and re-reviewed before required checks pass and the PR is squash-
  merged. P08-T7 satisfied that contract in #82; P06-T0 is now blocked and
  unclaimed until the reviewed immutable external `rh-mcp` release and
  full-manifest digest are recorded.
- **Handoff/blockers:** the provider-neutral boundary is merged. Production
  deployment artifacts and real credential validation remain intentionally
  blocked on owner decisions; they did not block this fail-closed
  implementation.
- **PR:** [#82](https://github.com/likefudan/ainvest/pull/82)
- **Squash-merge commit:**
  `00a274e2ab0d7fabfcf8e9cb7c0ef32f90292b1e`

##### Execution envelope: P06-T0

- **Title:** Integrate the External Robinhood Read Gateway
- **Status/owner:** `blocked` — unclaimed; implementation must not start before
  the external dependency conditions below are recorded
- **Superseded claim:** the earlier `p06_t0_robinhood_read_gateway` claim and
  `agent/p06-t0-robinhood-read-gateway` / `.worktrees/p06-t0` execution
  envelope assumed ainvest-owned MCP v1 transport with an injected
  authenticated session. That claim is superseded by the external
  `likefudan/rh-mcp` gateway boundary. The old branch/worktree is not an active
  implementation surface and must not be reused. After the external release is
  recorded, the coordinator must recreate the worktree or explicitly rebase
  and issue a new immutable execution envelope.
- **Implementation base:** not assigned. The future immutable base must include
  this scope correction and the tracker commit that pins the reviewed `rh-mcp`
  release/commit plus expected full-manifest digest.
- **Dependencies:** `P03-T13`, `P01-T4`, and `P08-T7` are merged. P06-T0 also
  requires an independently reviewed immutable `rh-mcp` implementation
  release/commit, a committed reviewed read-tool manifest and full-manifest
  digest, and a subsequent ainvest tracker PR that records those exact values.
  The merged `rh-mcp` design correction
  `366e7556cc765a0742fed7d6e17e0b9ec8e20aec` defines direction but is not an
  implementation release and does not unblock P06-T0.
- **Design and task authority:** `design.md` sections 3.5, 5.1, 5.2, 5.6,
  10.1, and 11; `IMPLEMENTATION_TODO.md` sections 1, 9 (`P06-T0`), 12
  (Batch E/F priority lane), and 16; `docs/security/secrets.md` and
  `docs/architecture/dependency-direction.md`; `DEC-003`, `DEC-009`,
  `DEC-015`, and `DEC-018`
- **External gateway ownership:** `likefudan/rh-mcp` owns OAuth, DCR, PKCE,
  token refresh, the credential-store protocol, private MCP Python SDK v2
  transport, session lifecycle, bounded pagination/results, tool discovery,
  the committed reviewed read manifest and schema digests, default-deny
  enforcement, and a stable sanitized SDK-neutral result/error envelope. Its
  public contract must not expose arbitrary tool invocation, raw
  `CallToolResult`, MCP sessions, tokens, or provider SDK types.
- **ainvest ownership:** P06-T0 is a thin adapter and composition boundary. It
  pins an immutable `rh-mcp` release/commit and expected full-manifest digest,
  verifies both fail closed at startup and before accepting results, and maps
  only the stable gateway envelope into the input boundary owned by P06-T1.
  It never imports `mcp.*`, obtains or refreshes OAuth tokens, accepts a raw
  session, discovers arbitrary tools, or exposes `CallToolResult`. P06-T1 owns
  Robinhood-to-ainvest domain normalization. P06-T2 owns the normalized ainvest
  CLI/Paper-facing read surface, supports a later Telegram read-query adapter,
  and composes the gateway only under an independent Read Broker deployment
  identity.
- **Allowed paths:** a future execution envelope may assign the thin adapter,
  SDK-neutral cross-repository fixtures/tests, and narrow composition files
  only after the dependency is pinned. No production path is assigned while
  this task is blocked. Dependency or lock-file changes require their own
  explicit reviewed scope and must not install a second conflicting public MCP
  SDK surface in ainvest.
- **Required behavior:** accept only the pinned gateway contract and expected
  full-manifest digest; reject missing, unknown, mutable, or mismatched
  versions/digests before consuming any result. Validate bounded SDK-neutral
  result/error envelopes and produce stable sanitized errors. Log only approved
  metadata such as capability name, bounded duration, manifest/result digest,
  and status. Never expose token, credential, MCP/provider type, raw session,
  raw account payload, or arbitrary tool name to Research, Strategy, CLI,
  Paper, or Telegram, and never add an automatic data-provider fallback.
- **Forbidden scope:** no P06-T1 mapping or normalized domain schemas; no
  P06-T2 CLI, service composition, or real-portfolio Paper integration; no
  P06-T3/Gate 4 claim; no write client, mutating tool, live order, order
  submission/cancellation/replacement, or write credential; no unofficial
  Robinhood API; and no credential, config, runtime-mode, dependency,
  lock-file, shared-schema, database/migration, deployment, CI, or broad
  logging changes unless the coordinator first assigns and records them.
- **Owner-assisted verification:** implementation and deterministic tests may
  proceed later without owner values using only sanitized deterministic
  cross-repository fixtures. No agent may claim real authorization, official
  tool/schema evidence, account-scope evidence, or a usable manifest until the
  owner completes Robinhood authorization in an external browser. Tokens,
  refresh tokens, DCR client information, passwords, account IDs/numbers, and
  raw account payloads must never enter either repository, fixtures, logs,
  issues/PRs, or chat. If owner authorization or secure credential composition
  is unavailable, keep the real check explicitly unverified and fail closed.
- **Cross-repository completion conditions:** in order: (1) `rh-mcp`
  implementation and tests pass independent review; (2) it publishes an
  immutable release/commit with the reviewed manifest and full-manifest digest;
  (3) an ainvest tracker PR records those exact immutable values; (4) the
  coordinator creates a new P06-T0 execution envelope/worktree; (5) P06-T0
  adapter and cross-repository contracts merge; then and only then may P06-T1
  be claimed, followed serially by P06-T2.
- **Future verification contract:** focused offline contract tests must cover
  immutable version/full-manifest pinning, SDK-neutral envelope validation,
  bounded results, authentication/timeout/sanitized error handling,
  provider-object and secret non-disclosure, stable bounded logs, and no
  fallback; then run `git diff --check` and `./scripts/dev verify`. Independent
  PR review must cover functionality, fail-closed security, tests, readability,
  dependency direction, and duplication before squash merge.
- **Handoff/blockers:** blocked only on the reviewed immutable external
  implementation/release and its committed full-manifest digest. The design
  correction commit above is recorded for traceability but cannot be treated as
  runtime or schema evidence.

`P06-T0` through `P06-T2` form the **Read-only Preview**, not Gate 4. Gate 4
remains `P06-T3` and still requires Gate 2 (`P04-T12`), Gate 3 (`P05-T8`),
`P08-T4`, and every other dependency on its task card. No broker-write work may
start before Gates 1–4 and the later live prerequisites pass. Robinhood MCP
remains the only live quote source; failure never falls back to Alpaca,
yfinance, or another provider.

Per owner instruction on 2026-07-29, `P04-T2` and `P05-T4` are
`not_started`, unclaimed, and paused while the priority lane runs; no background
worktree or implementation agent should be started for either task. After
`P06-T2`, create a separate narrow
scheduling/task-card PR for Telegram read-only queries built on the Read
Gateway and the `P05-T4`/`P05-T5` transport; do not mix that read surface with
Telegram approval or any broker-write capability.

#### Research track — `P04-T0` through `P04-T12`

All provider tests use recorded fixtures or deterministic fakes; canonical
tests must not require public network access. Under `DEC-003`, development data
can never become a live quote fallback.

| Task | Status | Dependencies / unlock | Allowed implementation scope |
|---|---|---|---|
| `P04-T0` | `merged` | `P02-T1`, `P03-T13` | `data/{models,ports,fakes}.py`, data re-exports, `tests/unit/data/test_models.py`, `tests/contract/data/**`, architecture boundary test, `docs/data-adapters.md` |
| `P04-T1` | `not_started` | `P04-T0` | `data/providers/yahoo.py`; Yahoo fixtures/tests; offline-data dependency/config changes only when assigned |
| `P04-T2` | `not_started` (owner-paused/unclaimed) | `P04-T0` | `data/providers/sec.py`; filing/XBRL fixtures and tests; provider dependency/config changes only when assigned |
| `P04-T3` | `not_started` | `P04-T0`, `P04-T2`, `P03-T10` | `data/providers/news.py`, `data/calendar.py`; news/calendar fixtures and tests |
| `P04-T4` | `not_started` | `P04-T0`–`P04-T3`, `P02-T1` | `data/{indicators,quality,cache,snapshots}.py`; bounded persistence changes; unit/integration tests |
| `P04-T5` | `not_started` | `P04-T0`–`P04-T4`, `P02-T1`–`P02-T2` | `agents/tools/**`; read-only tool schemas, bounds, fakes, and tests |
| `P04-T6` | `not_started` | `P04-T5`, `DEC-004`; real calls also require `DEC-009` | `agents/research_agent.py`, `prompts/**`; fake-model tests; research dependency/config changes only when assigned |
| `P04-T7` | `not_started` | `P04-T5`, `P04-T6`, `P02-T6`–`P02-T8` | `agents/research_builder.py`; bounded research persistence changes; tests |
| `P04-T8` | `not_started` | `P04-T6`, `P04-T7` | `tests/evals/research/**`, `scripts/run_research_evals.py`, versioned evaluation fixtures/reports |
| `P04-T9` | `not_started` | `P03-T0`–`P03-T5`, `P03-T14`, `P04-T4` | `backtest/runner.py`; replay fixtures and tests |
| `P04-T10` | `not_started` | `P04-T9` | `backtest/{costs,validation}.py`; leakage/cost/walk-forward tests |
| `P04-T11` | `not_started` | `P04-T9`, `P04-T10` | `backtest/reporting.py`; deterministic report fixtures/tests; reporting dependency only when assigned |
| `P04-T12` | `not_started` | all `P04-T0`–`P04-T11` merged | `docs/releases/phase-2-acceptance.md`; Gate 2 harness/fixtures only |

After `P04-T4` merges, the Research Agent chain
`P04-T5` → `P04-T6` → `P04-T7` → `P04-T8` and the backtest chain
`P04-T9` → `P04-T10` → `P04-T11` may be implemented in parallel. They rejoin
at `P04-T12`.

#### Paper approval track — Gate 3

Telegram remains Paper-only under `DEC-005`; synthetic IDs and fake transports
are used until `DEC-010` is accepted and secrets are provisioned outside Git.

| Task | Status | Dependencies / unlock | Allowed implementation scope |
|---|---|---|---|
| `P05-T0` | `merged` | `P02-T3`, `P02-T4`, `P02-T6`–`P02-T9` | `approval/{service,tokens}.py`, approval re-exports, `schemas/approval.py`, `db/{repositories,uow}.py`, `tests/unit/approval/test_{approval_service,tokens}.py`; generated ApprovalChallenge schema + manifest only |
| `P05-T1` | `not_started` | `P05-T0`, `P01-T4`, `P02-T3`, `P02-T4` | `approval/telegram_approval.py`; callback validation, audit/outbox integration, tests |
| `P05-T4` | `not_started` (owner-paused/unclaimed) | `P05-T0`, `DEC-005`; environment integration requires `DEC-010` | `approval/telegram.py`; notification/config adapter, snapshots, fake-transport tests |
| `P05-T5` | `not_started` | `P05-T4`, `P01-T4` | `approval/telegram_updates.py`; poller offset/dedup persistence; bounded webhook interface; tests |
| `P05-T6` | `not_started` | `P05-T0`, `P05-T1`, `P02-T7`, `P02-T10`, `P03-T12` | `approval/handoff.py`; workflow/outbox integration; exactly-once and recovery tests |
| `P05-T8` | `not_started` | `P05-T0`, `P05-T1`, `P05-T4`–`P05-T6`, `P08-T6`, `P08-T7`, `P08-T13` | `docs/releases/phase-3-acceptance.md`; Gate 3 harness and security evidence |

`P05-T1` is dependency-ready but remains unclaimed. `P05-T4` is
owner-paused/unclaimed and may not start until the owner/coordinator explicitly
resumes it; only after that task merges may `P05-T5` start. `P05-T6` follows
`P05-T1`. The completed Paper approval path unlocks `P08-T13`, then `P05-T8`.

#### Deferred live approval track

This track does not block Gate 2 or Gate 3. Production acceptance is
fail-closed until the cited owner decisions are accepted. `P08-T14` is listed
here because it is on the live-approval dependency chain; its only canonical
task row is in the cross-cutting table below.

| Task | Status | Dependencies / owner gate | Allowed implementation scope |
|---|---|---|---|
| `P05-T7` | `not_started` | `P08-T6`, `DEC-015` | deployment manifest/IaC and `docs/runbooks/approval-deploy.md`; no guessed domain/provider |
| `P08-T14` | `not_started` | `P08-T7`; production endpoint also requires `DEC-018` | see cross-cutting row; local interfaces may be tested with fake identities |
| `P05-T2` | `not_started` | `P05-T0`, `P08-T14`, `DEC-015`, `DEC-016`, `DEC-018` | `approval/webauthn.py`, registration routes, one coordinated migration, security tests |
| `P05-T3` | `not_started` | `P05-T0`, `P05-T2`, `P05-T7`, `P02-T4` | `api/app.py`, approval routes/assets, `approval/assertion.py`, WebAuthn tests |

#### Cross-cutting foundation

| Task | Status | Dependencies / unlock | Allowed implementation scope |
|---|---|---|---|
| `P08-T0` | `merged` | `P01-T4`, `P03-T13` (satisfied) | `runtime.py`, `docs/runtime-modes.md`, `tests/unit/test_runtime.py` |
| `P08-T3` | `merged` | `P01-T2`, `P02-T8` (satisfied) | observability logging + unit test; Paper-flow correlation test/context hook; assigned dependency/setup files |
| `P08-T4` | `not_started` | `P08-T3` | `observability/{metrics,tracing,health}.py`; observability tests |
| `P08-T5` | `not_started` | `P08-T4`, `P02-T9` | `observability/alerts.py`, `docs/runbooks/incidents/**`, alert tests |
| `P08-T6` | `not_started` | `P01-T1` | `docs/security/control-matrix.md`; security tests and assigned CI scan changes |
| `P08-T7` | `merged` ([#82](https://github.com/likefudan/ainvest/pull/82)) | `P01-T4`, `P01-T1` | squash commit `00a274e2ab0d7fabfcf8e9cb7c0ef32f90292b1e`; handoff recorded above |
| `P08-T8` | `not_started` | `P01-T2`–`P01-T4`, `P03-T17` | `README.md`; safe Quickstart/Paper demo documentation only |
| `P08-T9` | `not_started` | `P03-T0`–`P03-T5` | `docs/strategy-plugin-guide.md`, starter template, external-package conformance test |
| `P08-T12` | `not_started` | incremental after each corresponding production card; not claimable as a broad umbrella | Coordinator-assigned, narrowly enumerated test files plus the matching `docs/testing.md` matrix rows only |
| `P08-T13` | `not_started` | `P02-T6`–`P02-T10`, `P03-T13`–`P03-T15`, `P05-T0`, `P05-T1`, `P05-T4`–`P05-T6` | `tests/{integration,faults}/**`; fake external services; test-only hooks coordinated |
| `P08-T14` | `not_started` | `P01-T1`, `P01-T4`, `P02-T8`, `P02-T10`, `P08-T7` | `admin/{auth,service}.py`, privileged API/CLI adapter, `docs/security/operator-access.md`, authorization/audit tests |

`P08-T0`, `P08-T3`, and `P08-T7` are merged. No P06 implementation is active;
`P06-T0` is blocked and unclaimed pending the reviewed immutable external
gateway release and recorded full-manifest digest. `P08-T6`, `P08-T8`, and
`P08-T9` are dependency-ready but remain unclaimed.
`P08-T12` is
scheduled incrementally after the production card whose test matrix it
extends; every claim must enumerate its exact test files and matching
`docs/testing.md` rows, and may not own an entire test directory. `P08-T4`
follows `P08-T3`; `P08-T5` follows `P08-T4`; `P08-T14` follows `P08-T7`;
`P08-T13` waits for the Paper approval implementation.

### Batch D — completed coordination note

Coordination note (closed): D4a → D4b ran serially (two PRs). D4 was
**integration only** — did not rewrite D1–D3 modules.

### Batch D — Part 1a (D1a)

- **Batch:** Batch D — Part 1a (D1a) — Strategy worker isolation (`P03-T4`)
- **Plan batch:** Batch D (D1)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-d1
- **Integration branch:** `task/batch-d1a-strategy-worker` (deleted after merge)
- **Base commit:** `b189f71a8feffe5255f7a653d398086f90934378` (includes claim #62)
- **Merge commit:** `861f5bf2db97b8eff6488af12e724e0c1b63cfb6`
- **Dependencies:** `P03-T0`–`T2`, `P02-T8` (satisfied on main)
- **Merge target:** merged into `main` via squash
- **Allowed paths:** `src/ainvest/strategies/worker/**`,
  `src/ainvest/strategies/__init__.py` (re-exports only),
  `tests/unit/strategies/**`, `tests/integration/strategies/**`,
  `docs/development.md` (worker isolation notes only),
  `docs/tasks/status.md` (D1a section + summary row + Active batch notes only),
  `pyproject.toml` (entry points / package data only if required)
- **Forbidden:** `strategy_conformance/` (D1b), portfolio aggregation (D2),
  pretrade/kill_switch (D2c), workflow (D3), live broker, rewriting registry
  load semantics beyond worker invocation hooks
- **Handoff notes:** Merged via #63. Isolated strategy workers with versioned JSON I/O,
  wall/CPU/memory limits, scrubbed env, socket block + documented container
  network expectations; fail-closed classification; one worker failure does not
  stop the batch. `./scripts/dev verify` passed (463 tests).
- **PR:** https://github.com/likefudan/ainvest/pull/63

| Task | Title | Status | Owner | Branch | Base | Dependencies | PR |
|---|---|---|---|---|---|---|---|
| `P03-T4` | Isolate Strategy Workers and Enforce Resource Boundaries | `merged` | cursor-subagent-d1 | `task/batch-d1a-strategy-worker` | `b189f71a8feffe5255f7a653d398086f90934378` | `P03-T0`–`T2`, `P02-T8` | https://github.com/likefudan/ainvest/pull/63 |

### Batch D — Part 1b (D1b)

- **Batch:** Batch D — Part 1b (D1b) — Strategy conformance suite (`P03-T5`)
- **Plan batch:** Batch D (D1)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-d1
- **Integration branch:** `task/batch-d1b-strategy-conformance` (deleted after merge)
- **Base commit:** `861f5bf` (D1a merged)
- **Merge commit:** `2dd98a4ac9325ba526788f8f6aafcf57e8654a30`
- **Dependencies:** `P03-T0`–`T4`, `P03-T13` (satisfied on main)
- **Merge target:** merged into `main` via squash
- **Allowed paths:** `src/ainvest/strategy_conformance/**`,
  CLI entry point / `pyproject.toml` scripts,
  `tests/unit/strategy_conformance/**`, `tests/integration/strategy_conformance/**`,
  `docs/strategy-conformance.md` (third-party CI example),
  `docs/tasks/status.md` (D1b section + summary row + Active batch notes only)
- **Forbidden:** rewriting worker internals beyond calling public APIs; D2/D3 scope;
  merging the PR
- **Handoff notes:** Merged via #64. Published `ainvest.strategy_conformance` +
  `ainvest-strategy-conformance` CLI; JSON + human reports; isolation via
  `evaluate_in_worker`. Reference MA passes; invalid plugins fail with stable
  codes. `./scripts/dev verify` passed (489 tests, 85.01% coverage).
- **PR:** https://github.com/likefudan/ainvest/pull/64

| Task | Title | Status | Owner | Branch | Base | Dependencies | PR |
|---|---|---|---|---|---|---|---|
| `P03-T5` | Publish the Strategy Conformance Test Suite | `merged` | cursor-subagent-d1 | `task/batch-d1b-strategy-conformance` | `861f5bf` | `P03-T0`–`T4`, `P03-T13` | https://github.com/likefudan/ainvest/pull/64 |

### Batch D — Part 2b (D2b)

- **Batch:** Batch D — Part 2b (D2b) — Multi-strategy signal aggregation (`P03-T7`)
- **Plan batch:** Batch D (D2)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-d2
- **Integration branch:** `task/batch-d2b-signal-aggregation` (deleted after merge)
- **Base commit:** `2dd98a4` (rebased onto post-D1 `main`)
- **Merge commit:** `3e12ecc182213dd84925da8073053b9cc54b6f0e`
- **Dependencies:** `P03-T6` (satisfied); D1a+D1b merged
- **Merge target:** merged into `main` via squash
- **Allowed paths:** `src/ainvest/portfolio/signal_aggregation.py`,
  `src/ainvest/portfolio/__init__.py`, `docs/decisions/**` (aggregation ADR),
  `tests/unit/portfolio/**`,
  `docs/tasks/status.md` (D2b section + summary row + Active batch notes only)
- **Forbidden:** pretrade/kill_switch (D2c), strategy worker (D1), workflow (D3),
  rewriting sizer behavior beyond calling it if needed
- **Handoff notes:** Merged via #65. ADR-020 / `DEC-020` accepted: conflict → `NEEDS_REVIEW` /
  no trade; group by symbol + `generated_at` + expiry + strategy version;
  never emit opposing orders for one symbol; strength never weighted as
  probability. API: `aggregate_signals` / `SignalAggregationResult`.
  Rebased onto `2dd98a4`; `./scripts/dev verify` passed (518 tests).
- **PR:** https://github.com/likefudan/ainvest/pull/65

| Task | Title | Status | Owner | Branch | Base | Dependencies | PR |
|---|---|---|---|---|---|---|---|
| `P03-T7` | Define Multi-Strategy Signal Aggregation | `merged` | cursor-subagent-d2 | `task/batch-d2b-signal-aggregation` | `2dd98a4` | `P03-T6` | https://github.com/likefudan/ainvest/pull/65 |

### Batch D — Part 2c (D2c)

- **Batch:** Batch D — Part 2c (D2c) — Pre-trade risk re-evaluation (`P03-T12`)
- **Plan batch:** Batch D (D2)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-d2
- **Integration branch:** `task/batch-d2c-pretrade-risk` (deleted after merge)
- **Base commit:** `3e12ecc` (rebased onto post-D2b `main`)
- **Merge commit:** `27631f272101f75a46c487e8b3be7f0ec7b992bc`
- **Dependencies:** `P03-T8`–`T11`, `P02-T7`, `P02-T9` (satisfied); D2b merged
- **Merge target:** merged into `main` via squash
- **Allowed paths:** `src/ainvest/risk/rules/orders.py`, `src/ainvest/risk/kill_switch.py`,
  `src/ainvest/risk/pretrade.py`, `src/ainvest/risk/__init__.py`,
  `src/ainvest/risk/rules/__init__.py`, `src/ainvest/risk/engine.py`,
  `src/ainvest/risk/models.py` (context/config fields), `tests/unit/risk/**`,
  `docs/tasks/status.md` (D2c section + summary row only)
- **Forbidden:** strategy worker (D1), signal aggregation (D2b), workflow (D3), live broker,
  auto-cancel of open orders (DEC-008)
- **Handoff notes:** Merged via #67. `KillSwitch` (configured+operational, alert, no auto-cancel);
  order rules for proposal-hash / client-order-id / symbol-side window / open-order
  conflicts; `evaluate_pretrade` re-fetches quote+portfolio and runs
  `PRETRADE_RULE_CODES` with a new decision id (never reuses prior APPROVED).
  Rebased onto `3e12ecc`; `./scripts/dev verify` passed (532 tests).
- **PR:** https://github.com/likefudan/ainvest/pull/67

| Task | Title | Status | Owner | Branch | Base | Dependencies | PR |
|---|---|---|---|---|---|---|---|
| `P03-T12` | Prevent Duplicate Orders and Re-run Risk Before Execution | `merged` | cursor-subagent-d2 | `task/batch-d2c-pretrade-risk` | `3e12ecc` | `P03-T8`–`T11`, `P02-T7`, `P02-T9` | https://github.com/likefudan/ainvest/pull/67 |

### Batch D — Part 3b (D3b)

- **Batch:** Batch D — Part 3b (D3b) — Domain commands/events (`P02-T10`)
- **Plan batch:** Batch D (D3)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-d3
- **Integration branch:** `task/batch-d3b-workflow-commands` (deleted after merge)
- **Handoff PR:** [#68](https://github.com/likefudan/ainvest/pull/68)
- **Base commit:** `27631f272101f75a46c487e8b3be7f0ec7b992bc` (rebased onto post-D2 `main`)
- **Merge commit:** `efec55b5e5f6c5b53dbf87475afa5353981f917a`
- **Dependencies:** `P02-T9`, `P02-T8`, Batch D2 (`P03-T7`/`T12`) satisfied on main
- **Merge target:** merged into `main` via squash
- **Allowed paths:** `src/ainvest/workflow/**`,
  `tests/unit/workflow/**`, `tests/contract/workflow/**` (if needed),
  `docs/tasks/status.md` (D3b section + summary row + Active batch notes only),
  `pyproject.toml` (package discovery only if required),
  `docs/architecture/dependency-direction.md` + architecture tests (register `workflow`)
- **Forbidden:** reconciliation/ledger (D3c), rewriting D1/D2, live broker,
  Temporal/durable queue implementation (interface only)
- **Handoff notes:** Merged via #68. `./scripts/dev verify` green (570 passed).
  `ainvest.workflow` commands/events with correlation/causation/idempotency;
  in-process dispatcher + IdempotencyStore; PURE / READ_ONLY_EXTERNAL / BROKER_WRITE.
  Architecture matrix registers `workflow`.

| Task | Title | Status | Owner | Branch | Base | Dependencies | PR |
|---|---|---|---|---|---|---|---|
| `P02-T10` | Define Domain Commands, Events, and Correlation IDs | `merged` | cursor-subagent-d3 | `task/batch-d3b-workflow-commands` | `27631f272101f75a46c487e8b3be7f0ec7b992bc` | `P02-T9`, `P02-T8` | [#68](https://github.com/likefudan/ainvest/pull/68) |

### Batch D — Part 3c (D3c)

- **Batch:** Batch D — Part 3c (D3c) — Reconciliation + portfolio ledger (`P03-T15`)
- **Plan batch:** Batch D (D3)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-d3
- **Integration branch:** `task/batch-d3c-reconciliation-ledger` (deleted after merge)
- **Handoff PR:** [#69](https://github.com/likefudan/ainvest/pull/69)
- **Base commit:** `efec55b5e5f6c5b53dbf87475afa5353981f917a`
- **Merge commit:** `da248c2f67ebed8da9c31ed87b3ded062d085031`
- **Dependencies:** `P03-T14`, `P02-T6`–`T8`, `P02-T10` (satisfied on main)
- **Merge target:** `main` (squash)
- **Allowed paths:** `src/ainvest/execution/reconciliation.py`,
  `src/ainvest/portfolio/ledger.py`,
  `src/ainvest/execution/__init__.py` / `portfolio/__init__.py` (re-exports),
  `tests/unit/execution/**`, `tests/unit/portfolio/**` (ledger/reconcile),
  `docs/tasks/status.md` (D3c section + summary row only)
- **Forbidden:** rewriting T10 / D1 / D2 paths beyond type imports
- **Handoff notes:** Merged via #69. `PortfolioLedger` with idempotent fills +
  conservation; `OrderReconciler` compares client IDs/qty/price/state and routes
  discrepancies to MANUAL_REVIEW with alerts (never silent money rewrite).
  Atomic fill batches rewrite in-batch `DUPLICATE` results to `BATCH_ROLLED_BACK`
  on rollback.

| Task | Title | Status | Owner | Branch | Base | Dependencies | PR |
|---|---|---|---|---|---|---|---|
| `P03-T15` | Reconcile Paper Orders and Maintain the Portfolio Ledger | `merged` | cursor-subagent-d3 | `task/batch-d3c-reconciliation-ledger` | `efec55b5e5f6c5b53dbf87475afa5353981f917a` | `P03-T14`, `P02-T6`–`T8` | [#69](https://github.com/likefudan/ainvest/pull/69) |

### Batch D — Part 4a (D4a)

- **Batch:** Batch D — Part 4a (D4a) — Paper orchestration (`P03-T16`)
- **Plan batch:** Batch D (D4)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-d4a
- **Integration branch:** `task/batch-d4a-paper-orchestration` (deleted after merge)
- **Handoff PR:** [#70](https://github.com/likefudan/ainvest/pull/70)
- **Base commit:** `da248c2f67ebed8da9c31ed87b3ded062d085031` (post-D3c main)
- **Merge commit:** `36f51ebb795e04f64f91a4983bc65cdb953d86d0`
- **Dependencies:** `P03-T0`–`T15`, `P02-T10` (satisfied on main); D1–D3 merged
- **Merge target:** `main` (squash); **first** of D4a→D4b
- **Allowed paths:**
  - `src/ainvest/orchestrator.py` and/or `src/ainvest/orchestrator/**`
  - CLI entry (`pyproject.toml` scripts + thin CLI module under orchestrator)
  - `tests/integration/test_paper_flow.py` and fixtures under
    `tests/fixtures/paper/` or `tests/integration/paper/`
  - `docs/tasks/status.md` (D4a section + summary row + Active batch notes only)
- **Forbidden:** rewriting D1–D3 modules beyond imports/re-exports; Telegram;
  live broker; `docs/releases/` (D4b / P03-T17); auto-approve paths
- **Handoff notes:** Merged via #70. Composition root `ainvest.orchestrator`
  wires worker → aggregate → sizer → risk → hash-bound proposal → explicit
  approval stub → pre-trade → dispatcher `ExecuteOrder`/`Reconcile` → Paper
  fill → reconciler + ledger conservation. Never auto-approves.

| Task | Title | Status | Owner | Branch | Base | Dependencies | PR |
|---|---|---|---|---|---|---|---|
| `P03-T16` | Orchestrate a Full Paper Flow from a Fixed ResearchPacket | `merged` | cursor-subagent-d4a | `task/batch-d4a-paper-orchestration` | `da248c2f67ebed8da9c31ed87b3ded062d085031` | `P03-T0`–`T15`, `P02-T10` | [#70](https://github.com/likefudan/ainvest/pull/70) |

### Batch D — Part 4b (D4b)

- **Batch:** Batch D — Part 4b (D4b) — Gate 1 acceptance (`P03-T17`)
- **Plan batch:** Batch D (D4)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-d4b
- **Integration branch:** `task/batch-d4b-gate-1-acceptance` (deleted after merge)
- **Handoff PR:** [#71](https://github.com/likefudan/ainvest/pull/71)
- **Base commit:** `36f51ebb795e04f64f91a4983bc65cdb953d86d0` (D4a merge)
- **Merge commit:** `41976373b64c72c231fd76ac7be809659ac1902a`
- **Dependencies:** `P03-T16` + Phase 01–03 cards (all prior)
- **Merge target:** `main` (squash); **second** of D4a→D4b
- **Allowed paths:**
  - `docs/releases/phase-1-acceptance.md`
  - optional Gate-1 harness under `scripts/` or CLI flag
  - `docs/tasks/status.md` (D4b + Batch D complete row)
- **Forbidden:** rewriting orchestration beyond acceptance harness; new features;
  Telegram / live broker
- **Handoff notes:** Gate 1 acceptance record produced from empty SQLite →
  migrate → fixed ResearchPacket → simulated fill → audit timeline export;
  worker isolation, risk fail-closed, Paper idempotency, illegal SM rejection
  verified; performance baseline recorded; high/critical defects = 0.
  See `docs/releases/phase-1-acceptance.md`. **Batch D complete.**

| Task | Title | Status | Owner | Branch | Base | Dependencies | PR |
|---|---|---|---|---|---|---|---|
| `P03-T17` | Gate 1: Accept the Deterministic Simulated Trading Loop | `merged` | cursor-subagent-d4b | `task/batch-d4b-gate-1-acceptance` | `36f51ebb795e04f64f91a4983bc65cdb953d86d0` | `P03-T16` + P01–P03 | [#71](https://github.com/likefudan/ainvest/pull/71) |

### Batch C — Part 4b (C4b)

- **Batch:** Batch C — Part 4b (C4b) — Exposure rules (`P03-T9`)
- **Plan batch:** Batch C (after C4a + D2a)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-c4b
- **Integration branch:** `task/batch-c4b-exposure-rules` (deleted after merge)
- **Handoff PR:** [#46](https://github.com/likefudan/ainvest/pull/46)
- **Base commit:** `f3641ddd9d0912a60a371e0558be3a6a23ee7203`
- **Dependencies:** `P03-T8`, `P03-T6` (satisfied on main)
- **Merge target:** `main` (squash)
- **Allowed paths:** `src/ainvest/risk/rules/exposure.py`,
  `src/ainvest/risk/models.py` (ExposureLimits + context fields only),
  `src/ainvest/risk/engine.py` (wire exposure rules only),
  `src/ainvest/risk/rules/__init__.py`, `src/ainvest/risk/__init__.py`,
  `tests/unit/risk/test_exposure.py`,
  `docs/tasks/status.md` (C4b section + summary row + Active batch notes only)
- **Forbidden:** pretrade/kill_switch (P03-T12), live broker, rewriting C4a rules
- **Handoff notes:** Implementing max notional, symbol weight, sector, daily
  turnover, min cash reserve, daily P&L limits on projected post-trade state.

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P03-T9` | Implement Notional, Position, Sector, and Cash Rules | `merged` | cursor-subagent-c4b | `task/batch-c4b-exposure-rules` | `f3641ddd9d0912a60a371e0558be3a6a23ee7203` | `P03-T8`, `P03-T6` | [#46](https://github.com/likefudan/ainvest/pull/46) |

### Batch C — Part 4a (C4a)

- **Batch:** Batch C — Part 4a (C4a) — Risk rule framework + eligibility +
  market quality (`P03-T8`, `P03-T10`, `P03-T11`)
- **Plan batch:** Batch C (after C1)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-c4a
- **Integration branch:** `task/batch-c4a-risk-framework` (deleted after merge)
- **Handoff PR:** [#45](https://github.com/likefudan/ainvest/pull/45)
- **Base commit:** `380aedfad5bb67f34878b49273faa16365ffc58a`
- **Dependencies:** `P02-T3`, `P02-T8`, `P02-T1` (satisfied on main)
- **Merge target:** `main` (squash)
- **Allowed paths:** `src/ainvest/risk/{engine,models}.py`,
  `src/ainvest/risk/rules/**`, `src/ainvest/risk/__init__.py`,
  `src/ainvest/data/calendar_port.py`, `src/ainvest/data/__init__.py`,
  `tests/unit/risk/**`, `tests/unit/data/**`,
  `docs/tasks/status.md` (C4a section + summary row + Active batch notes only)
- **Forbidden:** `exposure.py` (C4b), pretrade/kill_switch (P03-T12), live broker
- **Verification:** `./scripts/dev verify` and `./scripts/dev audit` passed
- **Handoff notes:** Composable risk engine with order-independent aggregation;
  eligibility (allowlist, identity, session via FakeMarketCalendar); market
  quality with separate proposal/pretrade thresholds; fail-closed on unknown
  rules and exceptions. Digests on `RiskEngineOutput`.

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P03-T8` | Build the Risk Rule Framework and Decision Aggregator | `merged` | cursor-subagent-c4a | `task/batch-c4a-risk-framework` | `380aedfad5bb67f34878b49273faa16365ffc58a` | `P02-T3`, `P02-T8` | [#45](https://github.com/likefudan/ainvest/pull/45) |
| `P03-T10` | Enforce Asset Eligibility, Allowlist, Side, and Trading Session | `merged` | cursor-subagent-c4a | `task/batch-c4a-risk-framework` | `380aedfad5bb67f34878b49273faa16365ffc58a` | `P03-T8` | [#45](https://github.com/likefudan/ainvest/pull/45) |
| `P03-T11` | Enforce Quote Freshness, Spread, Volatility, and Slippage | `merged` | cursor-subagent-c4a | `task/batch-c4a-risk-framework` | `380aedfad5bb67f34878b49273faa16365ffc58a` | `P03-T8`, `P02-T1` | [#45](https://github.com/likefudan/ainvest/pull/45) |

### Batch C — Part 3b (C3b)

- **Batch:** Batch C — Part 3b (C3b) — Deterministic Paper Broker and fill
  simulator (`P03-T14`)
- **Plan batch:** Batch C (after C1 + D3a)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-c3b
- **Integration branch:** `task/batch-c3b-paper-broker` (deleted after merge)
- **Base commit:** `c60586c1ee1c76b49cd08884314c7957bcd031b2`
- **Dependencies:** `P03-T13`, `P02-T9`, `P02-T6`–`P02-T8` (satisfied on main)
- **Merge target:** `main` (squash)
- **Handoff PR:** [#44](https://github.com/likefudan/ainvest/pull/44)
- **Allowed paths:** `src/ainvest/execution/paper.py`,
  `src/ainvest/execution/__init__.py`, `tests/unit/execution/**`,
  `tests/contract/execution/**` (only if needed for Paper adapter),
  `docs/tasks/status.md` (C3b section + summary row + Active batch notes only)
- **Forbidden:** Robinhood MCP, live trading, risk engine rewrite, workflow
  (`P02-T10`), reconciliation (`P03-T15`)
- **Verification:** `./scripts/dev verify` and `./scripts/dev audit` passed
  locally; CI Verify / Secret scan / Dependency audit green on #44
- **Handoff notes:** `PaperBroker` implements cash/positions, submit/cancel,
  partial/full fill from injected market events only; injected clock/RNG;
  explicit `PaperCostModel` (fee/half-spread/slippage); idempotent submit by
  `client_order_id`; read/write port views; no oversell/overdraft. Review fixed
  fill timestamps when clock ahead of event and open-order-only cash reserves.
- **Next after merge:** C4a (`P03-T8`/`T10`/`T11`) may start; C4b waits for C4a

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P03-T14` | Build the Deterministic Paper Broker and Fill Simulator | `merged` | cursor-subagent-c3b | `task/batch-c3b-paper-broker` | `c60586c1ee1c76b49cd08884314c7957bcd031b2` | `P03-T13`, `P02-T9`, `P02-T6`–`P02-T8` | [#44](https://github.com/likefudan/ainvest/pull/44) |

### Batch D — Part 2a (D2a)

- **Batch:** Batch D — Part 2a (D2a) — Single-strategy Position Sizer (`P03-T6`)
- **Plan batch:** Batch D (early unlock for C4b)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-d2a
- **Integration branch:** `task/batch-d2a-position-sizer` (deleted after merge)
- **Base commit:** `00b772a2c4c4af84c3e317e8bd0a2f237515364e`
- **Merge commit:** `33292d775ec9b2e3d6638c88dce851a6f64dde4a`
- **Follow-up:** [#41](https://github.com/likefudan/ainvest/pull/41) (`32660fb`)
  — SELL when BP=0; open-order reserves
- **Dependencies:** `P02-T2`, `P02-T3`, `P01-T4` (satisfied on main)
- **Merge target:** `main` (squash)
- **Handoff PR:** [#40](https://github.com/likefudan/ainvest/pull/40)
- **Handoff notes:** `size_position` converts target-weight `TradeSignal` +
  quote + portfolio + `SizingConfig` into a whole-share `CandidateOrder` or a
  stable no-trade reason. Unlocks C4b after C4a.

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P03-T6` | Implement the Single-Strategy Position Sizer | `merged` | cursor-subagent-d2a | `task/batch-d2a-position-sizer` | `00b772a2c4c4af84c3e317e8bd0a2f237515364e` | `P02-T2`, `P02-T3`, `P01-T4` | [#40](https://github.com/likefudan/ainvest/pull/40), [#41](https://github.com/likefudan/ainvest/pull/41) |

### Batch D — Part 3a (D3a)

- **Batch:** Batch D — Part 3a (D3a) — Order state machine and illegal-transition
  guards (`P02-T9`)
- **Plan batch:** Batch D (early unlock for C3b)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-agent
- **Integration branch:** `task/batch-d3a-order-state-machine` (deleted after merge)
- **Base commit:** `32660fbb06b71c20fef54489182b4dc1ef7fa35e`
- **Merge commit:** `3de469dc8f61814d6b3cbc6f501c83a06dcb1be3`
- **Dependencies:** `P02-T3`, `P02-T7`, `P02-T8` (satisfied on main)
- **Merge target:** `main` (squash)
- **Handoff PR:** [#42](https://github.com/likefudan/ainvest/pull/42)
- **Handoff notes:** Order + cancel graphs match design.md §8; CAS;
  recovery-only unknown paths; duplicate event_id idempotent; commit-then-seen
  persistence with required atomic wrapper for audit-backed path. Review fixed
  non-atomic audit apply and unbound same-state event ids before merge.
- **Next after merge:** C3b (`P03-T14`) may start

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P02-T9` | Implement the Order State Machine and Illegal-Transition Guards | `merged` | cursor-agent | `task/batch-d3a-order-state-machine` | `32660fbb06b71c20fef54489182b4dc1ef7fa35e` | `P02-T3`, `P02-T7`, `P02-T8` | [#42](https://github.com/likefudan/ainvest/pull/42) |

### Batch C — Part 1 (C1)

- **Batch:** Batch C — Part 1 (C1) — SQLAlchemy models, Alembic, repositories,
  UoW, append-only audit (`P02-T6`, `P02-T7`, `P02-T8`)
- **Plan batch:** Batch C (critical path)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-c1
- **Integration branch:** `task/batch-c1-db-audit` (deleted after merge)
- **Base commit:** `b2c30cba584efd9d21361197ce0e97c7d042cdf6`
- **Merge commit:** `6732046f1d610d32bfa4d9a2a5e3b53022df3b03`
- **Dependencies:** Batch B complete (`P02-T0`–`P02-T5`)
- **Merge target:** `main` (squash)
- **Handoff PR:** [#37](https://github.com/likefudan/ainvest/pull/37)
- **Handoff notes:** Implemented `ainvest.db` (models, DecimalString/UtcDateTime,
  repositories, UoW) and `ainvest.audit` (envelope, recursive redaction, digests,
  append-only service). Initial Alembic revision `ec71aaa3381a`. Residual:
  repository branch coverage is partial beyond exercised paths; cancel/operator
  rows are modeled but not yet repository-wrapped.
- **Next after merge:** C4a (`P03-T8`/`T10`/`T11`) may start; C3b still needs
  `P02-T9`

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P02-T6` | Create SQLAlchemy Models and the Initial Alembic Migration | `merged` | cursor-subagent-c1 | `task/batch-c1-db-audit` | `b2c30cba584efd9d21361197ce0e97c7d042cdf6` | `P02-T0`–`P02-T3`, `P01-T4` | [#37](https://github.com/likefudan/ainvest/pull/37) |
| `P02-T7` | Implement Repositories, Unit of Work, and Concurrency Control | `merged` | cursor-subagent-c1 | `task/batch-c1-db-audit` | `b2c30cba584efd9d21361197ce0e97c7d042cdf6` | `P02-T6` | [#37](https://github.com/likefudan/ainvest/pull/37) |
| `P02-T8` | Implement Append-Only Audit Events and Redaction | `merged` | cursor-subagent-c1 | `task/batch-c1-db-audit` | `b2c30cba584efd9d21361197ce0e97c7d042cdf6` | `P02-T6`, `P02-T7` | [#37](https://github.com/likefudan/ainvest/pull/37) |

### Batch C — Part 2 (C2)

- **Batch:** Batch C — Part 2 (C2) — Strategy API, registry, YAML instances,
  reference MA plugin (`P03-T0`–`P03-T3`)
- **Plan batch:** Batch C (parallel with C1/C3a)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-c2
- **Integration branch:** `task/batch-c2-strategy-api` (deleted after merge)
- **Base commit:** `b2c30cba584efd9d21361197ce0e97c7d042cdf6`
- **Merge commit:** `c1fa3106e828df0c47fd27d168c7ebd5a4445b06`
- **Dependencies:** `P02-T2`, `P02-T5`, `P01-T3`, `P01-T4` (satisfied on main)
- **Merge target:** `main` (squash)
- **Handoff PR:** https://github.com/likefudan/ainvest/pull/38
- **Next after merge:** Feeds later worker isolation (P03-T4) and Gate 1

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P03-T0` | Define the Strategy API, Definitions, and Hook Contract | `merged` | cursor-subagent-c2 | `task/batch-c2-strategy-api` | `b2c30cba584efd9d21361197ce0e97c7d042cdf6` | `P02-T2`, `P02-T5`, `P01-T3` | https://github.com/likefudan/ainvest/pull/38 |
| `P03-T1` | Load pluggy Plugins into StrategyRegistry | `merged` | cursor-subagent-c2 | `task/batch-c2-strategy-api` | `b2c30cba584efd9d21361197ce0e97c7d042cdf6` | `P03-T0` | https://github.com/likefudan/ainvest/pull/38 |
| `P03-T2` | Implement Strategy Instance YAML Configuration | `merged` | cursor-subagent-c2 | `task/batch-c2-strategy-api` | `b2c30cba584efd9d21361197ce0e97c7d042cdf6` | `P03-T0`, `P03-T1`, `P01-T4` | https://github.com/likefudan/ainvest/pull/38 |
| `P03-T3` | Build a Reference Moving-Average Strategy Plugin | `merged` | cursor-subagent-c2 | `task/batch-c2-strategy-api` | `b2c30cba584efd9d21361197ce0e97c7d042cdf6` | `P03-T0`–`T2`, `P02-T1`–`T2` | https://github.com/likefudan/ainvest/pull/38 |

### Batch C — Part 3a (C3a)

- **Batch:** Batch C — Part 3a (C3a) — Broker read/write port and error
  taxonomy (`P03-T13` only)
- **Plan batch:** Batch C (parallel with C1/C2)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Owner/agent:** cursor-subagent-c3a
- **Integration branch:** `task/batch-c3a-broker-port` (deleted after merge)
- **Base commit:** `55c6660a23ca2b8da1ecef73f7c5a3c55f185693`
- **Merge commit:** `14049789847f5e278a1eadeab6dfb4f53088cd76`
- **Dependencies:** `P02-T3` (satisfied on main)
- **Merge target:** `main` (squash)
- **Handoff PR:** [#36](https://github.com/likefudan/ainvest/pull/36)
- **Handoff notes:** `BrokerReadPort` / `BrokerWritePort`, error taxonomy, and
  DEC-007 no-replace contract. Submit binds `ApprovalScope` to proposal
  `account_scope`. Residual: Paper adapter is C3b (`P03-T14`).
- **Next after merge:** C3b Paper waits for Batch D `P02-T9`; C4a may start

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P03-T13` | Define the Broker Port and Error Taxonomy | `merged` | cursor-subagent-c3a | `task/batch-c3a-broker-port` | `55c6660a23ca2b8da1ecef73f7c5a3c55f185693` | `P02-T3` | [#36](https://github.com/likefudan/ainvest/pull/36) |

## Completed batches

### Batch B — Part 4 (B4)

- **Batch:** Batch B — Part 4 (B4) — schema versioning, JSON Schema export,
  fixtures, and contract/CI breaking-change detection (`P02-T5`)
- **Plan batch:** Batch B (final part; Batch B complete)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Integration:** [PR #27](https://github.com/likefudan/ainvest/pull/27),
  follow-up [PR #30](https://github.com/likefudan/ainvest/pull/30),
  squash merged into `main`
- **Integration branch:** `task/batch-b4-schema-versioning` (deleted after
  squash merge)
- **Base commit:** `c843aa198deb4fe362957ae01780b53450aae8ea`
- **Implementation commit:** `e7f266d17b8bd1ad980a82c7978b73bcefb2b5b2`
- **Follow-up commit:** `f89842205b1a98dcc06bbcd1f2761d660373bee3`
- **Dependency PRs/commits:** Batch B3 /
  [PR #25](https://github.com/likefudan/ainvest/pull/25) /
  [PR #26](https://github.com/likefudan/ainvest/pull/26)
- **Merge target:** `main` (squash)
- **Allowed paths:** `docs/schema-versioning.md`, `schemas/json/**`,
  `src/ainvest/schemas/export.py`, `src/ainvest/schemas/examples.py`,
  `src/ainvest/schemas/__init__.py`, `src/ainvest/strategies/**`,
  `tests/contract/**`, `tests/unit/strategies/**`, `tests/unit/schemas/test_examples.py`,
  `scripts/**`, `docs/tasks/status.md`, `README.md`, `docs/development.md`
- **Safety posture:** documentation, JSON Schema artifacts, contract tests, and
  Strategy API version helpers only; Paper remains the default; no broker write
  capability, credentials, AI calls, Telegram runtime, or live trading
- **Verification:** final `./scripts/dev verify` passed (232 tests; 84.89%
  coverage) after #30; all required PR checks passed for #27 and #30
- **Handoff notes:** Added `docs/schema-versioning.md` (forbid-aligned major/minor
  rules; cumulative minor version policy), exported 20 core models to
  `schemas/json/v1/`, dual Pydantic + Draft 2020-12 JSON Schema fixture checks
  (including RFC 3339 date-time format), OrderProposal hash binding, Strategy
  API range helpers, and `./scripts/dev export-schemas --check` for schema and
  fixture drift. Follow-up #30 pinned `schema_version` to exact `1.0` and made
  UTC timezone/format enforcement match across both validators.
- **Next:** Batch C (`C1`/`C2` preferred first claims)

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P02-T5` | Establish Schema Versioning and Compatibility Rules | `merged` | cursor-agent | `task/batch-b4-schema-versioning` (deleted) | `c843aa198deb4fe362957ae01780b53450aae8ea` | `P02-T0`–`P02-T4` | [#27](https://github.com/likefudan/ainvest/pull/27), [#30](https://github.com/likefudan/ainvest/pull/30) |

### Batch B — Part 3 (B3)

- **Batch:** Batch B — Part 3 (B3) — order/risk/approval/broker schemas and
  canonical order hashing (`P02-T3`, `P02-T4`)
- **Plan batch:** Batch B (partial; remaining part B4)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Integration:** [PR #25](https://github.com/likefudan/ainvest/pull/25),
  squash merged into `main`
- **Integration branch:** `task/batch-b3-order-schemas` (deleted after squash
  merge)
- **Base commit:** `c6fedb8cf4011ff305ff40f8503c44e94def9996`
- **Implementation commit:** `0a12c80b3beb4ae859bb27a399b611bd26151028`
- **Dependency PRs/commits:** Batch B2 /
  [PR #23](https://github.com/likefudan/ainvest/pull/23) /
  [PR #24](https://github.com/likefudan/ainvest/pull/24)
- **Merge target:** `main` (squash)
- **Allowed paths:** `src/ainvest/schemas/orders.py`,
  `src/ainvest/schemas/risk.py`, `src/ainvest/schemas/approval.py`,
  `src/ainvest/schemas/broker.py`, `src/ainvest/schemas/common.py`,
  `src/ainvest/schemas/__init__.py`, `src/ainvest/approval/`,
  `tests/unit/schemas/**`, `tests/unit/approval/**`, `docs/tasks/status.md`,
  `README.md`
- **Safety posture:** domain schemas, hash helpers, and unit tests only; Paper
  remains the default; no broker write capability, credentials, AI calls,
  Telegram runtime, or live trading are introduced
- **Verification:** final `./scripts/dev verify` passed (161 tests; 84.81%
  coverage); `./scripts/dev audit` found no known vulnerabilities; all required
  PR checks passed
- **Handoff notes:** Added money-moving schemas (`CandidateOrder`,
  `OrderProposal`, risk/approval/broker/cancel types) with telegram+paper and
  webauthn+live enforcement, plus canonical `sha256:` order/cancel digests and
  fixed vectors. Extended the shared Decimal contract in
  `src/ainvest/schemas/common.py` (reject scientific notation; bound
  significand/exponent/rendered length after trailing-zero canonicalization,
  including collapsing every zero encoding to ``Decimal(0)``; keep raw input
  maxLength) so hashing and exact order checks cannot amplify memory/CPU.
  `./scripts/dev verify` passed (161 tests; 84.81% coverage);
  `./scripts/dev audit` clean.
- **Next:** Batch B — Part 4 (B4) — `P02-T5`

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P02-T3` | Define Candidate Order, Risk, Approval, and Broker Schemas | `merged` | cursor-agent | `task/batch-b3-order-schemas` (deleted) | `c6fedb8cf4011ff305ff40f8503c44e94def9996` | `P02-T0`, `P02-T2` | [#25](https://github.com/likefudan/ainvest/pull/25) |
| `P02-T4` | Implement Canonical Order Serialization and Hashing | `merged` | cursor-agent | `task/batch-b3-order-schemas` (deleted) | `c6fedb8cf4011ff305ff40f8503c44e94def9996` | `P02-T3` | [#25](https://github.com/likefudan/ainvest/pull/25) |

### Batch B — Part 2 (B2)

- **Batch:** Batch B — Part 2 (B2) — portfolio, strategy-context, and
  trade-signal schemas (`P02-T2`)
- **Plan batch:** Batch B (partial; remaining parts B3–B4)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Integration:** [PR #23](https://github.com/likefudan/ainvest/pull/23),
  squash merged into `main`
- **Integration branch:** `task/batch-b2-strategy-schemas` (deleted after
  squash merge)
- **Base commit:** `6eec043e1a6101ac17f7bfa6fa4fae51ef78ae5d`
- **Implementation commit:** `ff8e5612703fb036a9cf7d3c8fb15682b4d3a0db`
- **Dependency PRs/commits:** Batch B1 /
  [PR #21](https://github.com/likefudan/ainvest/pull/21) /
  [PR #22](https://github.com/likefudan/ainvest/pull/22)
- **Merge target:** `main` (squash)
- **Allowed paths:** `src/ainvest/schemas/portfolio.py`,
  `src/ainvest/schemas/strategy.py`, `src/ainvest/schemas/__init__.py`,
  `tests/unit/schemas/**`, `docs/tasks/status.md`, `README.md`
- **Safety posture:** domain schemas and unit tests only; Paper remains the
  default; no broker write capability, credentials, AI calls, Telegram, or live
  trading are introduced
- **Verification:** final `./scripts/dev verify` passed (138 tests; 84.10%
  coverage); `./scripts/dev audit` found no known vulnerabilities; all required
  PR checks passed
- **Handoff notes:** Added frozen `PortfolioSnapshot` (account scope, cash,
  buying power, positions, exposure, open orders) plus immutable
  `StrategyContext` / `TradeSignal`. Strength is signed `[-1,1]` and not a
  probability; HOLD cannot carry target_weight or become an order. Future
  timestamps, inverted expiry windows, missing strategy versions, and
  inconsistent exposure fail closed. `./scripts/dev verify` passed (138 tests;
  84.10% coverage); `./scripts/dev audit` clean.
- **Next:** Batch B — Part 3 (B3) — `P02-T3` + `P02-T4`

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P02-T2` | Define Portfolio, Strategy Context, and TradeSignal Schemas | `merged` | cursor-agent | `task/batch-b2-strategy-schemas` (deleted) | `6eec043e1a6101ac17f7bfa6fa4fae51ef78ae5d` | `P02-T0`, `P02-T1` | [#23](https://github.com/likefudan/ainvest/pull/23) |

### Batch B — Part 1 (B1)

- **Batch:** Batch B — Part 1 (B1) — common domain types and
  market/research/evidence schemas (`P02-T0`, `P02-T1`)
- **Plan batch:** Batch B (partial; remaining parts B2–B4)
- **Coordinator:** cursor-agent / local
- **Status:** `merged`
- **Integration:** [PR #21](https://github.com/likefudan/ainvest/pull/21),
  squash merged into `main`
- **Integration branch:** `task/batch-b1-schemas` (deleted after squash merge)
- **Base commit:** `9afbf33448a981aa48bfa98f866a04a69eb92d28`
  (superseded the earlier `669746e` tip cited in the dispatch
  note so Batch A follow-ups from PR #20 are included)
- **Implementation commit:** `5f75db668f7cc0708404a609bdcba7bab385fb4e`
- **Dependency PRs/commits:** Batch A complete; config/package baseline on
  `main` including [PR #20](https://github.com/likefudan/ainvest/pull/20)
- **Merge target:** `main` (squash)
- **Safety posture:** domain schemas and unit tests only; Paper remains the
  default; no broker write capability, credentials, AI calls, Telegram, or live
  trading are introduced
- **Verification:** final `./scripts/dev verify` passed (124 tests; 83.79%
  coverage); `./scripts/dev audit` found no known vulnerabilities; all
  pre-commit checks passed; post-merge `main` CI passed
- **Next:** Batch B — Part 2 (B2) — `P02-T2` only

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P02-T0` | Define Common Domain Types | `merged` | cursor-agent | `task/batch-b1-schemas` (deleted) | `9afbf33448a981aa48bfa98f866a04a69eb92d28` | `P01-T2`–`P01-T4` | [#21](https://github.com/likefudan/ainvest/pull/21) |
| `P02-T1` | Define Market, Research, and Evidence Schemas | `merged` | cursor-agent | `task/batch-b1-schemas` (deleted) | `9afbf33448a981aa48bfa98f866a04a69eb92d28` | `P02-T0` | [#21](https://github.com/likefudan/ainvest/pull/21) |

### Batch A review remediation

- **Status/owner:** `merged` — `/root`
- **Integration:** [PR #16](https://github.com/likefudan/ainvest/pull/16),
  squash merged into `main`
- **Branch/base:** `agent/fix-batch-a-review` from
  `93e644406f97750cc5132242b62230a9cff3a73d`
- **Scope:** Corrected configuration source precedence, strict WebAuthn
  prerequisites, relative-import and ORM boundary enforcement, PR secret-scan
  permissions, immutable CI action references, complete dependency-profile
  auditing, and Batch terminology.
- **Verification:** `./scripts/dev verify` passed (89 tests; 85.83% coverage);
  `./scripts/dev audit` found no known vulnerabilities; all pre-commit checks
  passed, including Gitleaks.
- **Safety posture:** Paper remains the default. No broker write capability,
  credentials, external deployment, or live-trading enablement was introduced.
- **Repository protection:** GitHub ruleset
  [`Protect main`](https://github.com/likefudan/ainvest/rules/19761285) is active
  on the default branch with no bypass actors. It requires a current PR,
  squash merge, resolved review conversations, and successful `Verify`,
  `Secret scan`, and `Dependency audit` checks; it also enforces linear history
  and blocks deletion and force pushes.

### Batch A — Part 2

- **Batch:** Batch A — Part 2 — threat model, package boundaries, safe
  configuration, and CI gates (`P01-T1`, `P01-T3`, `P01-T4`, `P01-T5`)
- **Plan batch:** Batch A — **complete**
- **Coordinator:** cursor-agent / local
- **Integration branch:** `task/batch-a-part-2` (deleted after squash merge)
- **Base commit:** `c0b8106dd3efdfbc5853ba019cb6f3c29702dac7`
- **Dependency PRs/commits:** Batch A — Part 1 /
  [PR #4](https://github.com/likefudan/ainvest/pull/4);
  batch-naming [PR #5](https://github.com/likefudan/ainvest/pull/5)
- **Merge target:** `main`
- **Implementation commit:** `3d5afefca34aa8748efbbd4942b2c38b8b736726` (squash merge)
- **Handoff PR:** [#6](https://github.com/likefudan/ainvest/pull/6)
- **Safety posture:** documentation, package skeleton, fail-closed config, and
  CI only; Paper remains the default; no broker write capability, credentials,
  or live trading are introduced
- **Verification:** `./scripts/dev verify` passed on the integration branch
  (72 tests; coverage ≥80%)

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P01-T1` | Document Trust Boundaries and Threat Model | `merged` | subagent/p01-t1 | `task/p01-t1-threat-model` | `c0b8106dd3efdfbc5853ba019cb6f3c29702dac7` | `P01-T0` | [#6](https://github.com/likefudan/ainvest/pull/6) |
| `P01-T3` | Create Package Boundaries and Architecture Tests | `merged` | subagent/p01-t3 | `task/p01-t3-package-boundaries` | `c0b8106dd3efdfbc5853ba019cb6f3c29702dac7` | `P01-T2` | [#6](https://github.com/likefudan/ainvest/pull/6) |
| `P01-T4` | Implement Configuration Loading and Safe Defaults | `merged` | subagent/p01-t4 | `task/p01-t4-config` | `c0b8106dd3efdfbc5853ba019cb6f3c29702dac7` | `P01-T2` | [#6](https://github.com/likefudan/ainvest/pull/6) |
| `P01-T5` | Add CI, Commit Quality Gates, and Dependency Security | `merged` | subagent/p01-t5 | `task/p01-t5-ci` | `c0b8106dd3efdfbc5853ba019cb6f3c29702dac7` | `P01-T2` | [#6](https://github.com/likefudan/ainvest/pull/6) |

### Batch A — Part 1

- **Batch:** Batch A — Part 1 — decision/process baseline and Python project
  baseline (`P01-T0`, `P01-T2`)
- **Plan batch:** Batch A (partial; remaining cards were Batch A — Part 2)
- **Coordinator:** `/root`
- **Integration branch:** [PR #4 head branch](https://github.com/likefudan/ainvest/pull/4)
  (deleted after merge)
- **Base commit:** `0cc3ff895542986e677f8913bbc134dae8aea602`
- **Dependency PRs/commits:** None
- **Merge target:** `main`
- **Implementation commit:** `1d77146443e883f15475297e4858b7b705658d7d`
- **Handoff PR:** [#4](https://github.com/likefudan/ainvest/pull/4)
- **Safety posture:** documentation and development tooling only; Paper remains
  the default and no broker, credential, or live capability is introduced

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P01-T0` | Create the Decision Register and ADR Process | `merged` | `/root/p01_t0_decisions` | PR #4 head (deleted) | `0cc3ff895542986e677f8913bbc134dae8aea602` | None | [#4](https://github.com/likefudan/ainvest/pull/4) |
| `P01-T2` | Initialize the Python Project and Dependency Groups | `merged` | `/root/p01_t2_python` | PR #4 head (deleted) | `0cc3ff895542986e677f8913bbc134dae8aea602` | None; ran in parallel with `P01-T0` | [#4](https://github.com/likefudan/ainvest/pull/4) |

## Execution envelope: P01-T0

- **Title:** Create the Decision Register and ADR Process
- **Status/owner:** `merged` — `/root/p01_t0_decisions`
- **Branch/base:** PR #4 head branch (deleted after merge) at
  `0cc3ff895542986e677f8913bbc134dae8aea602`
- **Dependencies:** None
- **Design and plan authority:** `design.md` sections 1, 3, 11–12, and 16–17;
  `IMPLEMENTATION_TODO.md` sections 1, 4 (`P01-T0`), and 16
- **Accepted decisions:** `DEC-001`–`DEC-008`
- **Unresolved owner decisions:** `DEC-009`–`DEC-019`; no secret or owner value
  is required to complete this documentation task
- **Expected artifacts:** decision lifecycle and stable register; accepted,
  proposed, and live-deferred entries; ADR template; cross-agent tracker;
  minimal README links
- **Allowed paths:** `docs/decisions/**`, `docs/adr/**`,
  `docs/tasks/status.md`, and the minimal coordination links/status wording in
  `README.md`
- **Forbidden paths:** all application, test, configuration, dependency, lock,
  CI, and deployment files; in particular, every path owned by `P01-T2`
- **Verification:** `git diff --check`; verify all `DEC-001`–`DEC-019` IDs are
  unique and present; verify every register entry has state, owner, deadline,
  safe default, and affected phase; verify README links resolve
- **Blockers:** None
- **Assumptions:** Task deadlines are implementation gates rather than calendar
  dates. Owner-controlled external values remain unresolved and fail closed.
- **Handoff notes:** Coordinator review passed uniqueness, completeness, link,
  diff, and secret-signature checks. Downstream task prompts should cite
  decision IDs rather than restating or guessing owner choices.
- **Resulting commit/PR:** implementation commit
  `1d77146443e883f15475297e4858b7b705658d7d`;
  [PR #4](https://github.com/likefudan/ainvest/pull/4)

## Execution envelope: P01-T2

- **Title:** Initialize the Python Project and Dependency Groups
- **Status/owner:** `merged` — `/root/p01_t2_python`
- **Branch/base:** PR #4 head branch (deleted after merge) at
  `0cc3ff895542986e677f8913bbc134dae8aea602`
- **Dependencies:** None; coordinated to run in parallel with `P01-T0`
- **Design and plan authority:** `design.md` sections 3, 10–12, 14, and 16–17;
  `IMPLEMENTATION_TODO.md` sections 1, 4 (`P01-T2`), and 16
- **Accepted decisions:** `DEC-001`–`DEC-008` apply where relevant; this task
  must not implement runtime policy or enable trading
- **Expected artifacts:** supported Python version, installable `src` package,
  separated dependency groups, hash-locked dependencies, repository command
  wrapper, configured lint/type/test/coverage tools, and smoke tests
- **Allowed paths:** `pyproject.toml`, `src/ainvest/__init__.py`,
  `tests/conftest.py`, `.python-version`, dependency lock files, canonical
  command wrapper, and task-specific setup/testing documentation
- **Forbidden paths:** `docs/decisions/**`, `docs/adr/**`,
  `docs/tasks/status.md` while `P01-T0` owns it, and later-phase application
  modules
- **Canonical verification:** `./scripts/dev setup` and
  `./scripts/dev verify`. The full command surface is recorded above and in
  `docs/development.md`.
- **Owner values/credentials:** None. Do not install or record real credentials,
  account data, tokens, or live configuration.
- **Blockers:** None recorded
- **Assumptions:** The package baseline remains capability-free and Paper-safe.
  Optional dependency groups must preserve worker and privilege separation.
- **Handoff notes:** Python 3.12.13 locked setup passed. The task-owner run of
  `./scripts/dev verify` passed lock consistency, Ruff format/lint, strict
  mypy, unit (1), contract (1), integration (1), aggregate tests (3), and 100%
  branch coverage against the 80% gate. The Research-only dependency profile
  was also checked not to install MCP, FastAPI, Telegram, or WebAuthn packages.
  Coordinator verification independently passed the same full gate and a clean
  temporary-environment install. `yfinance` is isolated in the `offline-data`
  profile; the Research profile export contains no `yfinance`, MCP, FastAPI,
  Telegram, or WebAuthn package.
- **Resulting commit/PR:** implementation commit
  `1d77146443e883f15475297e4858b7b705658d7d`;
  [PR #4](https://github.com/likefudan/ainvest/pull/4)

## Execution envelope: P01-T1

- **Title:** Document Trust Boundaries and Threat Model
- **Status/owner:** `merged` — `subagent/p01-t1`
- **Branch/base:** `task/p01-t1-threat-model` at
  `c0b8106dd3efdfbc5853ba019cb6f3c29702dac7`
- **Dependencies:** `P01-T0` (merged)
- **Design and plan authority:** `design.md` sections 1, 3–5, 7–9, 11;
  `IMPLEMENTATION_TODO.md` sections 1, 4 (`P01-T1`), and 16; `docs/decisions`
- **Accepted decisions:** `DEC-001`–`DEC-008`
- **Allowed paths:** `docs/security/**`
- **Forbidden paths:** all application, test, CI, lock, and configuration
  runtime files; `docs/tasks/status.md` (coordinator-owned)
- **Verification:** `docs/security/{README,threat-model,data-flow}.md` with
  threats `T-001`–`T-016`, control/task mappings, residual risks, and diagrams
- **Blockers:** None
- **Handoff notes:** Integrated on `task/batch-a-part-2`. Threat IDs ready for
  Phase 08 security-test traceability.
- **Resulting commit/PR:** feature `c5564a4041588a46bcdb57de2f0ea7046b69cb8c`; squash `3d5afefca34aa8748efbbd4942b2c38b8b736726`; [PR #6](https://github.com/likefudan/ainvest/pull/6)

## Execution envelope: P01-T3

- **Title:** Create Package Boundaries and Architecture Tests
- **Status/owner:** `merged` — `subagent/p01-t3`
- **Branch/base:** `task/p01-t3-package-boundaries` at
  `c0b8106dd3efdfbc5853ba019cb6f3c29702dac7`
- **Dependencies:** `P01-T2` (merged)
- **Design and plan authority:** `design.md` section 10.2;
  `IMPLEMENTATION_TODO.md` sections 1 and 4 (`P01-T3`)
- **Accepted decisions:** `DEC-001`–`DEC-008` apply where relevant
- **Allowed paths:** `src/ainvest/{agents,data,schemas,strategies,risk,approval,execution,portfolio,audit,api}/**`,
  `src/ainvest/__init__.py`, `tests/unit/architecture/**`,
  `docs/architecture/**`
- **Forbidden paths:** `src/ainvest/config.py`, `config/**`, `.env.example`,
  `.github/**`, `.pre-commit-config.yaml`, `docs/security/**`,
  `docs/tasks/status.md`
- **Canonical verification:** architecture unit tests plus integrated
  `./scripts/dev verify`
- **Blockers:** None
- **Handoff notes:** Forbidden-edge matrix enforced via AST checker; intentional
  violation fixtures under `tests/unit/architecture/fixtures/`. Coordinator
  narrowed root `.gitignore` `data/`/`logs/` to `/data/`/`/logs/`.
- **Resulting commit/PR:** feature `5d30648` / `0457680`; squash `3d5afefca34aa8748efbbd4942b2c38b8b736726`; [PR #6](https://github.com/likefudan/ainvest/pull/6)

## Execution envelope: P01-T4

- **Title:** Implement Configuration Loading and Safe Defaults
- **Status/owner:** `merged` — `subagent/p01-t4`
- **Branch/base:** `task/p01-t4-config` at
  `c0b8106dd3efdfbc5853ba019cb6f3c29702dac7`
- **Dependencies:** `P01-T2` (merged)
- **Design and plan authority:** `design.md` sections 11–12;
  `IMPLEMENTATION_TODO.md` sections 1, 4 (`P01-T4`), and 16;
  `DEC-001`, `DEC-002`, `DEC-004`, `DEC-005`, `DEC-006`
- **Allowed paths:** `src/ainvest/config.py`, `config/**`, `.env.example`,
  `tests/unit/config/**`
- **Forbidden paths:** package submodules owned by `P01-T3`, CI files owned by
  `P01-T5`, `docs/security/**`, `docs/tasks/status.md`, and `pyproject.toml`
- **Canonical verification:** config unit tests; integrated
  `./scripts/dev verify`
- **Owner values/credentials:** None. Placeholders only in `.env.example`.
- **Blockers:** None
- **Handoff notes:** Paper defaults locked; live WebAuthn incomplete configs
  fail closed; secrets use `repr=False`. Downstream schema tasks may load
  settings through `ainvest.config` only.
- **Resulting commit/PR:** feature `4a808e79442a4d9ad4d6423c8af6b360ed1680bb`; squash `3d5afefca34aa8748efbbd4942b2c38b8b736726`; [PR #6](https://github.com/likefudan/ainvest/pull/6)

## Execution envelope: P01-T5

- **Title:** Add CI, Commit Quality Gates, and Dependency Security
- **Status/owner:** `merged` — `subagent/p01-t5`
- **Branch/base:** `task/p01-t5-ci` at
  `c0b8106dd3efdfbc5853ba019cb6f3c29702dac7`
- **Dependencies:** `P01-T2` (merged)
- **Design and plan authority:** `docs/development.md`;
  `IMPLEMENTATION_TODO.md` sections 1 and 4 (`P01-T5`)
- **Allowed paths:** `.github/**`, `.pre-commit-config.yaml`, `CODEOWNERS`,
  `pyproject.toml` (markers / CI tooling), `uv.lock`, `docs/development.md`,
  `tests/conftest.py`, `tests/unit/test_live_safety_marker.py`
- **Forbidden paths:** application modules under `src/ainvest/**` except via
  existing test surface, `docs/security/**`, `docs/tasks/status.md`,
  `config/**`, `.env.example`
- **Canonical verification:** CI invokes `./scripts/dev verify`; Gitleaks;
  pip-audit; local `./scripts/dev verify` passed after integration
- **Blockers:** None
- **Handoff notes:** `live_safety` marker cannot use skip/skipif. CODEOWNERS
  covers security/execution/approval/config/CI paths. No credentials in CI.
- **Resulting commit/PR:** feature `d83155de62ab76b31716c585bbbcf12194c064ce`; squash `3d5afefca34aa8748efbbd4942b2c38b8b736726`; [PR #6](https://github.com/likefudan/ainvest/pull/6)

## Execution envelope: P02-T0

- **Title:** Define Common Domain Types
- **Status/owner:** `merged` — `cursor-agent`
- **Branch/base:** `task/batch-b1-schemas` (deleted) at
  `9afbf33448a981aa48bfa98f866a04a69eb92d28`
- **Dependencies:** `P01-T2`–`P01-T4` (merged)
- **Design and plan authority:** `design.md` §6; `IMPLEMENTATION_TODO.md`
  sections 1 and 5 (`P02-T0`)
- **Allowed paths:** `src/ainvest/schemas/common.py`,
  `src/ainvest/schemas/__init__.py`, `tests/unit/schemas/test_common.py`,
  `docs/tasks/status.md`, `README.md`
- **Forbidden paths:** portfolio/strategy/order schemas (B2/B3),
  `schemas/json/**` versioning export (B4), broker/approval runtime
- **Verification:** unit tests for Decimal/UTC/InstrumentIdentity; included in
  `./scripts/dev verify`
- **Blockers:** None
- **Handoff notes:** Shared primitives ready for B2 `TradeSignal` /
  portfolio schemas. JSON uses decimal strings and UTC `Z` timestamps.
- **Resulting commit/PR:** squash
  `5f75db668f7cc0708404a609bdcba7bab385fb4e`;
  [PR #21](https://github.com/likefudan/ainvest/pull/21)

## Execution envelope: P02-T1

- **Title:** Define Market, Research, and Evidence Schemas
- **Status/owner:** `merged` — `cursor-agent`
- **Branch/base:** `task/batch-b1-schemas` (deleted) at
  `9afbf33448a981aa48bfa98f866a04a69eb92d28`
- **Dependencies:** `P02-T0` (same PR)
- **Design and plan authority:** `design.md` §5–§6.1;
  `IMPLEMENTATION_TODO.md` sections 1 and 5 (`P02-T1`)
- **Allowed paths:** `src/ainvest/schemas/market.py`,
  `src/ainvest/schemas/research.py`, `tests/unit/schemas/test_research.py`,
  `tests/unit/schemas/fixtures/**`
- **Forbidden paths:** `P02-T2`–`P02-T5` production paths; live/broker code
- **Verification:** design example validates; golden fixture present; stale /
  time-order / NL-as-evidence failures covered; `model_json_schema()` smoke
  test (versioned `schemas/json/` export deferred to B4 / `P02-T5`)
- **Blockers:** None
- **Handoff notes:** `ResearchPacket` requires market provenance, rejects
  look-ahead data, and aggregates freshness across market, technical, and
  evidence sources. Next claim is B2 (`P02-T2`).
- **Resulting commit/PR:** squash
  `5f75db668f7cc0708404a609bdcba7bab385fb4e`;
  [PR #21](https://github.com/likefudan/ainvest/pull/21)
