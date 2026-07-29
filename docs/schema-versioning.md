# Schema Versioning and Compatibility

This document is the compatibility contract for independently developed strategy
plugins and other consumers of ainvest domain models. It applies to versioned
Pydantic wire schemas (`schema_version`) and to the Strategy API surface that
plugins declare against (`ainvest_strategy_api`).

Authoritative models live in `src/ainvest/schemas/`. Committed JSON Schema
snapshots live under `schemas/json/`. Contract tests under `tests/contract/`
fail the merge gate when snapshots or fixtures drift unexpectedly.

## Two version lines

| Line | Field / constant | Meaning |
|---|---|---|
| Domain payload | `schema_version` (`MAJOR.MINOR`, e.g. `1.0`) | Shape of a single domain document (`ResearchPacket`, `TradeSignal`, …) |
| Strategy API | `ainvest_strategy_api` (`MAJOR.MINOR.PATCH`, e.g. `1.0.0`) | Host capability plugins may call (context/signal contracts, hooks) |

Payload versions and Strategy API versions evolve independently. A plugin may
support Strategy API `1.x` while exchanging only payload `schema_version`
`1.0` documents.

## Domain `schema_version` (major / minor)

Format: `MAJOR.MINOR` (two non-negative integers, no patch segment).

Each generated model line accepts only the payload versions it explicitly
implements. Most current `v1` artifacts implement only
`schema_version="1.0"` and reject documents claiming another minor or major;
matching the `MAJOR.MINOR` text shape alone never makes a payload compatible.
`ApprovalChallenge` additionally implements `1.1`: the original
`ApprovalChallenge` model/artifact remains the exact `1.0` contract, the
`ApprovalChallengeV1_1` model/artifact owns `1.1`, and
`parse_approval_challenge` is the cumulative dispatcher that accepts both
implemented minors without widening `1.0` documents in place.

Support within a major is cumulative. When a backward-compatible `1.1` model is
implemented, its accepted version type becomes `Literal["1.0", "1.1"]` (or an
equivalent explicit version dispatcher), and its contract tests must prove that
every valid `1.0` fixture still validates. It must not replace the accepted set
with only `Literal["1.1"]`. An older `1.0` validator may reject a `1.1` document;
that is the forward-compatibility limitation described below. A new major uses
new models/artifacts and the explicit migration boundary described later.

Compatibility is evaluated under ainvest's fail-closed unknown-field policy
(`extra="forbid"` / exported `additionalProperties: false`). That policy makes
**forward** compatibility (old validators reading newer documents) stricter than
typical “ignore unknown fields” schema ecosystems.

### Terminology

- **Backward compatible:** newer validators still accept every document that was
  valid under the previous minor.
- **Forward compatible:** older validators still accept documents produced under
  the newer minor. ainvest does **not** provide forward compatibility when the
  wire shape grows, because unknown properties are rejected.

### Minor (backward compatible)

A minor bump (`1.0` → `1.1`) may only:

- add documentation-only JSON Schema annotations
- widen acceptance of an **existing** field so previously valid documents remain
  valid (for example raising a numeric upper bound, or accepting an additional
  enum member that old producers never emitted into stored documents)

A minor bump must **not**:

- add, remove, or rename properties
- make an optional field required
- narrow types, enums, patterns, or ranges
- change field semantics, units, unknown-field policy, or Decimal/UTC rules

Because unknown fields are forbidden, publishing a new property—even an
“optional” one with a default—is a **major** change: existing forbid-mode
consumers and exported JSON Schema validators will reject documents that include
it. Coordinated upgrades across producers and consumers do not turn that into a
minor.

### Major (breaking)

A major bump (`1.x` → `2.0`) is required for any change that can break a
conforming consumer or producer, including:

- adding, removing, or renaming a field
- making an optional field required
- narrowing types, enums, patterns, or ranges
- changing field semantics or units
- changing unknown-field policy
- changing canonical Decimal / UTC serialization rules

Breaking changes require an explicit PR that updates `schemas/json/` snapshots
and this document's migration notes. Silent drift is rejected by CI.

## Unknown-field policy

Domain models use Pydantic `extra="forbid"`.

- Unknown fields are **rejected** at the ainvest boundary (fail closed).
- Cross-language JSON Schema consumers must treat additional properties as
  disallowed (`additionalProperties: false` on exported object schemas).
- Producers outside ainvest that forward opaque extensions must place them
  outside these contracts or negotiate a versioned extension object in a later
  **major**.
- This policy is why additive wire fields cannot ship as a minor bump (see
  above).

## Deprecation window

1. Mark a field or enum member deprecated in docs and JSON Schema description
   while it remains accepted for at least **one minor** after announcement.
2. Emit structured warnings in logs/metrics when deprecated members appear
   (runtime warning hooks may land after P02-T5).
3. Remove only in a **major** bump after the deprecation window.
4. Strategy plugins must not rely on deprecated members for new behavior.

There is no silent removal inside a minor line.

## Migration boundaries

- Payload migrations are explicit: old `MAJOR` documents are not auto-upgraded
  into a new `MAJOR` by the host.
- Strategy state is bound to `strategy` + `strategy_version`. Plugin upgrades
  must not implicitly migrate in-flight state (design.md §5.3.2).
- ORM / persistence migrations (Alembic) are a separate boundary (`P02-T6+`) and
  must retain the original `schema_version` of stored JSON payloads.
- Approval digests bind protected order fields; a payload major that changes
  those fields invalidates prior approvals by construction (`P02-T4`).

## JSON Schema artifacts

- Path: `schemas/json/v{MAJOR}/<ModelName>.json`
- Generated from live Pydantic models via `ainvest.schemas.export`
- Regenerated with `./scripts/dev export-schemas`
- Checked by `./scripts/dev export-schemas --check` and contract tests (also
  part of `./scripts/dev verify` / CI)
- Valid and invalid fixtures are checked with a standards-based JSON Schema
  validator and its installed RFC 3339 `date-time` format checker, as well as
  the authoritative Pydantic model. UTC fields require an explicit `Z` or
  numeric offset; naive and malformed timestamps fail both boundaries.

Intentional breaking or additive schema edits update the committed files in the
same PR. Unintended diffs fail CI.

## Fixtures

Under `tests/contract/fixtures/<ModelName>/`:

- `valid.json` — at least one document that must validate
- `invalid_*.json` — multiple documents that must fail validation

Fixtures are the shared language for UI and other-language consumers alongside
the JSON Schema snapshots. They are deterministic generated artifacts:
`./scripts/dev export-schemas --check` fails when either schemas or fixtures
drift from the live model/example generators.

## Strategy API version ranges

Plugins declare a supported range of `ainvest_strategy_api` versions, for
example `>=1.0.0,<2.0.0`.

- The host exposes `STRATEGY_API_VERSION` in `ainvest.strategies.api`.
- Loaders must reject plugins whose declared range does not contain the host
  version (design.md §5.3.2).
- Range syntax supported in P02-T5: comma-separated clauses of
  `>=X.Y.Z`, `>X.Y.Z`, `<=X.Y.Z`, `<X.Y.Z`, or exact `X.Y.Z`.
- A major Strategy API bump may change StrategyContext / TradeSignal
  expectations even when payload `schema_version` remains `1.0`.

See `ainvest.strategies.api` for `parse_strategy_api_range` and
`strategy_api_range_contains`.
