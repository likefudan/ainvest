# Decision Register

This register is the canonical index of product and implementation decisions for
ainvest. Accepted entries are authoritative under the source-precedence rules in
`IMPLEMENTATION_TODO.md`. An execution agent must cite the relevant decision IDs
in its task envelope and must not invent an unresolved owner value.

Last reviewed: 2026-07-27

## Decision lifecycle

Every decision keeps its stable `DEC-NNN` identifier for the lifetime of the
project. IDs are never reused or renumbered.

- `proposed`: the owner decision is unresolved. The recorded safe default is
  binding until the decision is accepted.
- `accepted`: the decision is active and must be implemented as written.
- `deferred_until_live`: the owner decision is intentionally not required for
  the Paper release. Live capabilities affected by it remain disabled.
- `superseded`: the entry is historical. It must name the accepted decision or
  ADR that replaced it; do not delete the old entry.

Only the product/account owner may accept choices involving credentials,
accounts, budgets, production identity, or numeric trading limits. An agent may
draft options and an ADR, but it may not change those choices to `accepted`.
Changing an accepted safety decision requires an accepted ADR that records the
reason, migration, rollback, and replacement decision ID.

Deadlines below are gate or task deadlines, not permission to weaken the safe
default. If a deadline arrives without an accepted value, the affected
capability stays disabled.

## Accepted decisions

| ID | Decision | State | Owner | Deadline | Safe default / required behavior | Affected phases and tasks |
|---|---|---|---|---|---|---|
| `DEC-001` | New orders may be created or executed only during the US regular trading session, including the exchange calendar's holidays and early closes. | `accepted` | Product owner | Accepted 2026-07-26 | Treat every time outside a confirmed regular session, or any calendar uncertainty, as non-trading time. | Phase 03 (`P03-T10`, `P03-T16`), Phase 07 (`P07-T1`), Phase 08 (`P08-T0`, `P08-T1`, `P08-T15`) |
| `DEC-002` | Every required risk limit must be present and valid before an order can be proposed or executed. There are no implicit tradable defaults. | `accepted` | Product owner | Accepted 2026-07-26 | Reject the trade when any required limit is missing, empty, malformed, out of range, or inconsistent. Research-only work may continue. | Phase 01 (`P01-T4`), Phase 03 (`P03-T8`–`P03-T12`, `P03-T16`), Phase 07 (`P07-T1`), Phase 08 (`P08-T0`, `P08-T15`) |
| `DEC-003` | Prefer contract-tested, officially supported Robinhood MCP read capabilities behind the read-only gateway. Robinhood MCP is the sole live quote source; there is no live fallback to Alpaca, yfinance, or another provider. | `accepted` | Product owner | Accepted 2026-07-26 | If a required MCP capability is unavailable, stale, incomplete, internally inconsistent, or schema-incompatible, reject trading. Optional sources remain development/offline only. | Phase 04 (`P04-T0`–`P04-T5`), Phase 06 (`P06-T0`–`P06-T3`), Phase 07 (`P07-T1`) |
| `DEC-004` | AI uses OpenAI `gpt-5.6-sol` through Pydantic AI and the Responses API with `reasoning_effort=medium`, `store=false`, strict structured output, built-in web search off, and no automatic model fallback. | `accepted` | Product owner | Accepted 2026-07-26 | A model error, timeout, refusal, invalid schema, unsupported evidence, or exhausted one-time transient retry produces no strategy-usable complete `ResearchPacket`. | Phase 04 (`P04-T5`–`P04-T8`, `P04-T12`) |
| `DEC-005` | Telegram approval is Paper-only. It uses a proposal/order-hash-bound, one-time callback in a numeric-ID-authorized private chat and produces only `approval_method=telegram`, `approval_scope=paper`. | `accepted` | Product owner | Accepted 2026-07-26 | Plain approval text, group messages, usernames, mismatched IDs/messages/hashes, expired or replayed nonces, and Telegram approval on a live path are rejected. | Phase 05 (`P05-T0`, `P05-T1`, `P05-T4`–`P05-T8`), Phase 07 (`P07-T1`), Phase 08 (`P08-T15`) |
| `DEC-006` | Every live order requires a fixed HTTPS origin and a single-use Passkey/WebAuthn approval bound to the canonical order hash, with `approval_method=webauthn`, `approval_scope=live`. | `accepted` | Product owner | Accepted 2026-07-26 | Without the live origin, closed bootstrap, recovery-capable credentials, or a valid WebAuthn assertion, live broker writes remain disabled. | Phase 05 (`P05-T2`, `P05-T3`, `P05-T7`), Phase 07 (`P07-T1`, `P07-T6`), Phase 08 (`P08-T15`) |
| `DEC-007` | The first release does not modify a live order in place. A replacement is cancellation followed by a new proposal, risk decision, canonical order hash, and approval. | `accepted` | Product owner | Accepted 2026-07-26 | Reject in-place replacement and never reuse the old approval for a changed order. Reconcile an uncertain cancellation before further action. | Phase 02 (`P02-T4`, `P02-T9`), Phase 05 (`P05-T0`, `P05-T6`), Phase 07 (`P07-T3`, `P07-T5`), Phase 08 (`P08-T13`, `P08-T15`) |
| `DEC-008` | Until a separate owner policy is accepted, the kill switch blocks new submissions and alerts but does not automatically cancel existing orders. | `accepted` | Product owner | Accepted 2026-07-26 | Leave existing orders under normal reconciliation and operator-controlled cancellation; never issue blind bulk cancellation. | Phase 07 (`P07-T4`, `P07-T5`), Phase 08 (`P08-T5`, `P08-T14`, `P08-T15`) |
| `DEC-020` | First-release multi-strategy signal aggregation fails closed: per-symbol conflicts become `NEEDS_REVIEW` / no trade; never emit opposing orders; do not weight `strength` as probability. | `accepted` | Engineering | Accepted 2026-07-27 | Exact duplicates collapse; disagreeing intent, `generated_at`, expiry, strategy version/identity, target weight, or strength → no selected signal. See `docs/decisions/adr-020-multi-strategy-signal-aggregation.md`. | Phase 03 (`P03-T7`, `P03-T6`, `P03-T16`) |

## Owner decisions required for the Paper release

| ID | Decision needed | State | Owner | Deadline | Fail-closed safe default | Affected phases and tasks |
|---|---|---|---|---|---|---|
| `DEC-009` | Provision the OpenAI API project outside the repository and set its monthly budget ceiling. Record only the approved budget and secret reference, never the API key. | `proposed` | Product/account owner | Before a real OpenAI call is enabled in `P04-T6` | The real AI adapter is disabled; deterministic fakes may be used. A missing or exhausted budget cannot produce a complete `ResearchPacket`. | Phase 04 (`P04-T6`, `P04-T8`, `P04-T12`) |
| `DEC-010` | Create separate staging and production Telegram Bots and provide each environment's allowed numeric private `user_id` and `chat_id`. Bot tokens stay only in secret storage. | `proposed` | Product/account owner | Before environment integration in `P05-T4` | Telegram integration and approval consumption are disabled. Tests use synthetic IDs and fake tokens only. | Phase 05 (`P05-T1`, `P05-T4`, `P05-T5`, `P05-T8`) |
| `DEC-011` | Select the first enabled strategy and approve its concrete universe, schedule, and parameters. | `proposed` | Product owner | Before the owner-configured end-to-end Paper run in `P03-T16` | No strategy instance is enabled. Reference strategies and synthetic parameters are test/demo artifacts only and cannot be treated as owner approval. | Phase 03 (`P03-T2`, `P03-T3`, `P03-T16`, `P03-T17`) |
| `DEC-012` | Set concrete per-order, per-symbol, sector, daily turnover/loss, drawdown, cash-reserve, and related required risk limits. | `proposed` | Product/account owner | Before any non-test Paper proposal in `P03-T16` | Under `DEC-002`, every order is rejected. Tests may use explicitly labeled synthetic fixtures. | Phase 01 (`P01-T4`), Phase 03 (`P03-T8`–`P03-T12`, `P03-T16`, `P03-T17`), Phase 07 (`P07-T1`) |
| `DEC-013` | Set retention periods for audit, raw market, research, approval, and execution records, including any legal or provider constraints. | `proposed` | Product owner | Before `P08-T2` is accepted for a persistent environment | Perform no automated deletion, restrict access, and do not claim production readiness until a retention policy and deletion boundary are accepted. | Phase 02 (`P02-T8`), Phase 08 (`P08-T2`, `P08-T6`, `P08-T10`, `P08-T15`) |
| `DEC-014` | Set backup recovery point objective (RPO) and recovery time objective (RTO), plus the systems and records they cover. | `proposed` | Product owner | Before `P08-T2` is accepted for a persistent environment | Do not claim recoverability or enable live trading; preserve source data and document that restore objectives are unverified. | Phase 08 (`P08-T2`, `P08-T10`, `P08-T15`) |

## Decisions deferred until live

| ID | Decision needed | State | Owner | Deadline | Fail-closed safe default | Affected phases and tasks |
|---|---|---|---|---|---|---|
| `DEC-015` | Select the fixed approval domain, cloud/runtime environment, TLS termination, production database, and secret manager. | `deferred_until_live` | Product/account owner | Before production completion of `P05-T7` or any live broker write | Run locally or in an explicitly non-live environment only; no live approval origin and `LIVE_TRADING_ENABLED=false`. | Phase 05 (`P05-T3`, `P05-T7`), Phase 07 (`P07-T0`, `P07-T1`), Phase 08 (`P08-T7`, `P08-T15`) |
| `DEC-016` | Define the independent first-Passkey bootstrap authentication and recovery process, including at least two recovery-capable credentials. Telegram cannot bootstrap or reset Passkeys. | `deferred_until_live` | Product/account owner | Before production acceptance of `P05-T2` and any live broker write | Registration/bootstrap stays closed and all `webauthn+live` approval is unavailable. | Phase 05 (`P05-T2`, `P05-T3`, `P05-T7`), Phase 07 (`P07-T1`, `P07-T6`), Phase 08 (`P08-T15`) |
| `DEC-017` | Authorize the dedicated Robinhood Agentic Account and set its live budget. | `deferred_until_live` | Product/account owner | Before any real-order exercise in `P07-T6` | No Robinhood write credentials or capability are provisioned; live trading remains disabled. | Phase 06 (`P06-T0`–`P06-T3` read-only authorization as applicable), Phase 07 (`P07-T0`, `P07-T1`, `P07-T6`) |
| `DEC-018` | Select the production Operator Control Plane authentication method/provider and assign least-privilege roles. | `deferred_until_live` | Product/account owner | Before exposing a remote production control plane or enabling any live write | No anonymous or Telegram-derived operator identity; remote privileged endpoints and live mode stay disabled. | Phase 07 (`P07-T4`, `P07-T5`), Phase 08 (`P08-T7`, `P08-T14`, `P08-T15`) |
| `DEC-019` | Decide whether a future kill switch may automatically cancel open orders and, if so, define eligible orders, partial-fill handling, ordering, reconciliation, and recovery. | `deferred_until_live` | Product/account owner | Must be accepted before automatic cancellation is implemented or enabled; it is not required to operate live under `DEC-008` | `DEC-008` remains binding: block new submissions and alert, but do not automatically cancel existing orders. | Phase 07 (`P07-T4`, `P07-T5`), Phase 08 (`P08-T5`, `P08-T14`, `P08-T15`) |

## How to update this register

1. Claim the related task in `docs/tasks/status.md`.
2. Add an ADR from `docs/adr/0000-template.md` when a choice changes
   architecture, a public contract, security posture, money safety, deployment,
   or an accepted decision.
3. Keep the original decision row and ID. If replaced, mark it `superseded`,
   identify the replacement ID and ADR, and add the new row.
4. Record the owner decision and its date without copying credentials, tokens,
   private keys, account numbers, or other secret values.
5. Update affected task envelopes and tests in the same PR.

Code, configuration descriptions, tests, task handoffs, and ADRs should cite
decision IDs as `DEC-NNN`. An accepted ADR may add detail, but it must not make
an unresolved external value appear accepted.
