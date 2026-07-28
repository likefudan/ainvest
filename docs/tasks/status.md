# Implementation Task Status

This file is the cross-agent coordination record for implementation work. It
records who owns a task, the exact source state they inherited, their permitted
write scope, dependencies, verification contract, blockers, and handoff. It is
not a substitute for the task card in `IMPLEMENTATION_TODO.md`.

Last updated: 2026-07-28

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
| Batch E — Research | Batch E | `P04-T0`–`P04-T12` | `in_progress` (`P04-T0` claimed) |
| Batch E — Paper approval | Batch E | `P05-T0`, `T1`, `T4`–`T6`, `T8` | `in_progress` (`P05-T0` claimed) |
| Batch E — Deferred live approval | Batch E | `P05-T7`, `P08-T14`, `P05-T2`, `P05-T3` | `not_started`; owner decisions remain deferred |
| Batch E — Cross-cutting foundation | Batch E | `P08-T0`, `T3`–`T9`, `T12`–`T14` | `in_progress` (`P08-T0`, `P08-T3` claimed) |

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

Tracker claim changes merge before their implementation PRs. The current
default serial queue after this claim is `P04-T0`, `P05-T0`, `P08-T0`, then
`P08-T3`. Later ready branches enter the queue only after their recorded
dependencies are on `main`. The coordinator may reorder independent ready
branches to reduce conflicts, but may not bypass the rebase, review, checks, or
squash-merge rules above.

#### Initial claims

| Task | Status | Owner | Branch / worktree | Immutable base | Dependencies |
|---|---|---|---|---|---|
| `P04-T0` | `in_progress` | `batch_e_p04_t0` | `agent/p04-t0-data-ports` / `.worktrees/p04-t0` | `3781fc165096aff1d4827b7e9c232e5c330b1e9e` | `P02-T1`, `P03-T13` (merged) |
| `P05-T0` | `in_progress` | `batch_e_p05_t0` | `agent/p05-t0-approval-challenges` / `.worktrees/p05-t0` | `3781fc165096aff1d4827b7e9c232e5c330b1e9e` | `P02-T3`, `P02-T4`, `P02-T6`–`P02-T9` (merged) |
| `P08-T0` | `in_progress` | `batch_e_p08_t0` | `agent/p08-t0-runtime` / `.worktrees/p08-t0` | `263f777c0b9fc438aa8f5ab87b3a8dd108765cbd` | `P01-T4`, `P03-T13` (merged) |
| `P08-T3` | `in_progress` | `batch_e_p08_t3` | `agent/p08-t3-logging` / `.worktrees/p08-t3` | `263f777c0b9fc438aa8f5ab87b3a8dd108765cbd` | `P01-T2`, `P02-T8` (merged) |

`P04-T0` may write `src/ainvest/data/{models,ports,fakes}.py`,
`src/ainvest/data/__init__.py`, `tests/unit/data/test_models.py`,
`tests/contract/data/**`,
`tests/unit/architecture/test_package_boundaries.py`, and
`docs/data-adapters.md`. It must not add a provider SDK, live fallback,
Robinhood implementation, or Research Agent behavior.

`P05-T0` may write `src/ainvest/approval/{service,tokens}.py`,
`src/ainvest/approval/__init__.py` (re-exports only),
`src/ainvest/schemas/approval.py`, `src/ainvest/db/repositories.py`,
`src/ainvest/db/uow.py`, `tests/unit/approval/test_approval_service.py`,
`tests/unit/approval/test_tokens.py`, and, only when regenerated by the
canonical schema-export command, `schemas/json/v1/ApprovalChallenge.json` and
`schemas/json/v1/MANIFEST.json`. The existing database tables cover this task;
it may not edit `src/ainvest/db/models.py`, create an Alembic revision, or edit
another database, schema, export, or test file without a new coordinator
assignment. It must not implement Telegram transport, WebAuthn, execution
handoff, or broker behavior.

##### Execution envelope: P08-T0

- **Title:** Define Runtime Modes and Startup Capability Gates
- **Status/owner:** `in_progress` — `batch_e_p08_t0`
- **Branch/worktree/base:** `agent/p08-t0-runtime` /
  `.worktrees/p08-t0` at
  `263f777c0b9fc438aa8f5ab87b3a8dd108765cbd`
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
- **Handoff:** pending implementation commit, scoped diff, verification
  evidence, independent review, PR, and squash-merge commit

##### Execution envelope: P08-T3

- **Title:** Add Structured Logging, Correlation, and Redaction
- **Status/owner:** `in_progress` — `batch_e_p08_t3`
- **Branch/worktree/base:** `agent/p08-t3-logging` /
  `.worktrees/p08-t3` at
  `263f777c0b9fc438aa8f5ab87b3a8dd108765cbd`
- **Design and task authority:** `design.md` sections 3.5, 3.6, 9, 11, and 13;
  `IMPLEMENTATION_TODO.md` sections 1, 11 (`P08-T3`), 12 (Batch E), and 16;
  `DEC-005`, `DEC-006`, `DEC-009`, `DEC-010`, and `DEC-015`–`DEC-018`
- **Dependencies:** `P01-T2` and `P02-T8`, both satisfied on the immutable
  base
- **Allowed paths:** `src/ainvest/observability/__init__.py`,
  `src/ainvest/observability/logging.py`, and
  `tests/unit/observability/test_logging.py`; no standalone documentation path
  is assigned
- **Shared configuration/dependencies:** `pyproject.toml` and `uv.lock` are
  read-only; `structlog` is already present in the observability dependency
  profile. `src/ainvest/config/**` is also read-only. Any dependency-profile or
  configuration change requires a new coordinator assignment.
- **Forbidden paths/scope:** every other production, test, documentation,
  configuration, dependency, schema, database, migration, CI, and tracker
  path; metrics/tracing/health (`P08-T4`); alerting (`P08-T5`); secret loading
  (`P08-T7`); logging raw prompts, approval links, tokens, authorization
  headers, account numbers, or full money-moving payloads
- **Verification:** `./scripts/dev unit`; `./scripts/dev verify`; inspect the
  scoped diff and secret signatures; test the secret corpus,
  nested/exception/header redaction, stable correlation fields, JSON output,
  and preservation of funds-safety events in
  `tests/unit/observability/test_logging.py`
- **Handoff:** pending implementation commit, scoped diff, verification
  evidence, independent review, PR, and squash-merge commit

#### Research track — `P04-T0` through `P04-T12`

All provider tests use recorded fixtures or deterministic fakes; canonical
tests must not require public network access. Under `DEC-003`, development data
can never become a live quote fallback.

| Task | Status | Dependencies / unlock | Allowed implementation scope |
|---|---|---|---|
| `P04-T0` | `in_progress` | `P02-T1`, `P03-T13` | `data/{models,ports,fakes}.py`, data re-exports, `tests/unit/data/test_models.py`, `tests/contract/data/**`, architecture boundary test, `docs/data-adapters.md` |
| `P04-T1` | `not_started` | `P04-T0` | `data/providers/yahoo.py`; Yahoo fixtures/tests; offline-data dependency/config changes only when assigned |
| `P04-T2` | `not_started` | `P04-T0` | `data/providers/sec.py`; filing/XBRL fixtures and tests; provider dependency/config changes only when assigned |
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
| `P05-T0` | `in_progress` | `P02-T3`, `P02-T4`, `P02-T6`–`P02-T9` | `approval/{service,tokens}.py`, approval re-exports, `schemas/approval.py`, `db/{repositories,uow}.py`, `tests/unit/approval/test_{approval_service,tokens}.py`; generated ApprovalChallenge schema + manifest only |
| `P05-T1` | `not_started` | `P05-T0`, `P01-T4`, `P02-T3`, `P02-T4` | `approval/telegram_approval.py`; callback validation, audit/outbox integration, tests |
| `P05-T4` | `not_started` | `P05-T0`, `DEC-005`; environment integration requires `DEC-010` | `approval/telegram.py`; notification/config adapter, snapshots, fake-transport tests |
| `P05-T5` | `not_started` | `P05-T4`, `P01-T4` | `approval/telegram_updates.py`; poller offset/dedup persistence; bounded webhook interface; tests |
| `P05-T6` | `not_started` | `P05-T0`, `P05-T1`, `P02-T7`, `P02-T10`, `P03-T12` | `approval/handoff.py`; workflow/outbox integration; exactly-once and recovery tests |
| `P05-T8` | `not_started` | `P05-T0`, `P05-T1`, `P05-T4`–`P05-T6`, `P08-T6`, `P08-T7`, `P08-T13` | `docs/releases/phase-3-acceptance.md`; Gate 3 harness and security evidence |

After `P05-T0` merges, `P05-T1` and `P05-T4` may be implemented in parallel.
`P05-T5` follows `P05-T4`; `P05-T6` follows `P05-T1`. The completed Paper
approval path unlocks `P08-T13`, then `P05-T8`.

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
| `P08-T0` | `in_progress` | `P01-T4`, `P03-T13` (satisfied) | `runtime.py`, `docs/runtime-modes.md`, `tests/unit/test_runtime.py` |
| `P08-T3` | `in_progress` | `P01-T2`, `P02-T8` (satisfied) | `observability/{__init__,logging}.py`, `tests/unit/observability/test_logging.py` |
| `P08-T4` | `not_started` | `P08-T3` | `observability/{metrics,tracing,health}.py`; observability tests |
| `P08-T5` | `not_started` | `P08-T4`, `P02-T9` | `observability/alerts.py`, `docs/runbooks/incidents/**`, alert tests |
| `P08-T6` | `not_started` | `P01-T1` | `docs/security/control-matrix.md`; security tests and assigned CI scan changes |
| `P08-T7` | `not_started` | `P01-T4`, `P01-T1` | `secrets.py`, `docs/security/secrets.md`, bounded identity/IAM artifacts and tests |
| `P08-T8` | `not_started` | `P01-T2`–`P01-T4`, `P03-T17` | `README.md`; safe Quickstart/Paper demo documentation only |
| `P08-T9` | `not_started` | `P03-T0`–`P03-T5` | `docs/strategy-plugin-guide.md`, starter template, external-package conformance test |
| `P08-T12` | `not_started` | incremental after each corresponding production card; not claimable as a broad umbrella | Coordinator-assigned, narrowly enumerated test files plus the matching `docs/testing.md` matrix rows only |
| `P08-T13` | `not_started` | `P02-T6`–`P02-T10`, `P03-T13`–`P03-T15`, `P05-T0`, `P05-T1`, `P05-T4`–`P05-T6` | `tests/{integration,faults}/**`; fake external services; test-only hooks coordinated |
| `P08-T14` | `not_started` | `P01-T1`, `P01-T4`, `P02-T8`, `P02-T10`, `P08-T7` | `admin/{auth,service}.py`, privileged API/CLI adapter, `docs/security/operator-access.md`, authorization/audit tests |

`P08-T0` and `P08-T3` are active on disjoint worktrees. `P08-T6`, `P08-T7`,
`P08-T8`, and `P08-T9` are dependency-ready but remain unclaimed; in
particular, this claim does not assign `P08-T6` or `P08-T7`. `P08-T12` is
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
