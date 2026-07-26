# Security documentation

This directory fixes ainvest's trust boundaries, threat model, and data-flow
assumptions before approval or broker code is written.

| Document | Purpose |
|---|---|
| [`threat-model.md`](threat-model.md) | Assets, trust domains, threats (`T-###`), controls mapped to task IDs and planned tests, residual risk |
| [`data-flow.md`](data-flow.md) | End-to-end data flows, trust-boundary crossings, and where credentials may and may not travel |

## Authority

- `design.md` §3–§5, §7–§9, §11 (architecture, approval, storage, secrets)
- `IMPLEMENTATION_TODO.md` §1 and card `P01-T1`
- `docs/decisions/README.md` (`DEC-001`–`DEC-008` accepted; owner values remain proposed or deferred)

## Non-negotiable posture

- Default mode is Paper Trading with fail-closed behavior on missing data,
  invalid approval, MCP failure, or incomplete risk limits.
- Telegram approval is Paper-only (`DEC-005`). Live writes require WebAuthn
  (`DEC-006`) and remain disabled until deferred live decisions are accepted.
- This documentation must not invent credentials, tokens, account numbers, or
  live enablement values.

## Maintenance

- New threats get the next free `T-###` ID; IDs are never reused.
- Control and test mappings are updated when the implementing task lands
  (`P08-T6` owns the living control matrix).
- Every high or critical live residual risk must be resolved or explicitly
  accepted before Phase 07.
