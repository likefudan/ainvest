# Deterministic historical performance reports

`ainvest.backtest.reporting.generate_report` implements P04-T11 as a pure
versioned Decimal metrics module. It needs no new dependency, account, provider,
credential, database or runtime service. The output is a bounded JSON domain
object with gross/net metrics displayed together, source fingerprints and a
non-optional historical-results disclaimer. It is not investment advice.

## Inputs and provenance

Callers retain a `ReportRequest` with 2–500 unique ordered `NavPoint` values,
one currency, explicitly chosen sample interval, optional benchmark,
optional annualization and optional P04-T10 walk-forward schedule. Values use
decimal strings, UTC clocks and per-point source digests. Code/version,
configuration, strategy/version, parameters, data, costs and temporal-validation
digests are mandatory. Inputs are capped at 1 MiB; NAV/flow/cost/traded values at
1e12. These fingerprints bind caller-supplied facts, **not independently
authenticated source capture or accounting correctness**.

NAV must be provided by a reviewed historical accounting composition. The
P04-T9 runner only produces decisions and injected historical portfolios; P04-T10
produces diagnostic costs, not a cash/position-feedback loop. The reporter never
fabricates such a loop or subtracts its costs from net NAV a second time. Its
integration test explicitly supplies a **toy constant-gross-NAV assumption** to
show cost traceability, not a strategy return or funded portfolio backtest.

Each point includes `gross_nav`, `net_nav`, `external_flow`, `cost` and
`traded_notional`. All fields are explicit, including zero values. The global
first point is an opening valuation: flow/cost/turnover must be zero there.
Gross/net curves use the same supplied external flow. Net NAV already reflects
costs. No accounting identity between independently supplied gross/net values is
invented by the reporter. Mixed currencies are not converted.

## Calculation definitions

`cash_flow_timing="end_of_period"` is the only supported convention. For each
consecutive pair, return is `(current NAV - external flow) / previous NAV - 1`.
Flows must reflect end-period deposits/withdrawals; intraperiod cash flows need
additional valuations and a reviewed upstream accounting convention. A deposit
is therefore not counted as a return. Positive starting NAV is required. Terminal
zero NAV can report a -100% return/drawdown; restarting from a zero base is
undefined and rejected, not reported as zero performance.

Total return compounds these flow-adjusted returns. Maximum drawdown is the
largest peak-to-trough loss in the compounded return index, **not** in raw NAV
that could be changed by a deposit. Period volatility is sample standard
deviation of observed period returns (`ddof=1`); it is absent with fewer than
two returns. This is per-observation dispersion, not an inferred trading-day
volatility. Annual volatility is period volatility × sqrt(periods per year),
only when explicit annualization exists and every elapsed interval matches its
`seconds_per_period`. Irregular overnight/weekend data is not silently assigned
252 periods/year. No CAGR, Sharpe ratio, risk-free rate or future-return estimate
is inferred.

Cost is the sum of provided period costs; traded notional is the sum of provided
gross traded notional. Turnover is **gross traded notional / mean net NAV** over
the section (all valuation points, including baseline). It includes both buys
and sells, not an unlabeled half-turnover convention. Deposits can affect this
mean denominator; the definition is retained in the output.

Arithmetic uses 50-digit precision, half-even rounding and 12-decimal output
metrics, independent of the host's ambient Decimal context. Compounded/period
return magnitudes are bounded; malformed, impossible-flow or oversized results
fail with stable `REPORT_*` codes. Money is never calculated with binary floats.

## Intervals, benchmarks and limitations

Each section requires exact opening and closing valuations at its declared
endpoints. Missing interval or endpoint produces `status="unavailable"` and
**null metrics/comparisons**. It does not stretch a nearby observation to match
the request. Cost/flow/turnover at the opening valuation is already in the
baseline and excluded; period events are accrued over `(start, end]`.

An optional benchmark must be same-currency, explicitly total-return, and have
exactly aligned observation timestamps within the section. Missing, unaligned,
foreign-currency or price-only benchmarks give null benchmark/relative returns
and a limitation code. Benchmark return is its endpoint level ratio minus one;
gross/net comparisons are arithmetic return differences, not alpha. The caller
must establish dividend/adjustment consistency upstream.

Walk-forward input produces separate in-sample and out-of-sample sections for
every fold, alongside overall gross/net metrics. Parameter fingerprints must
match the report identity. There is no pooling of overlapping training windows,
no duplicate test-period aggregation and no test-driven parameter selection.
The same valuation can close training and open testing; its closing cost belongs
only to training, avoiding double counting. Missing fold endpoints remain
explicitly unavailable. Temporal validation is retained as a source fingerprint;
this report is not an independent universal leakage qualification.

## Replay and authorization boundary

`PerformanceReport` retains request/benchmark/walk-forward digests, all source
identity fields and a self-checked report digest. `verify_report(request, report)`
recomputes the retained request and rejects altered metrics or source bindings.
Hashes are integrity/replay checks, not signatures against privileged replacement
of both data and hashes. The caller retains requests; this module does not save,
overwrite, delete or provision storage.

Every report keeps `source_kind="caller_supplied_historical_nav"`,
`execution_enabled=false` and `scheduled_paper_eligible=false`. Passing metrics
cannot approve a strategy, select risk limits, grant human approval, activate
real AI or complete the full production/scheduled Paper gates.
