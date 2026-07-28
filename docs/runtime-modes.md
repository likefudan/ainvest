# Runtime modes and startup capability gates

`ainvest.runtime` is the composition boundary for process authority. It exposes
one immutable capability matrix and fails startup when a process is given a
capability outside its configured mode.

This gate complements, but does not replace, the deterministic trading-session
and risk checks. `DEC-001` and `DEC-002` still require every order to be inside
a confirmed US regular session and to have every required risk limit. Missing
or uncertain inputs always produce no trade.

## Capability matrix

| Capability | Research | Paper | Live |
| --- | --- | --- | --- |
| Data and Research Agent packages | Allowed | Allowed | Allowed |
| Strategy execution and Risk packages | Not loaded | Allowed | Allowed |
| Approval and Execution packages | Not loaded | Allowed | Allowed |
| Telegram Paper approval | Not loaded | Allowed | Not an authorization capability |
| WebAuthn Live approval | Not loaded | Not loaded | Allowed |
| Robinhood account read port | Not loaded | Optional, read-only | Optional, read-only |
| Broker write port | None | `PaperBroker` write-only view | Robinhood write-only port through `LiveGuard` |
| Scheduler jobs | Research only | Research, strategy, Paper execution, reconciliation | Research, strategy, Live execution, reconciliation |

The same matrix also limits secret classes:

- Research: OpenAI and external read-only data-provider secrets.
- Paper: Research secrets, Telegram Bot, and optional Robinhood read-only
  authorization.
- Live: Research secrets, Robinhood read authorization, WebAuthn server
  material, and isolated Robinhood write authorization.

Secret *values* are never part of the matrix or health output.

## Startup rules

Use `start_runtime(settings, ...)` as the composition root:

- Research starts without any broker object. Passing a read port, PaperBroker,
  `LiveGuard`, or write factory is rejected.
- Paper requires a concrete deterministic `PaperBroker`. The returned runtime
  holds its write-only view, never a Robinhood write capability. A real account
  may be injected only through a separate `BrokerReadPort` that exposes no
  write methods.
- Paper rejects WebAuthn/LiveGuard construction. Telegram remains
  `approval_method=telegram`, `approval_scope=paper` under `DEC-005`.
- Live rejects `PaperBroker`. It requires complete validated settings, an
  explicit `LiveGuard`, and a write factory. The factory is passed to the guard,
  so the runtime cannot construct a Robinhood write port before the guard
  authorizes it.
- The built-in `RejectingLiveGuard` never invokes the factory and always fails
  startup. P07-T4 must supply the production guard; until then, production Live
  startup is impossible.
- A LiveGuard used under `AINVEST_ENV=production` must explicitly implement the
  P07-T4 production-ready contract. Test/development guards are rejected before
  their write factory can run.
- Read ports that expose `submit` or `cancel`, write ports that expose read
  methods, and ports with in-place replacement methods are rejected.

The first-release configuration locks
`REGULAR_TRADING_HOURS_ONLY=true`,
`REQUIRE_COMPLETE_RISK_LIMITS=true`, and human approval for Live. Runtime
startup validates these invariants again at the composition boundary.

## Health output

`Runtime.health_summary()` reports:

- readiness;
- the active mode;
- allowed package and future scheduler-job capability names;
- broker capabilities that are actually active, so an optional missing read
  port is never reported as available; and
- allowed secret classes with the fixed value `[REDACTED]`.

It never reports secret values, account identifiers, tokens, credentials, or
configuration object representations. Health is descriptive only; it does not
authorize trading.

## Examples

Research:

```python
runtime = start_runtime(Settings(trading_mode=TradingMode.RESEARCH))
```

Paper:

```python
runtime = start_runtime(settings, paper_broker=paper_broker)
```

Live intentionally has no working default:

```python
runtime = start_runtime(
    live_settings,
    live_guard=production_live_guard,
    live_write_factory=isolated_robinhood_write_factory,
)
```

The last example can succeed only after P07-T4 provides and validates the
production guard. Telegram approval can never authorize that path (`DEC-005`,
`DEC-006`).
