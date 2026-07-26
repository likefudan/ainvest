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

### Minor (compatible)

A minor bump (`1.0` → `1.1`) may:

- add optional fields with safe defaults
- widen a value domain in a backward-compatible way (e.g. allow an additional
  enum member that old producers never emit)
- add documentation-only JSON Schema annotations

Consumers that validate with the previous minor **must** continue to accept
documents produced under the new minor when unknown fields are stripped by the
producer, and producers **must not** require new fields from older consumers.

### Major (breaking)

A major bump (`1.x` → `2.0`) is required when a change can break a conforming
consumer or producer, including:

- removing or renaming a field
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
- Cross-language JSON Schema consumers should treat additional properties as
  disallowed for exported schemas (`additionalProperties: false` where
  emitted).
- Producers outside ainvest that forward opaque extensions must place them
  outside these contracts or negotiate a versioned extension object in a later
  major.

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

Intentional breaking or additive schema edits update the committed files in the
same PR. Unintended diffs fail CI.

## Fixtures

Under `tests/contract/fixtures/<ModelName>/`:

- `valid.json` — at least one document that must validate
- `invalid_*.json` — multiple documents that must fail validation

Fixtures are the shared language for UI and other-language consumers alongside
the JSON Schema snapshots.

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
