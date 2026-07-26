# Implementation Task Status

This file is the cross-agent coordination record for implementation work. It
records who owns a task, the exact source state they inherited, their permitted
write scope, dependencies, verification contract, blockers, and handoff. It is
not a substitute for the task card in `IMPLEMENTATION_TODO.md`.

Last updated: 2026-07-26

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
…). When a plan batch is delivered in more than one integration part, record parts as
`Batch <Letter> — Part N`. Mark the plan batch complete only when every card in
that section has merged. Current Batch A split:

| Record | Plan section | Cards | Status |
|---|---|---|---|
| Batch A — Part 1 | Batch A | `P01-T0`, `P01-T2` | complete (merged) |
| Batch A — Part 2 | Batch A | `P01-T1`, `P01-T3`, `P01-T4`, `P01-T5` | complete (merged) |
| Batch A complete | Batch A | all of the above | complete |

Do not invent numeric variants such as `1A` or `Batch 1A`.

## Active batch

### Batch A remediation

- **Status/owner:** `in_review` — `/root`
- **Branch/base:** `agent/fix-batch-a-review` at
  `93e644406f97750cc5132242b62230a9cff3a73d`
- **Scope:** Correct review findings in P01-T3 through P01-T5: configuration
  source precedence, strict WebAuthn prerequisites, relative-import and ORM
  boundary enforcement, PR secret-scan permissions, immutable CI action
  references, complete dependency-profile auditing, and Batch terminology.
- **Safety posture:** Paper remains the default. No broker write capability,
  credentials, external deployment, or live-trading enablement is introduced.
- **Verification:** task-specific regression tests and the complete local merge
  gate pass; the all-profile audit command requires network access and is also
  enforced by CI.
- **External blocker:** GitHub returns HTTP 403 for both repository rulesets
  and classic branch protection because this private repository's current plan
  does not include those features. Upgrade the plan or make a separate,
  deliberate repository-visibility decision before requiring `Verify`,
  `Secret scan`, and `Dependency audit`. Until then, the owner must wait for all
  three checks before merging; GitHub cannot enforce that policy here.

After this remediation merges, the next claim is **Batch B** (`P02-T0` through
`P02-T5`) — domain schemas, market/research/portfolio/strategy/order/risk/
approval/broker contracts, canonical order hashing, and schema versioning.
Prefer the merge order in `IMPLEMENTATION_TODO.md` section 12
(B1 → B2 → B3 → B4). A coordinator must claim Batch B here before dispatching
those tasks.

## Completed batches

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
