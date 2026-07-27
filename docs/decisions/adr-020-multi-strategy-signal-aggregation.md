# ADR-020: First-release multi-strategy signal aggregation

- **Status:** accepted
- **Date:** 2026-07-27
- **Decision owner:** Engineering (first-release safety policy)
- **Decision register IDs:** `DEC-020`
- **Affected phases/tasks:** `P03-T7`, `P03-T6` (downstream sizing), `P03-T16`
- **Deadline or gate:** Before multi-strategy Paper proposals in `P03-T16`
- **Supersedes:** None
- **Superseded by:** None

## Context

`design.md` §5.3.4 requires the Position Sizer path to merge multiple strategy
signals for one symbol. `IMPLEMENTATION_TODO.md` `P03-T7` requires a durable
first-release rule so conflicting signals become at most one candidate order and
never self-cross.

`TradeSignal.strength` is an internal score in `[-1, 1]`, not a success
probability or sizing weight (`design.md` §5.3.3 / §6.2). Blind averaging or
strength-weighted voting would invent an unapproved probability model.

## Decision drivers

- Safety and fail-closed behavior
- Determinism, auditability, and replayability
- Never emit opposing BUY/SELL orders for one symbol
- Preserve every input signal and a stable reason code
- Avoid unapproved strength/probability weighting

## Considered options

### Option 1: Conflict → no trade / `NEEDS_REVIEW` (selected)

Within one evaluation clock, actionable signals for a symbol must agree on
intent, target weight, generation time (`generated_at` as signal `as_of`),
expiry, strategy identity, strategy version, and strength. Exact duplicates
collapse to one signal. Any disagreement returns `NEEDS_REVIEW` with no selected
signal. Strength is never averaged or treated as probability.

### Option 2: Strength-weighted merge

Combine agreeing or opposing intents by weighting `strength`. Rejected: no
accepted rule treats strength as probability, and opposing intents would still
risk self-crossing unless a separate veto existed.

### Option 3: Priority / newest-wins

Pick one strategy by configured priority or latest `generated_at`. Deferred:
requires an owner-approved priority table (`DEC-011`-adjacent) that does not
exist for the first release.

## Decision

First-release aggregation (`ainvest.portfolio.signal_aggregation`) is:

1. Evaluate all inputs at a single caller-supplied UTC `as_of`.
2. Partition by `symbol`. Emit **at most one** result per symbol.
3. Partition actionable signals (active BUY/SELL at `as_of`) by group key
   `(generated_at, expires_at, strategy_version)` — the card's
   `as_of` / expiry / strategy-version axes, with `generated_at` as the signal
   evaluation clock.
4. Across or within groups for one symbol:
   - BUY and SELL together → `NEEDS_REVIEW` / `INTENT_CONFLICT`
   - Differing `generated_at` → `NEEDS_REVIEW` / `AS_OF_MISMATCH`
   - Differing `expires_at` → `NEEDS_REVIEW` / `EXPIRY_MISMATCH`
   - Differing `strategy_version` → `NEEDS_REVIEW` / `STRATEGY_VERSION_MISMATCH`
   - Differing strategy name → `NEEDS_REVIEW` / `MULTI_STRATEGY_CONFLICT`
   - Differing `target_weight` → `NEEDS_REVIEW` / `TARGET_WEIGHT_CONFLICT`
   - Differing `strength` → `NEEDS_REVIEW` / `STRENGTH_DISAGREEMENT` (no merge)
5. Exact duplicates (same trade-relevant fields) collapse by lowest
   `signal_id`. Outcome `SELECTED` with reason `SINGLE_SIGNAL` or
   `DUPLICATE_SIGNALS_COLLAPSED`.
6. HOLD, expired, and not-yet-active signals are preserved in
   `input_signals` but never selected.
7. Aggregation selects at most one `TradeSignal` per symbol. Quantity and
   limit price remain the Position Sizer's job (`P03-T6`). Risk retains veto.

## Fail-closed behavior

- Any unresolved conflict → no selected signal (`NEEDS_REVIEW`).
- Empty or non-actionable inputs → `NO_TRADE` (never invent a signal).
- No strength weighting, no probability interpretation, no silent newest-wins.
- The module never returns two selected sides for one symbol.

## Consequences

### Positive

- Deterministic, auditable, and safe under multi-strategy load.
- Clear machine reason codes for operators and risk/review handoff.
- Compatible with current sizer (one signal in → at most one candidate).

### Negative and trade-offs

- Agreeing multi-strategy BUYs still require review until a later merge ADR.
- Duplicate deliveries with any field drift (including strength) fail closed.

### Residual risks

- Orchestrators must not bypass aggregation and size every raw signal.
  Mitigation: workflow/integration cards consume this API; architecture tests
  keep `portfolio` free of `strategies`/`execution` imports.

## Implementation and validation

- **Affected public interfaces:**
  `aggregate_signals`, `SignalAggregationResult`, `AggregationOutcome`,
  `AggregationReasonCode` in `ainvest.portfolio.signal_aggregation`.
- **Configuration and migration:** none (pure function; no tradable defaults).
- **Tests and gate evidence:** `tests/unit/portfolio/test_signal_aggregation.py`;
  `./scripts/dev verify`.
- **Observability and audit evidence:** every result carries full
  `input_signals` plus `reason_code`.
- **Rollout plan:** ship with `P03-T7`; wire in later workflow cards.
- **Rollback plan:** revert module; leave sizer single-signal path unchanged.

## Follow-up

- [x] Update `docs/decisions/README.md`.
- [x] Update affected entries in `docs/tasks/status.md`.
- [x] Update implementation, examples, and tests.
- [x] Verify no secret value was added to the repository.
- [ ] Optional later ADR: owner-approved multi-strategy merge (priority table
      or explicit consensus rule) once `DEC-011` strategies are enabled.

## References

- `design.md` sections 5.3.3, 5.3.4, 6.2
- `IMPLEMENTATION_TODO.md` task `P03-T7`
- Decision register: `DEC-020`
