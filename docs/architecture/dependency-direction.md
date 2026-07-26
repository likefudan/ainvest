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

## Allowed direction

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

## Forbidden edges (enforced by architecture tests)

| Importer | Must not import |
| --- | --- |
| `schemas` | any other boundary package; `sqlalchemy` / ORM |
| `data` | `agents`, `strategies`, `risk`, `approval`, `execution`, `api` |
| `agents` | `execution`, `approval`, `risk` |
| `strategies` | `execution`, `approval`, `risk`, `agents` |
| `risk` | `approval`, `execution`, `agents`, `strategies` |
| `approval` | `execution`, `agents`, `strategies` |
| `portfolio` | `execution`, `approval`, `agents`, `strategies` |
| `audit` | `execution`, `approval`, `agents`, `strategies`, `risk` |

Import cycles among boundary packages are forbidden.

## Domain vs ORM

- Domain models live in `schemas` (and future non-ORM helpers).
- SQLAlchemy ORM models must not live in `schemas` and must not cross package
  boundaries as ORM instances.
- Architecture tests fail if `schemas` imports `sqlalchemy` / `sqlalchemy.orm`.

## Checker proof

`tests/unit/architecture/fixtures/` holds intentionally invalid sources parsed
by unit tests to prove detection. Those fixtures are never imported by
production code.
