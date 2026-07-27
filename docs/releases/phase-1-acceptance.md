# Phase 1 Acceptance — Deterministic Simulated Trading Loop (Gate 1)

**Decision / task:** `P03-T17` / Batch D4b  
**Status:** **accepted**  
**Acceptance date:** 2026-07-27  
**Kernel commit (D4a merge on `main`):** `36f51ebb795e04f64f91a4983bc65cdb953d86d0`  
**Handoff PR (orchestration):** [#70](https://github.com/likefudan/ainvest/pull/70)

## Scope

Gate 1 freezes the first usable domain kernel:

- Fixed `ResearchPacket` → strategy worker → signal aggregation → sizing →
  proposal-time risk → hash-bound proposal → **explicit** approval stub →
  pre-trade re-evaluation → PaperBroker submit → injected fill →
  reconciliation + portfolio ledger conservation.
- No Telegram, no Robinhood, no live credentials, no auto-approve paths.
- Paper account scope only (`LIVE_TRADING_ENABLED` remains false in CI).

Out of scope for this gate: Research Agent / OpenAI, Telegram approval UX,
Robinhood MCP, Passkey live approval, scheduler, and production deployment.

## Environment

| Item | Value |
| --- | --- |
| Python | 3.12+ (`.python-version` = 3.12) |
| Package manager | `uv` (locked `uv.lock`) |
| Canonical gates | `./scripts/dev setup` then `./scripts/dev verify` |
| Persistence | SQLite via SQLAlchemy + Alembic initial schema |
| Execution | in-process `PaperBroker` + `InProcessCommandDispatcher` |
| Calendar | `FakeMarketCalendar` (weekday regular session) |
| Fixed evaluation clock | `2026-07-23T15:00:00Z` |

## Verification commands

```bash
./scripts/dev setup
./scripts/dev verify
uv run --locked ainvest-paper-flow --dry-run
uv run --locked ainvest-paper-flow --inject-approval
```

Targeted safety / isolation suites exercised for this record:

```bash
uv run --locked pytest \
  tests/integration/test_paper_flow.py \
  tests/integration/strategies/test_worker_isolation.py \
  tests/unit/execution/test_state_machine.py \
  tests/unit/execution/test_paper.py -q
```

`./scripts/dev verify` on the Gate 1 kernel commit: **609 passed**, branch
coverage **≥80%** (observed ~86%).

## Empty DB → migrate → fixed packet → simulated fill → audit export

Harness (operator / CI notebook equivalent; uses public D4a API only):

1. Create an empty SQLite file and call `create_all_tables(engine)` (Alembic
   head / SQLAlchemy metadata for the Phase 1 schema).
2. `run_paper_flow(make_paper_flow_config(inject_approval=True))`.
3. Persist `PaperFlowResult.audit_events` through `AuditService.append`.
4. Export `AuditService.list_by_correlation(correlation_id)`.

Observed on 2026-07-27 against commit `36f51eb`:

| Metric | Result |
| --- | --- |
| Terminal | `FILLED` |
| Lifecycle | `FILLED` |
| Filled quantity | `4` |
| Correlation ID | `corr_01HZYD4APAPER0001` |
| Order hash | `sha256:c51164a98959000cacbfbb7c98a1f26391817d44a1c1c814dd21b066f17f8599` |
| Steps | 11 (strategy → reconcile) |
| Audit events persisted | 11 (same correlation) |
| Ledger conservation | `true` |
| Replay digests | identical across two runs |

## Security / safety assertions

| Assertion | Evidence |
| --- | --- |
| Strategy worker has no credentials / network | `tests/integration/strategies/test_worker_isolation.py` |
| Risk fails closed (allowlist / limits / session) | paper-flow risk rejection + unit risk suites |
| Never auto-approve | dry-run stops at `APPROVAL_PENDING`; `consume_challenge` requires explicit `approved=` |
| Paper submit idempotent | paper broker unit tests + dispatcher idempotency |
| Illegal state-machine transitions rejected | `tests/unit/execution/test_state_machine.py` |
| Blind broker retry blocked after UNKNOWN | `test_paper_flow_unknown_broker_then_reconcile` → `MANUAL_REVIEW` + `BlindBrokerRetryError` |
| No live broker / Telegram in Gate 1 path | composition root uses Paper + approval stub only |
| Secrets not required for Gate 1 | CI keeps `LIVE_TRADING_ENABLED=false`; no broker tokens |

## Five-flow matrix

| Flow | Driver | Expected terminal |
| --- | --- | --- |
| Success (replayable digests) | full liquidity + inject approval | `FILLED` |
| Risk rejection | mismatched allowlist | `RISK_REJECTED` (no submit) |
| Expired approval | consume after challenge TTL | `APPROVAL_EXPIRED` (no submit) |
| Unknown broker then reconcile | UNKNOWN write port | `SUBMIT_UNKNOWN` → `MANUAL_REVIEW`; blind retry blocked |
| Partial fill | `market_liquidity=1` | `PARTIALLY_FILLED` + conservation |

Covered by `tests/integration/test_paper_flow.py`.

## Performance baseline

Local single-process measurements (Apple Silicon / uv env; indicative only):

| Step | Observed |
| --- | --- |
| Empty SQLite `create_all_tables` | ~17 ms |
| Full paper flow (worker → fill → reconcile) | ~109 ms |
| Second identical run (digest replay) | ~110 ms |
| Full `./scripts/dev verify` suite | ~609 tests / ~11 s pytest wall |

These are not SLOs; they establish a Gate 1 baseline for future regressions.

## Defect register

| Severity | Count | Notes |
| --- | --- | --- |
| Critical | **0** | — |
| High | **0** | — |
| Medium / Low | tracked in PR review threads on #70 | e.g. custom write-port fill coupling documented/fail-closed |

Gate 1 acceptance requires high/critical = 0. Satisfied.

## Open decisions (do not invent values)

| ID | Topic | Gate 1 stance |
| --- | --- | --- |
| DEC-011 / DEC-012 | Numeric risk / strategy fixture values | Synthetic explicit fixtures only; not production limits |
| Telegram / Passkey | Human approval channels | Deferred; stub only |
| Robinhood live | Broker writes | Deferred; Paper only |

## Sign-off

| Role | Statement |
| --- | --- |
| Gate criterion | Fixed `ResearchPacket` → repeatable, testable simulated fill |
| Kernel SHA | `36f51ebb795e04f64f91a4983bc65cdb953d86d0` |
| Owner | cursor-agent / local coordinator |
| Result | **Phase 1 / Gate 1 accepted** |

Subsequent phases (Batch E+) may extend data, approval, and broker adapters
without rewriting the Gate 1 paper loop contracts frozen here.
