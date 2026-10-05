# Offline costs and temporal validation

P04-T10 adds pure, bounded diagnostic libraries. It does not place orders, grant
approval, update a portfolio/database, select owner strategy/risk policy, or
measure strategy returns. P04-T9 continues to inject caller-supplied historical
portfolio snapshots: hypothetical fills do **not** feed back into those snapshots.
No production backtest orchestration or cash/position-constrained execution is
implied. Performance reporting is a separate P04-T11 contract.

## Required synthetic cost configuration

`CostConfig` has no monetary defaults. Callers supply a version, flat commission,
commission basis points, half-spread/slippage basis points, participation fraction,
positive minimum delay and explicit unit-test flag. For example, **test values only**:

```json
{"version":"synthetic-v1","commission_flat":"1","commission_bps":"10",
 "half_spread_bps":"5","slippage_bps":"5","participation":"0.10",
 "minimum_delay_seconds":1,"unit_test_zero_cost":false}
```

These numbers are not current Robinhood fees, market estimates or approved risk
settings. All-zero configured cost is rejected unless `unit_test_zero_cost=true`;
that flag is retained in results. A nonzero cost profile can still yield zero
realized cost when no hypothetical order fills.

`CostRequest` retains explicit RAW adjustment, 1–128 unique Paper candidates and
1–500 ordered, nonoverlapping closed bars of one full instrument identity and
interval. Identity must already be known when the candidate is created. Quality
flags, delayed bars, mismatches and oversized economics fail closed. Inputs are
capped at 2 MiB; outputs at 4096 fills. Quantities/prices/notional are capped at
1e12, order increments at least 1e-8; decimal arithmetic uses a fixed context.
No approval/execution package or broker handle is used.

Orders compete in creation-time/candidate-ID priority. Each bar has one shared
volume × participation pool. Quantities round down to their order increment.
Only bars starting after the configured positive delay are eligible. The entire
bar volume is a **post-hoc approximation**, so liquidity is credited at its explicit
closure, and a window extending beyond order expiry is not used. Future bar volume
never enters a strategy context. This is not an assertion of an intrabar fill time.

Prices start at the next eligible raw open, move adversely by half-spread plus
slippage and round adversely to the price tick. If that price violates the limit,
there is no fill: no favorable clipping, high/low touch assumption or same-bar fill.
Adverse stress prices are hypothetical and need not have traded within observed
high/low. Flat commission is charged only on the first partial fill per candidate;
percentage commission applies to every fill. Fee/impact rounding is conservative
to eight money decimals. Returned cash deltas are diagnostics, not funded balances.

JSON roundtrip requests replay deterministically. Results retain request/config
digests and each fill's request binding; `verify_cost_replay` recomputes the retained
request and rejects changed totals, fills or parameters. Requests must be retained
by the caller: no storage, deletion or operational cache is provisioned here.

## Adjustment convention

Cost simulation requires raw order prices and raw execution bars. Mixing adjusted
historical prices with raw candidates is rejected. `AdjustmentRequest` separately
transforms one caller-established instrument/currency lot. RAW permits explicitly
ordered, unique, already-available effective split and dividend-credit events.
Already-applied action IDs prevent reapplication; the returned IDs must be carried
into the next request. Split shares change while authoritative `total_basis` stays
constant; per-share basis is a 12-decimal display value. Dividend credits require
explicit entitled shares and an actual credit timestamp: current shares/ex-date
are not guessed to establish payment entitlement. No tax calculation is implied.

SPLIT and SPLIT_AND_DIVIDEND conventions permit no action reapplication at all.
The caller must supply consistently adjusted share/basis inputs. This conservative
boundary does not infer a vendor's total-return formula. Adjustment inputs use
at most eight decimals and 1e12 bounds; unsupported fractional lots fail rather
than silently lose shares. Cash-in-lieu and provider capture are not implemented.

## Temporal evidence and rolling windows

`TemporalRequest` records code/config/data digests and up to 512 individual data
uses, 512 historical universe records and 64 prefix probes. It checks:

- event and knowledge timestamps do not exceed the use cutoff;
- bars are explicitly closed before use;
- filings/news are actually published before availability/use, not merely dated
  to an earlier reporting period;
- historical membership covers each non-parameter use, was known at the cutoff,
  and coverage is explicitly asserted `complete_point_in_time`;
- parameter selection occurs before its recorded train/test freeze deadline;
- perturbing an unseen future suffix changes neither visible inputs nor decisions.

Unknown universe coverage, today's survivor list, missing publication/closure
times or ineffective probes cannot become a passing check. Records and the
coverage assertion are supplied trusted evidence, not independently authenticated
proof that all delisted instruments exist. A digest does not authenticate source
capture. Probe digests must come from retained paired runs; the checker does not
execute arbitrary callbacks or prove absence of hardcoded future knowledge.
Results explicitly keep `universal_leakage_proof=false` and `live_eligible=false`.

`walk_forward` generates at most 64 rolling half-open train/embargo/test windows.
Training windows can overlap; out-of-sample windows cannot. Window durations,
stride, embargo, fixed parameter digest, data digest and code version are explicit.
Parameter fitting is not performed; selection deadlines end at the training
boundary. Each fold's actual selected parameters must be retained and validated
against that deadline by the composition layer. No test-driven optimization or
pooling of duplicate out-of-sample timestamps is authorized.

Tests include a deliberately leaking final-price strategy detected by suffix
perturbation, late filings, incomplete universes and altered cost totals, plus
the actual isolated reference replay followed by diagnostic costs and a paired
future-suffix check. Fake policy/data remain distinct from deployment qualification.
