# Offline research evaluations and budget admission

Run the repeatable, credential-free software checks with:

```sh
uv run --locked python scripts/run_research_evals.py
```

The runner prints one JSON report and returns zero only if all offline checks
pass. It does not save files or contact a provider. Optional
`--compare /absolute/path/prior-report.json` checks comparable suite definitions,
synthetic rates, thresholds and case ordering. Reads are size-bounded and errors
sanitized. This does not approve an upgrade.

The twelve versioned cases cover ordinary data, conflicting sources, stale news,
empty filings, extreme prices, injected source instructions, prohibited trade/
numeric prose, forged references, unsupported observations, invalid model JSON
and stale required quotes. They run the actual tools, agent validation, assembly
and replay with fake model ports. Source instructions cannot add named execution
or configuration capabilities. Compliant and hostile fakes probe both outcomes;
neither proves real-model injection resistance.

Reports retain model/prompt/tool versions and digests, suite/archive digests,
schema success, evidence coverage, unsupported attempts versus accepted claims,
numeric/replay consistency, measured wall latency, observed tokens/requests,
usage completeness and estimated cost. No packet means coverage and numeric
consistency are absent, not vacuously perfect. Negative cases must match their
expected status and rejection code. All outcomes must match, accepted unsupported
claims must be zero and retained claims need full verified evidence coverage.
Explicit versioned latency/token/cost ceilings and unknown usage fail closed.
Costs round upward using **synthetic rates, not current API prices**; incomplete
usage makes total cost incomplete.

Reports always say `qualification=offline_software_only`,
`scheduled_paper_eligible=false` and `real_ai_eligible=false`. Real-model full
evaluations and owner release approval remain prerequisites for scheduled Paper,
model/prompt changes or real AI activation.

## Explicit budget boundary

`ResearchBudgetGuard` requires an explicit ceiling and versioned synthetic rates.
The caller must check admission before starting research. Maximum token costs
are reserved atomically; known usage settles once, repeated exact settlement is
idempotent and differing settlement conflicts. Unknown usage retains at least
the reservation or known observed lower bound and returns a sanitized
`pause_new_research` alert event. Insufficient capacity, observed excess or run
capacity exhaustion causes a sticky pause. No reset, eviction or model downgrade
is available.

Alerts are domain events for a future trusted notifier, not Telegram sends.
This process-local guard holds at most 128 runs; it is not a durable monthly
ledger or provider billing guarantee. No real pricing profile, accepted owner
budget, project/key, scheduler or operational storage is configured. DEC-009 and
reviewed durable budget/notification composition remain activation gates.
Canonical verification includes the offline tests; no staging DB, secrets,
Telegram poller or real account is read or changed.
