# Development Commands

This repository supports Python 3.12 and newer. Python 3.12 is the minimum compatibility
baseline and the version selected by `.python-version`. It is a maintained CPython release and
gives contributors a stable target while dependencies add support for newer interpreter versions.

The project uses [uv](https://docs.astral.sh/uv/) for Python installation, dependency resolution,
virtual environments, and command execution. `uv.lock` is committed and records artifact hashes.
Do not install dependencies manually into the project environment or invoke underlying tools with
ad hoc options in task handoffs.

## Canonical workflow

Run the repository wrapper from the repository root:

```bash
./scripts/dev setup
./scripts/dev verify
```

The complete command interface is:

| Command | Purpose |
|---|---|
| `./scripts/dev setup` | Create or update the locked default development environment |
| `./scripts/dev lock` | Intentionally regenerate `uv.lock` after changing dependencies |
| `./scripts/dev lock-check` | Verify the project metadata and lock file agree |
| `./scripts/dev format` | Apply formatting and safe lint fixes |
| `./scripts/dev format-check` | Check formatting without modifying files |
| `./scripts/dev lint` | Run static lint rules |
| `./scripts/dev type-check` | Run strict type checks |
| `./scripts/dev unit` | Run isolated unit tests |
| `./scripts/dev contract` | Run public/provider contract tests |
| `./scripts/dev integration` | Run cross-component integration tests |
| `./scripts/dev test` | Run all tests with branch coverage |
| `./scripts/dev export-schemas` | Write or `--check` committed JSON Schema snapshots |
| `./scripts/dev audit` | Audit every locked dependency group and optional extra |
| `./scripts/dev verify` | Run the lock, formatting, lint, type, suite, and coverage gates |

Schema compatibility rules and Strategy API version ranges are documented in
[`docs/schema-versioning.md`](schema-versioning.md). Committed snapshots live
under `schemas/json/`, with deterministic valid/invalid fixtures under
`tests/contract/fixtures/`. `verify` includes
`./scripts/dev export-schemas --check` so unintended schema or fixture drift
fails CI.

`verify` is the canonical local merge gate. GitHub Actions CI calls this wrapper (see
`.github/workflows/ci.yml`) rather than duplicating tool-specific arguments. CI also runs a
Gitleaks secret scan, GitHub CodeQL SAST, and `./scripts/dev audit` after `./scripts/dev setup`.
The audit exports and checks the complete lock across every optional extra and dependency group,
not only the default development environment. Workflows must not inject real OpenAI, broker, or
Telegram credentials, must keep `LIVE_TRADING_ENABLED=false`, and must not upload `.env`, tokens,
or account data as artifacts. The living threat-to-evidence register is
[`docs/security/control-matrix.md`](security/control-matrix.md); incomplete rows remain
release-blocking and are not made complete merely because general CI is green.

CI actions are pinned to immutable commit SHAs. The workflow grants only read access to repository
contents and pull requests; pull-request read access is required for the Gitleaks action to inspect
PR metadata. Version comments beside action SHAs may be updated only after reviewing the upstream
release and replacing the SHA.

GitHub ruleset `Protect main` (ruleset ID `19761285`) requires the `Verify`, `Secret scan`,
`Dependency audit`, and `SAST` checks
on the current default branch with strict up-to-date checking. It also requires pull requests,
resolved review conversations, squash-only merges, and linear history, and blocks deletion and
force pushes. The ruleset has no bypass actors. `SAST` is the workflow job context required by the
ruleset; the separate CodeQL reporting context is not added as a second required check.

## Pre-commit

Optional local hooks mirror the CI quality gates via `./scripts/dev`:

```bash
./scripts/dev setup
uv run --locked pre-commit install
uv run --locked pre-commit run --all-files
```

## Live-safety tests

Pytest registers a `live_safety` marker for trading-safety gates. Those tests are part of the
default suite run by `./scripts/dev test` / `./scripts/dev verify` and by CI. They must not use
`@pytest.mark.skip` or `@pytest.mark.skipif`; collection fails if they do. Do not deselect them
with an ordinary `-m "not live_safety"` policy in merge CI.

## Dependency profiles

The default install contains the environment-independent core plus development and test tools.
Runtime capabilities are isolated as optional extras:

| Profile | Install command | Intended consumer |
|---|---|---|
| Core | `uv sync --locked --no-default-groups` | Shared schemas, workflow, storage, scheduling, structured JSON logging |
| Research | `uv sync --locked --no-default-groups --extra research` | Research/data workers |
| Offline data | `uv sync --locked --no-default-groups --extra offline-data` | Development and offline research only |
| Approval | `uv sync --locked --no-default-groups --extra approval` | Approval API and Telegram worker |
| Broker | see [Broker profile](#broker-profile) — **not** `uv sync` | Official MCP broker gateways |
| Observability | `uv sync --locked --no-default-groups --extra observability` | Metrics, tracing, and health telemetry |

Multiple deployment profiles may be combined by repeating `--extra`. In particular, the research
profile does not install the offline-data, approval, or broker packages. `yfinance` is isolated in
the offline-data profile and must never supply live quotes, pre-trade risk inputs, or broker
fallback data.

Production images should install only their required extras with `--no-default-groups`; Ruff,
mypy, pytest, and other development tools are intentionally absent from those environments.

### Broker profile

The broker profile installs the independently reviewed, artifact-pinned `rh-mcp` release
`v0.3.0`. It is declared in the `broker` extra as a PEP 508 direct reference to the release
wheel with a `#sha256=` fragment, not as a version specifier: `rh-mcp` is not published on
PyPI, so a version specifier would resolve against an index where that name is unregistered
and therefore claimable. Every current pinned value comes from "Approved pin-refresh target:
`likefudan/rh-mcp` `v0.3.0`" in `docs/tasks/status.md` by way of
`src/ainvest/execution/robinhood/pins.py`; the recorded `v0.2.0` subsection is retained only as
historical evidence.

**The broker profile is installed with pip, not with `uv sync`, and that is a security
requirement.** `ainvest.execution.robinhood.artifact` verifies the installed gateway at
deployment/startup against the pinned wheel SHA-256, read from the PEP 610 `direct_url.json`
the installer writes. pip records that digest under `archive_info.hashes`. uv writes
`"archive_info": {}` for every install shape it offers — URL, local file,
`uv pip install --require-hashes`, `uv sync` — on both uv 0.11.26 (pinned in CI) and 0.12.3,
so a uv-installed gateway fails closed at startup with `artifact_digest_absent`. uv remains
the lock authority; only the final install step changes hands:

```bash
uv export --locked --no-default-groups --extra broker --no-emit-project \
  --format requirements.txt --output-file broker-requirements.txt
python -m pip install --require-hashes --requirement broker-requirements.txt
```

`./scripts/dev broker-install` runs exactly that against the development environment, and both
`./scripts/dev setup` and `./scripts/dev verify` perform it, because the merge gate asserts the
artifact pin against the real installed distribution rather than against a fixture.

`rh-mcp` requires `mcp>=2,<3`, so installing the broker profile installs the MCP Python SDK
transitively. The standing rule means **no direct dependency and no `mcp.*` import**: `ainvest`
never names `mcp` or `httpx2` in its own metadata, no module under `src/ainvest` imports either,
and the SDK is unreachable from the default profile and from every extra except `broker`. All
three are enforced by `tests/unit/test_dependency_boundary.py`, not by this paragraph. The
profile must never add a client that uses Robinhood usernames, passwords, or unofficial
Robinhood APIs.

## Dependency changes

Use compatible version ranges in `pyproject.toml`. APScheduler is deliberately constrained to the
3.11.x line until the architecture decision changes. After any dependency edit:

1. Run `./scripts/dev lock`.
2. Review both `pyproject.toml` and `uv.lock`, including transitive package additions.
3. Run `./scripts/dev setup` so the working environment matches the new lock.
4. Run `./scripts/dev verify`.
5. Commit the metadata and lock file together.

Never put credentials in dependency URLs, `.env` files committed to Git, tests, fixtures, logs, or
lock-file configuration.

## Strategy worker isolation

Strategy plugins run in isolated child processes via
`python -m ainvest.strategies.worker`. The host and child exchange **versioned JSON
only** (`WorkerRequest` / `WorkerResponse`); ORM objects, sockets, and credentials
must never cross that boundary.

Each worker:

- receives a scrubbed environment (broker / OpenAI / Telegram / DB / Passkey secrets removed)
- enforces wall-clock timeout plus best-effort CPU and memory limits
- blocks in-process socket construction by default
- uses a dedicated working directory that is read-only when practical

**Network isolation expectations:** the in-process socket block is fail-closed for
ordinary strategy code, but it is not a kernel sandbox. Production and CI should
also deny egress at the OS/container layer (`docker --network=none`, a network
namespace, or a Kubernetes NetworkPolicy). On macOS local development, rely on the
in-process block; do not treat it as a capability-safe sandbox.

Use `evaluate_in_worker` / `evaluate_many_in_workers` from `ainvest.strategies`. A
failure in one worker must not stop other strategy runs in the same batch.
