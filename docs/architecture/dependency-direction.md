# Package dependency direction

Boundary packages under `src/ainvest` follow the control-flow order from
`design.md` §10.2 and `IMPLEMENTATION_TODO.md` P01-T3.

## Packages

| Package | Role |
| --- | --- |
| `schemas` | Shared versioned Pydantic domain contracts |
| `data` | Read-only market/news/fundamentals adapters |
| `agents` | Research Agent orchestration |
| `strategies` | Strategy protocol, registry, isolated workers |
| `risk` | Hard risk rules and veto authority |
| `approval` | Human approval (Telegram paper / Passkey live) |
| `execution` | Paper broker and Robinhood MCP write path |
| `portfolio` | Positions, exposure, performance |
| `audit` | Append-only audit events |
| `api` | HTTPS approval and operator API |
| `workflow` | Domain commands/events, correlation IDs, in-process dispatcher |

## Runtime control and data flow

The arrows below describe runtime handoff of versioned data. They are **not**
permission for the package on the left to import the package on the right.

```text
schemas  <---  shared by all packages (import schemas only; never the reverse)
data     --->  agents / strategies (via schemas)
agents   --->  strategies (via ResearchPacket schemas)
strategies ---> risk (via TradeSignal schemas; no direct package import required)
risk     --->  approval (via OrderProposal schemas)
approval --->  execution (via approved order handoff)
execution ---> broker write tools only here
```

Layers exchange **versioned Pydantic models**, not ORM instances.

## Python import direction

All packages may import `schemas`. Orchestrators such as `api`, `execution`, and
`workflow` may import lower-level interfaces where the forbidden-edge matrix
permits it. Runtime control flow must use dependency injection when following
the data-flow arrows would otherwise create a forbidden reverse import.
`workflow` defines command/event envelopes and may import `schemas` plus
digest helpers from `audit`; lower packages must not import `workflow`.

## Forbidden Python imports (enforced by architecture tests)

| Importer | Must not import |
| --- | --- |
| `schemas` | any other boundary package; `sqlalchemy` / ORM |
| `data` | `agents`, `strategies`, `risk`, `approval`, `execution`, `api`, `workflow` |
| `agents` | `execution`, `approval`, `risk`, `workflow` |
| `strategies` | `execution`, `approval`, `risk`, `agents`, `workflow` |
| `risk` | `approval`, `execution`, `agents`, `strategies`, `workflow` |
| `approval` | `execution`, `agents`, `strategies`, `workflow` |
| `portfolio` | `execution`, `approval`, `agents`, `strategies`, `workflow` |
| `audit` | `execution`, `approval`, `agents`, `strategies`, `risk`, `workflow` |

Import cycles among boundary packages are forbidden.

## Domain vs ORM

- Domain models live in `schemas` (and future non-ORM helpers).
- SQLAlchemy ORM models must not live in `schemas` and must not cross package
  boundaries as ORM instances.
- Architecture tests fail if `schemas` imports `sqlalchemy` / `sqlalchemy.orm`.
- The future persistence package is reserved as `ainvest.db`. Boundary packages
  may consume repository interfaces, but may not import
  `ainvest.db.models` or `ainvest.db.orm`. `schemas` may not import any
  `ainvest.db` module.
- Both absolute and relative imports are resolved by the checker; relative
  syntax cannot bypass the dependency matrix.

## Checker proof

`tests/unit/architecture/fixtures/` holds intentionally invalid sources parsed
by unit tests to prove detection. Those fixtures are never imported by
production code.
