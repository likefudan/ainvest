# Point-in-time strategy decision replay

P04-T9 is an offline library, not a trading service or performance report.
`run_replay(data, points, config, definition=..., calendar=...)` constructs
historical strategy contexts and records dry strategy/sizing/risk decisions.
No broker handle, approval function, network reader, secret store or database
is accepted. Output always has `execution_enabled=false` and
`performance_report_available=false`.

Inputs require ordered raw bars with explicit `closed_at`, observation and
receipt timestamps, same-identity quotes, and explicitly expected historical
grids. At each cutoff only closed bars and quotes already received then are
eligible. A late revision cannot appear earlier merely because its market date
is old. Missing expected bars, stale/delayed/flagged inputs and indicator warm-up
gaps fail closed; no sorting, gap fill or guessed calendar grid repairs data.
Original knowledge/closure timestamps must be trustworthy; later historical
downloads without them cannot establish point-in-time correctness.

Only the eligible normalized prefix enters TA-Lib and the `StrategyContext`.
Visible-prefix digests and research IDs exclude unseen future values. The full
dataset digest remains separately available for artifact traceability. Retained
indicator parameters and TA-Lib/numpy/C-library versions, context/state/signal,
parameter/config/code digests and risk/sizing results support repeat runs. Normalized
input digests are not falsely labeled original provider wire captures. Empty
thesis is intentional: no AI explanation is fabricated for historical prices.

Strategy evaluation uses the existing isolated worker with network/filesystem
protection and mandatory CPU/memory/wall limits. One replay runs at a time; a
concurrent call is rejected rather than queued. Limits are 500 bars/quotes,
64 ordered cutoffs, a 120-second maximum deadline, 2 MiB per dataset/schedule,
256 KiB per retained step and 2 MiB total retained steps. Runtime worker latency
does not enter the deterministic semantic digest. Parameter, context and plugin
metadata returned by workers are reconciled before sizing.

These are the existing worker's process/env/network-hook/read-only-workdir
controls, not a new kernel-enforced sandbox against arbitrary native plugin code.
No stronger deployment isolation or production readiness is claimed. Calendars
are restricted to the existing local FakeMarketCalendar or ExchangeCalendar;
their configuration/horizon/library version is fingerprinted, and arbitrary
calendar callbacks are rejected rather than becoming an unrecorded I/O channel.

Portfolio snapshots, policy availability, sizing/risk limits, metadata, calendar,
volatility and state are explicit caller-supplied historical inputs. Only Paper
portfolio scope is accepted. No operational strategy or financial limit is
chosen; test policy is synthetic. Strategy state advances from validated prior
results, but portfolio snapshots do not assume fictitious fills. The same
reference strategy and Position Sizer are used by replay and Paper.

All standard screening/exposure/order rules run through the existing Risk Engine
in **proposal phase**. Its phase-specific behavior is preserved; this is not a
claim of pretrade approval, human approval or execution eligibility. Risk-rejected
candidates remain diagnostic decisions. More than one signal per point fails
conservatively; this initial adapter does not aggregate multiple strategies.

Raw adjustment is explicit. Fill/cost simulation, corporate-action treatment,
leakage/walk-forward validation and performance/benchmark disclosures belong to
P04-T10/T11. No return, profitability or recoverability claim is made here. Tests
use synthetic data and policy, actual isolated reference workers, future suffix
invariance, late-receipt/gap/stale rejection and same-context Paper parity; staging
and live accounts are untouched.
