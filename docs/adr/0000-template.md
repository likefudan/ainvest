# ADR-NNNN: Short decision title

- **Status:** proposed
- **Date:** YYYY-MM-DD
- **Decision owner:** Name or role
- **Decision register IDs:** `DEC-NNN`
- **Affected phases/tasks:** `Pxx-Tn`
- **Deadline or gate:** Before `Pxx-Tn` / Gate N
- **Supersedes:** None
- **Superseded by:** None

## Context

Describe the problem, why a durable decision is required, and the current
repository or operational constraints. Link the relevant sections of
`design.md`, `IMPLEMENTATION_TODO.md`, and the decision register.

## Decision drivers

- Safety and fail-closed behavior
- Security and least privilege
- Determinism, auditability, and replayability
- Operational complexity and recovery
- Compatibility, cost, and delivery timing

Add or remove drivers that are material to this decision.

## Considered options

### Option 1: Name

Describe the option, including security assumptions, failure modes, cost, and
operational consequences.

### Option 2: Name

Describe the option using the same criteria.

## Decision

State the selected option precisely. For an owner-controlled external value,
record the approved value or a non-secret configuration reference; never record
credentials, tokens, account numbers, private keys, or secret contents.

## Fail-closed behavior

State exactly what the system does when this decision is unresolved, its
configuration is absent or invalid, or a dependency is unavailable. For
money-moving behavior, the default must be no trade.

## Consequences

### Positive

- List the benefits.

### Negative and trade-offs

- List the costs and limitations.

### Residual risks

- List remaining risks, their owner, and the required mitigation or acceptance.

## Implementation and validation

- **Affected public interfaces:**
- **Configuration and migration:**
- **Tests and gate evidence:**
- **Observability and audit evidence:**
- **Rollout plan:**
- **Rollback plan:**

## Follow-up

- [ ] Update `docs/decisions/README.md`.
- [ ] Update affected entries in `docs/tasks/status.md`.
- [ ] Update implementation, examples, and tests.
- [ ] Verify no secret value was added to the repository.

## References

- `design.md` section:
- `IMPLEMENTATION_TODO.md` task:
- Decision register:
- Related PR, issue, or external documentation:
