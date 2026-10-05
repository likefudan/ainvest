# Phase 2 — Research and offline backtesting evidence

**Task:** P04-T12 / Gate 2  
**Status:** offline software evidence complete; **full Gate 2 acceptance pending**  
**Date:** 2026-10-04  
**Prerequisite software baseline:** P04-T11 #204,
`ec898c13585adb81b58844a454913ce9653c876d`  
**Claim baseline:** #205, `ac693b5eb193ac740c6aafaf678d34aba0a0612e`

This document does not authorize real AI, scheduled Paper, deployment or Live.
DEC-009 remains partially unresolved: the owner approved **USD 20 per month**
on 2026-10-05; the OpenAI project and local secret reference are still pending.
This records a budget decision, not a configured provider limit or activation.
Passing fakes establish software invariants, not real-model quality/injection
resistance or authenticated capture of trading-eligible provider observations.

## Repeatable offline checks

```sh
./scripts/dev setup
./scripts/dev verify
./scripts/dev audit
uv run --locked python scripts/run_gate2_research.py
uv run --locked python scripts/run_research_evals.py
```

The Gate 2 harness prints synthetic Paper-flow logs followed by a final JSON
evidence summary. Exit zero means **offline checks passed**, never full release
approval. It reads only fixed repository-owned synthetic helpers and the saved
sanitized quote contract fixture. No runtime configuration, account identity,
OpenAI key, broker OAuth credential, Telegram token or operational DB is loaded.
No arbitrary file/module input, provider fallback or model-selection option exists.
The evaluation subprocess receives a minimal credential-free environment.

The summary hard-codes `full_gate_accepted=false`, `real_ai_eligible=false`,
`scheduled_paper_eligible=false`, `model_port=synthetic_only` and the next owner
action. Semantic fingerprints omit evaluation latency and log wall timestamps;
actual evaluation reports still retain measured latency. Repeated runs with the
same software, locked libraries and fixture inputs yield identical semantic
summary fields. No report is saved automatically.

## End-to-end synthetic proof

1. The existing read-only tools consume a fixed synthetic quote, closed history
   grid and flat Paper portfolio. Synthetic quote delivery is explicit at the
   cutoff; the existing risk receipt-skew/freshness limits remain unchanged.
2. A fake model port calls quote/history/indicators/concentration and returns
   only an exact observation from tool evidence. Fixed model/prompt/tool version
   contracts still apply; this is not a real OpenAI response.
3. The builder reconciles tool captures/evidence and builds a complete
   `ResearchPacket`. Quote prices, TA-Lib technical fields, position quantity,
   value, weight and buying power are checked against typed deterministic output.
4. Offline reconstruction recomputes the packet from the retained archive,
   without contacting providers or the model. Request/packet/capture/code hashes
   are recorded in the summary.
5. The generated packet enters the **existing Gate 1** isolated strategy,
   aggregation, sizing and standard risk composition. Default flow stops at
   `APPROVAL_PENDING` with zero simulated fills.
6. A second, separate test explicitly injects the existing approval stub, then
   exercises pre-trade checks, PaperBroker fill, reconciliation and ledger
   conservation. Costs are explicitly synthetic/nonzero. This is not Telegram
   approval, an operator's approval or authorization to submit a real order.

All strategy/risk/sizing settings, instrument identity, balance, timestamps and
approval are test fixtures. No owner strategy or operational risk limit is
selected. Temporary quota-bound SQLite tests verify append/idempotency/reload
and replay; the CLI creates no database. Storage/retention/recovery are not
provisioned by this evidence.

## Saved provider evidence boundary

The pinned v0.4.4 `get_equity_quotes.json` **sanitized schema-shaped contract
fixture** is normalized through the existing public mapper. Provider prose is
discarded; session-unverified/display-only observations remain ineligible for
trading. Only its digest and the disabled eligibility are returned. Locally
computed hashes in this fixture envelope are **not authenticated gateway
response-capture evidence**. No missing canonical identity or session evidence
is guessed to convert it into a complete strategy packet.

Real captured-provider replay and qualification remain pending. The existing
display-only Robinhood smoke tests are useful read connectivity evidence, not
permission to promote their data into execution inputs.

## Evidence matrix

| Requirement | Offline evidence | Boundary |
| --- | --- | --- |
| Required numeric fields originate in deterministic tools | `tests/integration/test_gate2_research.py`, builder/indicator/tool tests | Exact typed output/recomputation; no model-authored financial numbers |
| Fixed Responses/model/schema/settings | `tests/integration/test_research_agent_flow.py` | Mock transport only; real adapter disabled |
| Injection, old news, conflicting sources, extreme/missing data | `tests/evals/research/`, versioned 12-case suite | Compliant/hostile fake model ports, not actual LLM qualification |
| Stale quote, provider timeout, unsupported observation, forged citation | Gate 2 four-case failure matrix | Failed archive withholds packet; no Paper handoff |
| Evidence tamper/replay/storage quota/rollback | Builder unit/integration and DB repository tests | Temporary storage; hashes not protection against privileged whole-record replacement |
| Explicit budget admission/exhaustion/unknown use | `tests/evals/research/test_research_budget.py` | Synthetic rates, in-memory reservations; not durable monthly billing enforcement |
| No default approval, existing risk/sizing/pre-trade, reconciliation | Gate 2 pending/test-fill/conservation plus Gate 1 suites | Paper stub only, no broker/Telegram calls |
| Point-in-time context and worker isolation | T9 replay and worker suites | Existing process hooks, not a new kernel sandbox |
| Costs/partial/shared volume/adjustments/leakage/walk-forward | T10 tests and [validation contract](../backtest-validation.md) | Explicit hypothetical profiles; no cost-fed portfolio feedback loop |
| Comparable gross/net/in/out reporting | T11 tests and [reporting contract](../backtest-reporting.md) | Caller-supplied historical NAV, no fabricated benchmarks or predictions |

Current-head canonical checks, secret scan, dependency audit and SAST must pass
before merge. Existing optional Telegram-runtime skips are not live-safety skips;
required live-safety tests remain selected and cannot be skipped.

Canonical local setup/verify on this implementation: **2247 passed**, one existing
optional Telegram-runtime skip; 1868 unit, 245 contract and 97 integration checks;
mypy 348 files; branch coverage **87.52%**. All seven new Gate 2 integration tests
pass, including the credential-canary CLI case. Locked dependency audit found no
known vulnerabilities; direct-URL rh-mcp remains separately artifact-pin verified.
Current-head CI is required before merge. The implementation PR is the merge record.

## Remaining gates and owner action

The following are not made complete by this offline record:

- Owner establishes an OpenAI API project with a local secret-file/reference
  outside Git; the **USD 20 per month** ceiling is already owner-approved in
  [DEC-009](../decisions/README.md). Provider limit configuration and application
  enforcement are not yet verified. Never paste the key into chat, logs, a PR,
  fixture or committed environment file; full DEC-009 acceptance remains pending.
- Reviewed durable usage/budget admission and notification wiring, credential
  loading/secret isolation, limits, pause behavior and operational storage.
  The existing process-local guard is not a monthly budget guarantee.
- Authenticated real provider capture, canonical identity/session/account quality
  where applicable, and offline replay without upgrading read-only evidence.
- Actual fixed-model evaluations, including prompt injection and quality gates;
  usage/cost observation and explicit owner release acceptance. No fallback model
  or scheduled Paper is enabled automatically.
- Separate owner strategy/risk/retention/recovery choices and deployment gates.
  Gate 3 acceptance does not substitute for this Gate 2 qualification.

The next required human input is the OpenAI API project identity and local
secret reference; the monthly budget has been supplied. Until then, the
supported artifact is reusable offline software
evidence; **full Gate 2 remains unaccepted**, real AI and scheduled Paper stay off,
and real-money trading remains unavailable.
