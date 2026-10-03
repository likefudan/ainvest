# Integration contracts and audit queries

These are **data contracts**, not permission to invoke broker actions. Current
runtime remains Paper / display-only; passing JSON Schema validation does not
authorize approval, operator access or trading.

## Published artifacts

| Contract | Artifact / source | Use |
| --- | --- | --- |
| Research, signals, portfolio, orders, risk, approval, broker results, market data | [Core manifest](../../schemas/json/v1/MANIFEST.json), [core schemas](../../schemas/json/v1/) | Cross-language structural validation; exact model versions listed in each artifact. |
| Internal commands | [WorkflowCommand.json](generated/WorkflowCommand.json) | Versioned in-process orchestration messages; no HTTP execution endpoint. |
| Internal result events | [WorkflowEvent.json](generated/WorkflowEvent.json) | Correlation, causation, idempotency and business outcomes. |
| States, edges, recovery, command/event mappings and codes | [catalog.json](generated/catalog.json) | Generated from implementation enums, mappings and literal approval-service codes. |
| Synthetic command and result | [examples.json](generated/examples.json) | A sizing message pair, not a submitted order or real account data. |

Core fixtures, including valid and invalid payloads, are under
[tests/contract/fixtures](../../tests/contract/fixtures/).
The catalog covers workflow outcomes, Broker error taxonomy, approval service,
Telegram approval and approval handoff. It is not a universal registry of every
provider-specific `reason_code`; do not infer success from an unknown code.

### Validate without importing ainvest models

Use a JSON Schema **Draft 2020-12** validator with RFC 3339 date-time format
checking enabled. For example, from a reviewed checkout after development setup:

```bash
uv run --locked python - <<'PY'
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

root = Path("docs/api/generated")
schema = json.loads((root / "WorkflowCommand.json").read_text())
payload = json.loads((root / "examples.json").read_text())["WorkflowCommand"]
Draft202012Validator.check_schema(schema)
checker = FormatChecker()
assert "date-time" in checker.checkers
Draft202012Validator(schema, format_checker=checker).validate(payload)
print("structurally valid; not execution authorization")
PY
```

The files contain their own `$defs`; validation needs no provider connection.
Some business rules cannot be expressed in these schemas: authorization,
current expiry, order-hash validity, matching identities, risk/session evidence,
and cross-field Python validators still run at the authoritative boundary.
For example, an `EvaluateRiskCommand` requires a candidate or proposal at runtime;
that model validator is not implied by JSON Schema's optional properties.

## Compatibility, numbers and time

Follow [schema versioning](../schema-versioning.md), not a blanket “any 1.x” rule.
Most artifacts accept only `schema_version="1.0"`. Approval challenge `1.0` and
`1.1` have separate artifacts; `parse_approval_challenge` explicitly dispatches
both. Domain MAJOR.MINOR versions are independent of the Strategy API's SemVer.
Unknown properties are rejected. Under that policy, even adding an optional wire
field is a major change; widening an existing field may be a minor change.

Money/quantity fields use decimal **strings**, e.g. `"12.5"`, never binary JSON
numbers. Scientific notation, NaN and Infinity are not valid wire values.
Python uses bounded `Decimal` and canonical normalization; do not compute order
hashes by ad-hoc float formatting. Use the authoritative order-hash helpers.
JSON Schema captures wire shape, but not every Decimal precision/range check.

Timestamps are timezone-aware. Numeric offsets are accepted and normalized to
UTC; serialized output uses `Z`. Naive or malformed times fail. Keep
`observed_at`, `received_at`, `as_of` and event time distinct; serialization does
not prove freshness or a valid market session.

## Commands, idempotency and state

`correlation_id` identifies the workflow; `causation_id` identifies the immediate
parent; `idempotency_id` identifies one intended effect. Keep that effect's key
on retry. A result uses the command's correlation/idempotency IDs and records
the command ID as its causation. See [command definitions](../../src/ainvest/workflow/commands.py)
and [dispatcher](../../src/ainvest/workflow/dispatcher.py).

The dispatcher digest excludes only attempt-scoped `command_id` and `issued_at`.
Same key plus same digest replays the stored event; a changed body conflicts.
Changing correlation, actor or business payload is not a harmless retry.
The default store is process-local, **not crash-safe or a distributed lock**.
Persistent deployments need the durable idempotency/outbox composition.

| Retry class | Rule |
| --- | --- |
| `PURE_RETRYABLE` | Retry deterministic/durable-idempotent local work with the same business identity; approval still checks nonce, expiry and atomic consumption. |
| `READ_ONLY_EXTERNAL` | Bounded read retries may be appropriate; reconciliation's local state updates remain transactional. |
| `BROKER_WRITE` | Never blindly submit/cancel again after uncertainty, and never evade this by minting a new key. |

The [generated catalog](generated/catalog.json) is the exact edge/terminal-state
reference. `SUBMIT_UNKNOWN` can only enter `RECONCILING`; `CANCEL_UNKNOWN` can
only enter `CANCEL_RECONCILING`. Insufficient evidence enters manual review.
The cancel command has its own state machine while the order may continue to
fill. Terminal states do not advance through ordinary transitions; stale
expected-state updates fail closed and persistence plus audit is atomic.
There is no in-place replacement: cancel, then new proposal/risk/hash/approval.

Branch on machine codes or typed exceptions, not human text. Broker `TIMEOUT`
is for read/preflight failure; a possibly applied write uses `UNKNOWN_OUTCOME`.
`REJECTED` means confirmed rejection, not an inferred rejection after disconnect.
State-machine `IllegalTransitionError`, `StaleStateError`, `PersistenceError`
and dispatcher `DuplicateCommandError`, `UnknownCommandHandlerError`,
`BlindBrokerRetryError` are local exception classes, not HTTP status codes.

## Approval transport: no published HTTP routes today

The current [API package](../../src/ainvest/api/__init__.py) has no public
FastAPI application or approved route set, so no OpenAPI document is published.
Do not invent `/execute`, `/cancel`, `/approve` or anonymous audit endpoints from
the internal command schemas. An actor string in a command is not authenticated
operator identity, even if its shape passes model validation.

Paper approval uses single-instance Telegram long polling, not a public webhook.
Its callback contains an opaque one-time nonce; the server validates numeric
private user/chat, original message, proposal, order hash and expiry atomically.
Only `telegram + paper` is authorized. Ordinary approval text does nothing.
See [polling](../telegram-polling.md), [notifications](../telegram-notifications.md)
and [Gate 3 evidence](../releases/phase-3-acceptance.md).
HTTPS/Passkey endpoints and production operator identity remain deferred under
DEC-015/016/018. Approval state or provider `ready=true` is never Live authority.

## Redacted audit query recipe

[audit_query_example.py](audit_query_example.py) is executable documentation,
not an installed CLI, remote service or identity provider. It takes an injected
`AuditService` and **requires** `authorize(selector, identifier)` to return exactly
boolean `True` before any query. False, None, other values and exceptions deny
access. The caller must bind that gate to a verified operator identity,
environment, scoped audit-read authorization, reason and audited access decision.
A no-op function or user-supplied actor/role string is not authentication.
Missing production identity configuration leaves remote audit access disabled.

Inside an already authorized Unit of Work, call:

```python
rows = query_timeline(
    audit,
    selector="proposal",  # or "correlation"
    identifier=authorized_subject_id,
    authorize=require_authenticated_scoped_audit_access,
    key=dedicated_redaction_key,
)
```

These names are composition inputs, not suggested credentials or a turnkey
authentication implementation. `dedicated_redaction_key` is `SecretBytes` with
at least 32 secret bytes, separately provisioned; never reuse a Bot/OAuth token.

Proposal selection calls `timeline_for_proposal` (exact `order_proposal` subject);
correlation selection calls `list_by_correlation` (possibly multiple subjects).
Neither is guaranteed to include records whose producer omitted that linkage.
Repository order is occurrence time then database insertion ID. The projection
preserves sequence positions but intentionally omits raw times and identifiers.
Only known event/state enums and keyed opaque references survive; unknown event
types become `OTHER`. Payloads, actor/account IDs, raw before/after dictionaries,
error text and digests are not exported. References preserve event/causation links
within the same key scope; changing the key changes references. Redaction is
data minimization, not proof that exported operational metadata is public.

The underlying repository currently materializes all matches: this example is
for bounded local investigations. A remote deployment additionally needs scoped
pagination, query limits, access auditing, secure sessions and rate limits under
P08-T14; do not expose this helper directly. Tests use only a temporary synthetic
database and a fake authorization gate, never the staging database.

## Regeneration and CI

From repository root after `./scripts/dev setup`:

```bash
./scripts/dev export-schemas --check
uv run --locked python scripts/export_api_reference.py
uv run --locked python scripts/export_api_reference.py --check
./scripts/dev verify
```

Review generated changes together with the implementation/version changes.
Canonical contract tests compare every generated byte and artifact name with
the source, validate the examples with a standards-based validator, reject
invalid payloads, and retain unknown-write recovery rules. Integration tests
prove denial-before-query and lossy redaction for both selectors. No CI workflow
or runtime endpoint needs to change to enforce this documentation contract.
