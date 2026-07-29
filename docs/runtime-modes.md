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
  the factory may run. Factory exceptions are translated to a stable,
  fail-closed error without retaining adapter messages or exception context.
  The raw port is retained only inside a private write-only proxy.
- For every submit and cancel, the proxy passes the concrete
  `BrokerSubmitRequest` or `CancelCommand` and a thread-safe, call-scoped
  delegate to the guard. The delegate expires when the guard method returns and
  permits exactly one call. The guard must return the exact result of that one
  call. Retained, late, concurrent, repeated, omitted, or result-substituting
  use fails closed without granting another broker send.
- The guard owns final request validation and delegate invocation. The P07-T4
  implementation must hold its lock, lease, or equivalent atomic decision
  boundary across its final order/account/allowlist/session/budget/kill-switch
  checks and the delegate call. A payload-blind check followed by a separate
  broker call is not a valid implementation.
- A non-domain guard failure before the broker delegate starts is a sanitized
  guard rejection. Once the delegate starts, any inconsistent guard outcome or
  non-domain failure is a stable `UNKNOWN_OUTCOME`; callers must reconcile by
  the submit/cancel idempotency key before any further write. A broker-domain
  error established by the delegate retains its normal broker taxonomy.
- The built-in `RejectingLiveGuard` always fails authorization. A guard decision
  is not cached: activating a kill switch or losing a prerequisite after
  startup blocks the next write before the underlying broker is called.
- Submit and cancel authorization are independent decisions. A switch that
  disables new submissions must not implicitly prevent an otherwise authorized
  risk-reducing cancel. A full guard revocation may block both. The built-in
  guard rejects both only because Live itself is unavailable in P08-T0.
- The proxy retains only an immutable `LiveGateContext` containing environment
  and mode; it never retains `Settings` or secret values. `Runtime` and the Live
  write proxy explicitly reject copying, deep-copying, and serialization so a
  broker capability cannot be duplicated or persisted.
- `AINVEST_ENV=production` plus Live is unconditionally rejected in P08-T0.
  A structural object, flag, or claimed readiness value cannot override this.
  P07-T4 must add the reviewed trusted integration before production Live can
  start.
- Read ports that expose `submit` or `cancel`, write ports that expose read
  methods, and ports with in-place replacement methods are rejected.
- `Runtime` construction is factory-controlled. Direct or uninitialized
  instances cannot expose broker ports or report health as `ready`.

These are composition-boundary guardrails for application code. They do not
claim to sandbox arbitrary trusted Python running in the same process: code
that deliberately uses private/mangled attributes or low-level object mutation
is inside the trusted-process boundary. Strategy and agent code must remain
isolated from the composition root and raw broker adapter.

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
