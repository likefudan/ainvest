# Observability contract

The observability package provides operational summaries, not an audit record.
Append-only audit events remain the source of truth for decisions and replay.
Metrics, traces, and health snapshots must be safe to send to a less-trusted
telemetry system.

Install the `observability` dependency profile to use Prometheus or
OpenTelemetry. Importing `ainvest.observability` remains safe without that
profile. `AinvestMetrics` and the default `SafeTracer` fail with a stable error
when their requested backend is absent; `SafeTracer(disabled=True)` provides an
explicit deterministic no-exporter path.

## Metrics

`AinvestMetrics` owns an injectable Prometheus registry. It creates a private
registry by default, avoiding the process-global registry in tests and embedded
workers. A composition root that needs one shared exposition surface should
construct one instance and inject it into components.

The stable metric families are:

| Metric | Labels |
| --- | --- |
| `ainvest_workflow_outcomes_total` | `workflow`, `outcome` |
| `ainvest_workflow_duration_seconds` | `workflow` |
| `ainvest_provider_requests_total` | `operation`, `outcome` |
| `ainvest_provider_request_duration_seconds` | `operation` |
| `ainvest_data_freshness_seconds` | `data_kind` |
| `ainvest_agent_tokens_total` | `direction` |
| `ainvest_orders` | `state` |
| `ainvest_pnl_threshold_breached` | `threshold` |

Every label is selected from a package enum. Provider names, symbols, account
identifiers, proposal/order IDs, exception text, URLs, model names, and other
free-form values are forbidden. To add a label value, change and review the
corresponding enum; never pass a normalized or hashed high-cardinality value.
The registry renders bytes that an HTTP boundary may expose later. P08-T4 does
not add an HTTP server or alert rules.

## Tracing

`SafeTracer` accepts only stable span names and `TraceMetadata`. The metadata
surface contains workflow and mode plus validated correlation/causation,
proposal, strategy-run IDs, and canonical SHA-256 digests. Arbitrary attribute
maps are intentionally unsupported.

On failure the helper records only the exception class and an error status. It
does not call OpenTelemetry `record_exception`, because that API normally
retains the exception message and could export credentials or provider
payloads. Logs and audit records provide sanitized diagnostic context.

```python
with tracer.span(SpanName.RISK_EVALUATE, metadata):
    evaluate_risk()
```

Exporters, sampling, resource attributes, and collector transport belong to a
deployment composition task. Resource attributes must follow the same bounded
policy and must never contain configuration or credential values.

## Health

`HealthAggregator` keeps liveness separate from dependency-aware readiness:

- `ready`: the process is alive and every dependency is ready;
- `degraded`: a dependency is degraded, or an unavailable dependency is marked
  `degraded_allowed`;
- `not_ready`: the application liveness check failed or a required dependency
  is not ready.

The composition root must declare a non-empty tuple of `DependencySpec`
objects when constructing an aggregator. Every declaration begins as
`not_ready` with reason `starting`, so startup cannot report ready before all
required checks have produced an observation. A process that intentionally has
no dependencies must pass both an empty tuple and
`dependency_mode=DependencySetMode.NONE`; an accidental empty declaration is
rejected. Unknown dependencies and observations that change a declared kind or
requirement are rejected.

Each dependency observation carries a per-dependency, monotonically increasing
`sequence` allocated when the check starts. Sequence zero is reserved for the
initial `starting` record. The aggregator compares sequences atomically under its
lock: a late completion with a lower sequence is ignored, even if its wall
clock is later. For equal sequences, exact duplicates are idempotent, an
otherwise identical observation retains the later timestamp, and conflicting
status or reason values produce `not_ready/observation_conflict`. This tie rule
is commutative, so thread completion order cannot restore readiness. A later
successful check recovers readiness only by carrying a greater sequence.

`remove()` records a versioned `not_ready/unavailable` observation instead of
erasing a declaration. Losing the final required dependency therefore remains
not ready; it cannot turn an empty collection into ready.

External dependency failures can change readiness to `degraded` or
`not_ready`, but cannot change liveness. This prevents an upstream outage from
causing a restart storm. Only a local application-level check may call
`set_application_alive(False)`.

Snapshots contain a deterministic UTC check time, sorted dependency records,
bounded reason codes, and a posture derived from `TradingMode`. Paper reports
`read_only=true` and `execution=paper`: simulated Paper execution does not grant
live broker-write authority. Health is descriptive and never authorizes a
trade.

Dependency names are stable component identifiers, not provider URLs, account
names, symbols, or dynamic tenant values. Reasons are enums rather than raw
exceptions. Callers may inject a deterministic clock for tests and recorded
diagnostics.
