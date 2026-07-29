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
| Research scheduling | Allowed | Allowed | Allowed |
| Strategy evaluation and signal expiry | Not loaded | Allowed | Allowed |
| Approval expiry | Not loaded | Allowed | Allowed |
| Execution | Not loaded | Paper only | Live only |
| Order monitoring and reconciliation | Not loaded | Allowed | Allowed |

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
  explicit `LiveGuard`, and a write factory. The guard authorizes startup before
  the factory may run. The raw port is retained only inside a private write-only
  proxy, which asks the same guard to re-evaluate all gates immediately before
  every submit and cancel.
- The built-in `RejectingLiveGuard` always fails authorization. A guard decision
  is not cached: activating a kill switch or losing a prerequisite after
  startup blocks the next write before the underlying broker is called.
- `AINVEST_ENV=production` plus Live is unconditionally rejected in P08-T0.
  A structural object, flag, or claimed readiness value cannot override this.
  P07-T4 must add the reviewed trusted integration before production Live can
  start.
- Read ports that expose `submit` or `cancel`, write ports that expose read
  methods, and ports with in-place replacement methods are rejected.
- `Runtime` construction is factory-controlled. Direct or uninitialized
  instances cannot expose broker ports or report health as `ready`.

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

Non-production integration tests may inject a test guard:

```python
runtime = start_runtime(
    live_settings,
    live_guard=test_live_guard,
    live_write_factory=isolated_robinhood_write_factory,
)
```

Production Live cannot succeed in P08-T0, regardless of the injected guard.
P07-T4 must provide and integrate the trusted production gate. Telegram
approval can never authorize that path (`DEC-005`, `DEC-006`).
