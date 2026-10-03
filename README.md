# ainvest

AI-assisted stock research, deterministic Python strategies, independent risk
controls, and human-approved **Paper Trading**.

**Real-money trading is unavailable in the current release.** Research output
is not investment advice. Trading can lose money; simulated results do not
predict returns, liquidity, or execution quality. Do not fund an account or
enable Live to try this project.

## What works today

- A deterministic fixed-fixture Paper loop: strategy, sizing, risk, approval
  stub, simulated execution, reconciliation and audit evidence.
- Separately configured Telegram **Paper-only** approval, with one-time bound
  callbacks and a durable handoff; [Gate 3 acceptance](docs/releases/phase-3-acceptance.md)
  includes the owner-assisted staging rehearsal.
- Separately authorized Robinhood **display-only** queries through the pinned
  `rh-mcp` gateway. Their output is not eligible for trading decisions.
- Funds-safety alert interfaces and [incident procedures](docs/runbooks/incidents/README.md).
  Real notification providers, durable runtime storage and operational wiring
  still require deployment work; tests are not proof of delivered human alerts.

Research integration and other phases remain in development. The
[task tracker](docs/tasks/status.md) and [decision register](docs/decisions/README.md)
are authoritative; a passing demo does not complete the production or Live gates.

## Quickstart: no trading credentials

Prerequisites: Git, `uv` on your PATH, access to this repository, and Python 3.12
or newer (the repository pins 3.12 for development). Examples use a POSIX shell
on macOS/Linux. From a fresh checkout:

```bash
git clone https://github.com/likefudan/ainvest.git
cd ainvest
./scripts/dev setup
uv run --locked ainvest-paper-flow --dry-run
```

Installation downloads locked dependencies and the artifact-pinned broker
package, so network access is needed for setup. Installing that package does
**not** authorize Robinhood or enable a broker session. The demo itself uses
fixed local fixtures: no OpenAI key, Telegram Bot, Robinhood authorization,
`.env`, persistent database, or current market session is needed.

The final JSON summary should have `terminal` and `lifecycle` equal to
`APPROVAL_PENDING`, `filled_quantity` equal to `"0"`, and `error` equal to `null`.
This is a successful safety stop, not a failed run. Structured log lines may
appear alongside the summary. Running without flags has the same pending path.

### Explicitly simulate an approval and fill

```bash
uv run --locked ainvest-paper-flow --inject-approval
```

Expect `terminal=FILLED`, `lifecycle=FILLED`, and `conservation_ok=true`.
`--inject-approval` consumes a **test approval stub**, not a Telegram callback,
Passkey, or real operator approval. It is only for this fixed-fixture demo;
do not adapt it into an approval bypass for configured workflows.

Two optional safety exercises:

```bash
uv run --locked ainvest-paper-flow --inject-approval --expire-approval
uv run --locked ainvest-paper-flow --inject-approval --partial-fill
```

| Exercise | Expected terminal | Meaning |
| --- | --- | --- |
| Expired approval | `APPROVAL_EXPIRED` | No simulated submission/fill; quantity remains `"0"`. |
| Limited liquidity | `PARTIALLY_FILLED` | Only `"1"` share fills; conservation remains true. |

Each invocation is a separate synthetic run. Fixed IDs, market timestamps,
balances and risk limits are test data, not owner-approved configuration or
live market evidence. Do not combine `--dry-run` with `--inject-approval`.

## Configuration: keep safety defaults

The fixture CLI above constructs its own test configuration. It is not a
general application launcher and does not demonstrate loading your `.env` or
running a scheduled strategy. No complete production Research runner is implied.

For configured applications, preserve these defaults:

```dotenv
TRADING_MODE=paper
LIVE_TRADING_ENABLED=false
REQUIRE_HUMAN_APPROVAL=true
REGULAR_TRADING_HOURS_ONLY=true
REQUIRE_COMPLETE_RISK_LIMITS=true
```

Use [.env.example](.env.example) as a reference, not a ready-to-run secret file.
Do not overwrite an existing `.env`, copy placeholder credentials into a
service, or commit tokens/account numbers. When configuring an integration,
follow its explicit environment/secret-file instructions. Review shell
environment overrides as well as dotenv and YAML; never print loaded settings
to inspect secrets. See [secret isolation](docs/security/secrets.md).

Missing or invalid required risk limits reject orders. The
[risk example](config/risk.example.yaml) contains placeholders, not permission
to trade; owner-selected strategies and concrete limits remain decisions
`DEC-011`/`DEC-012`. The fixture's synthetic limits do not resolve them.
Configured order creation/execution requires a verified regular US trading
session. Stale/missing data and uncertain submit/cancel outcomes fail closed;
never retry an unknown order blindly. See [runtime gates](docs/runtime-modes.md).

### Optional: migrate a disposable database

The fixture demo needs no migration. To inspect the schema safely, create a
**new temporary** database, not an existing staging/production database:

```bash
AINVEST_DEMO_DIR=$(mktemp -d)
uv run --locked alembic -x "url=sqlite:///${AINVEST_DEMO_DIR}/paper.sqlite3" upgrade head
uv run --locked alembic -x "url=sqlite:///${AINVEST_DEMO_DIR}/paper.sqlite3" current
```

`current` should show the migration head. The explicit `-x url=...` selects the
temporary target even if `ALEMBIC_DATABASE_URL` is set. Keep both commands in the
same shell. This leaves a disposable database in that temporary directory; it
does not wire it into the fixture CLI. Back up and follow the relevant service
procedure before migrating a persistent database; do not experiment with
downgrades or repoint this example at a running poller's database.

## Tests and development

```bash
./scripts/dev verify
```

This is the canonical local merge gate: lock consistency, artifact verification,
formatting, lint, strict types, schema snapshots, unit/contract/integration tests
and coverage. Tests use fakes and Paper, not real credentials. Some optional
runtime tests may be skipped when their extra is absent; Live-safety tests may
never be skipped. For individual gates and dependency profiles, see
[development commands](docs/development.md).

The broker profile has a special hash-verifying pip installation step managed
by `./scripts/dev setup` and `./scripts/dev verify`. Do not replace it with
`uv sync --extra broker`, repin to an unreviewed version, or weaken manifest
checks to fix setup problems; consult the development guide's broker section.

## Architecture and boundaries

```text
Read-only data -> ResearchPacket -> strategy TradeSignal -> sizing -> risk
  -> frozen order proposal -> human Paper approval -> pre-trade risk
  -> PaperBroker -> reconciliation -> append-only audit
```

AI researches and explains; it cannot submit an order. Strategies run in
isolated workers without broker credentials and produce signals, not final
orders. Risk has veto authority. Approval is bound to the complete order hash;
Telegram authorizes Paper only. Changing an approved order requires a new
proposal and approval. Money uses Decimal; domain timestamps use UTC and
versioned schemas. See [design](design.md), [package boundaries](docs/architecture/dependency-direction.md),
and [schema contracts](docs/schema-versioning.md).

Non-goals include high-frequency trading, unattended live trading, derivatives,
crypto/margin strategies, managing other people's accounts, and natural-language
orders. First-release scope is US-listed stocks/ETFs, with limit orders preferred.

## Beyond the fixture demo

- **Telegram Paper:** start with [environment provisioning and notifications](docs/telegram-notifications.md),
  then [single-instance polling](docs/telegram-polling.md) and the
  [Gate 3 rehearsal](docs/releases/phase-3-acceptance.md). Real Bot/recipient setup
  is separate; never run a second poller against an active environment.
- **Robinhood display:** follow the [display-only CLI](docs/robinhood-read-cli.md),
  [read-account binding](docs/robinhood-account-binding.md) and optional
  [Telegram query adapter](docs/telegram-read-queries.md). Even `ready=true`
  or `tradable=true` in provider data is not approval or execution authority.
- **Strategy development:** start with the [external plugin developer guide](docs/strategy-plugin-guide.md)
  and [disabled HOLD-only starter](examples/strategy-plugin), then the
  [conformance reference](docs/strategy-conformance.md) and
  [moving-average example](src/ainvest/strategies/reference/moving_average).
  Plugin installation and passing tests never enable an operational instance.
- **Operations/security:** review the [threat model](docs/security/threat-model.md),
  [control matrix](docs/security/control-matrix.md), [observability](docs/observability.md),
  and [funds-safety incident runbook](docs/runbooks/incidents/README.md).
- **Integration contracts:** use the [schema, state and audit reference](docs/api/README.md)
  for generated internal message contracts and guarded, redacted query examples;
  these are not remote execution APIs.

Live is not a quickstart option. It requires completed/reviewed execution and
reconciliation gates, verified account/instrument/session evidence, owner-set
limits and account budget, a fixed HTTPS origin, closed Passkey bootstrap and
recovery, per-order WebAuthn approval, authenticated operator controls, and
validated operational safeguards. These requirements are not satisfied by
changing an environment flag, installing `rh-mcp`, or passing Gate 3. The
[decision register](docs/decisions/README.md) records the unresolved/deferred
choices; production Live remains disabled.
