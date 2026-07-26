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
5. A shared integration branch may be used only when the wave coordinator
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

## Active wave

None. A coordinator must claim the next wave here before dispatching tasks.

## Completed waves

### Wave 1A

- **Wave:** 1A — decision/process baseline and Python project baseline
- **Coordinator:** `/root`
- **Integration branch:** `agent/wave-1a-foundation`
- **Base commit:** `0cc3ff895542986e677f8913bbc134dae8aea602`
- **Dependency PRs/commits:** None
- **Merge target:** `main`
- **Implementation commit:** `1d77146443e883f15475297e4858b7b705658d7d`
- **Handoff PR:** [#4](https://github.com/likefudan/ainvest/pull/4)
- **Safety posture:** documentation and development tooling only; Paper remains
  the default and no broker, credential, or live capability is introduced

| Task | Title | Status | Owner/agent | Branch | Base commit | Dependencies | Handoff PR |
|---|---|---|---|---|---|---|---|
| `P01-T0` | Create the Decision Register and ADR Process | `merged` | `/root/p01_t0_decisions` | `agent/wave-1a-foundation` | `0cc3ff895542986e677f8913bbc134dae8aea602` | None | [#4](https://github.com/likefudan/ainvest/pull/4) |
| `P01-T2` | Initialize the Python Project and Dependency Groups | `merged` | `/root/p01_t2_python` | `agent/wave-1a-foundation` | `0cc3ff895542986e677f8913bbc134dae8aea602` | None; ran in parallel with `P01-T0` | [#4](https://github.com/likefudan/ainvest/pull/4) |

## Execution envelope: P01-T0

- **Title:** Create the Decision Register and ADR Process
- **Status/owner:** `merged` — `/root/p01_t0_decisions`
- **Branch/base:** `agent/wave-1a-foundation` at
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
- **Branch/base:** `agent/wave-1a-foundation` at
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
