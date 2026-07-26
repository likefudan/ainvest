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
| `./scripts/dev audit` | Audit every locked dependency group and optional extra |
| `./scripts/dev verify` | Run the lock, formatting, lint, type, suite, and coverage gates |

`verify` is the canonical local merge gate. GitHub Actions CI calls this wrapper (see
`.github/workflows/ci.yml`) rather than duplicating tool-specific arguments. CI also runs a
Gitleaks secret scan and `./scripts/dev audit` after `./scripts/dev setup`. The audit exports and
checks the complete lock across every optional extra and dependency group, not only the default
development environment. Workflows must not inject real OpenAI, broker, or Telegram credentials,
must keep `LIVE_TRADING_ENABLED=false`, and must not upload `.env`, tokens, or account data as
artifacts.

CI actions are pinned to immutable commit SHAs. The workflow grants only read access to repository
contents and pull requests; pull-request read access is required for the Gitleaks action to inspect
PR metadata. Version comments beside action SHAs may be updated only after reviewing the upstream
release and replacing the SHA.

The repository owner should require the `Verify`, `Secret scan`, and `Dependency audit` checks on
`main`. If GitHub branch protection is unavailable for the repository's visibility and account
plan, this remains a manual merge requirement: never merge while one of those checks is pending,
skipped, or failed.

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
| Core | `uv sync --locked --no-default-groups` | Shared schemas, workflow, storage, scheduling |
| Research | `uv sync --locked --no-default-groups --extra research` | Research/data workers |
| Offline data | `uv sync --locked --no-default-groups --extra offline-data` | Development and offline research only |
| Approval | `uv sync --locked --no-default-groups --extra approval` | Approval API and Telegram worker |
| Broker | `uv sync --locked --no-default-groups --extra broker` | Official MCP broker gateways |
| Observability | `uv sync --locked --no-default-groups --extra observability` | Runtime telemetry |

Multiple deployment profiles may be combined by repeating `--extra`. In particular, the research
profile does not install the offline-data, approval, or broker packages. `yfinance` is isolated in
the offline-data profile and must never supply live quotes, pre-trade risk inputs, or broker
fallback data. The broker profile contains the official MCP Python SDK and must never add a client
that uses Robinhood usernames, passwords, or unofficial Robinhood APIs.

Production images should install only their required extras with `--no-default-groups`; Ruff,
mypy, pytest, and other development tools are intentionally absent from those environments.

## Dependency changes

Use compatible version ranges in `pyproject.toml`. APScheduler is deliberately constrained to the
3.11.x line until the architecture decision changes. After any dependency edit:

1. Run `./scripts/dev lock`.
2. Review both `pyproject.toml` and `uv.lock`, including transitive package additions.
3. Run `./scripts/dev verify`.
4. Commit the metadata and lock file together.

Never put credentials in dependency URLs, `.env` files committed to Git, tests, fixtures, logs, or
lock-file configuration.
