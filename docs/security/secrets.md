# Secrets, identities, and least-privilege access

Status: provider-neutral boundary implemented by `P08-T7`
Related: `design.md` §11, [`data-flow.md`](data-flow.md),
[`threat-model.md`](threat-model.md), `DEC-003`, `DEC-009`, `DEC-010`,
`DEC-015`, `DEC-017`, and `DEC-018`

This document defines how ainvest code refers to secrets and which service
identities may read them. It does not select a cloud, production secret
manager, account, or IAM principal. Those choices remain owner-controlled, and
their absence keeps production secret access unavailable.

## Invariants

1. Code requests a stable logical `SecretId`; it never embeds a credential or
   provider-specific resource name.
2. Each `SecretAccessService` is permanently bound to exactly one
   `ServiceRole` at construction. `get` and `probe` do not accept a role, so a
   request cannot impersonate another identity per call. The boundary retains
   only that role's configured references. Unknown construction roles, unknown
   identifiers, unconfigured references, and cross-role requests fail closed.
3. A presence probe returns only role, logical identifier, and status. It does
   not fetch, serialize, log, audit, or return the secret or provider reference.
4. A successful read returns `SecretValue`. Plaintext is available only through
   an explicit `reveal()` call after authorization. Its `str` and `repr` are
   redacted, and common copy, pickle, and JSON serialization paths are blocked.
   The access boundary accepts only the exact runtime `SecretValue` type; a
   subclass cannot override `reveal`, `str`, or `repr` and pass validation.
   `SecretRef`, development/in-memory providers, and the access service also
   block copying and serialization so their private state cannot become an
   accidental snapshot.
5. Provider exceptions are replaced by sanitized access errors without
   propagating provider messages, exception chains, or values.
6. Strategy workers receive none of these credentials. Their environment
   scrubber removes role credential keys before the child starts, and the
   worker has no secret-provider handle.
7. Secret access does not grant a runtime capability. Runtime mode and live
   gates independently decide whether a read or write client may be
   constructed.

## Role matrix

| Service role | Allowed logical secrets | Explicitly excluded |
|---|---|---|
| Research | OpenAI API, external research-provider credential, Research-scoped database credential | Telegram/WebAuthn, Robinhood read/write, Approval database |
| Approval | Telegram Bot, Telegram webhook, WebAuthn server, Approval-scoped database credential | OpenAI, research provider/database, Robinhood read/write |
| Read Broker | Robinhood read credential only | Robinhood write and every Research/Approval secret |
| Write Broker | Robinhood write credential only | Robinhood read and every Research/Approval secret |

The Read Broker does not lend its credential or raw MCP session to Research or
Strategy. Research consumes versioned results from the future Read Gateway.
The Write Broker remains unavailable to Paper and the Read-only Preview; this
module alone cannot enable it.

## Provider boundary

`SecretProvider` has two operations:

- `probe(ref)` uses reference/key metadata only and returns `present`,
  `missing`, `permission_denied`, or `unavailable` without fetching or
  truth-testing a value.
- `read(ref)` returns a `SecretValue` or raises a sanitized
  `SecretProviderError`.

Implementations must apply provider-side IAM in addition to the application
role policy. Application authorization is defense in depth, not a replacement
for separate workload identities and provider permissions.

Provider statuses are accepted only when they are exact known enum values.
Foreign, unhashable, or otherwise hostile statuses become `provider_error`.
Reference registries and provider entry points likewise accept only the exact
runtime `SecretRef` type, before reading either of its properties; subclasses
and duck-typed objects cannot substitute hostile reference behavior.
Provider exceptions, including exceptions with hostile status properties, are
translated after leaving the provider exception context; neither the cause nor
the context is retained in the public `SecretAccessError`.

The repository includes only:

- `MemorySecretProvider`, a deterministic mutable fake for tests and offline
  development;
- `DevelopmentEnvironmentSecretProvider`, which reads only a mapping explicitly
  supplied by its caller. Process-environment access requires the explicit
  `from_process_environment(..., deployment_environment="development")`
  factory; and
- `UnavailableProductionSecretProvider`, the fail-closed production
  placeholder while the production platform and secret manager are undecided.

There is no production provider fallback to `.env`, process environment,
development files, or the in-memory fake. A future provider selected under
`DEC-015` must be implemented and reviewed with its workload-identity/IAM
configuration before production use.

## Development loading

The existing configuration loader owns `.env` and file-secret parsing and
precedence. Secret access does not add a second parser or implicitly search the
working directory. Development composition must:

1. opt in to the existing uncommitted `.env` path explicitly when loading
   settings;
2. bind stable provider-neutral references to development environment keys;
3. construct `DevelopmentEnvironmentSecretProvider` with the explicit
   `development` label; and
4. construct a separate `SecretAccessService(role, provider, references)` for
   the process identity; and
5. inject that already-bound service only into the process that owns the role.

`.env`, credentials, provider resource IDs, account IDs, and resolved values
must never be committed. Repository examples may contain empty placeholders
only.

## Startup checks and failures

For every secret required by the selected non-live capability, startup calls
`probe` and accepts only `available`. The following remain value-free:

- `denied`: the role is not allowed to request the logical secret;
- construction rejects an unknown role without echoing the input;
- `unknown_secret`: an invalid logical identifier was requested;
- `reference_unconfigured`: no provider-neutral binding exists;
- `missing`: the provider permits the reference but no value is present;
- `provider_permission_denied`: provider IAM rejected it;
- `provider_unavailable`: the provider or production integration is absent;
- `provider_error`: an unexpected provider failure.

Health, logs, traces, and audit events may record only that status metadata.
They must not record `SecretValue`, the result of `reveal()`, provider
exceptions, environment values, or provider references.

## Rotation

Configuration binds a logical `SecretId` to a stable provider reference.
Rotation replaces the value behind that same reference. The application code,
role policy, and logical identifier do not change. Long-lived clients must
acquire a fresh value when their provider-specific authentication lifecycle
requires it and discard the plaintext promptly; they must not cache plaintext
in settings, audit rows, logs, traces, or strategy requests.

Production rotation procedures, overlapping versions, revocation, and
workload-identity validation depend on the future provider selected under
`DEC-015`. Until then, the production provider is deliberately unavailable.

## Integration checklist for later tasks

- Use only an approved logical identifier and role.
- Probe at startup without printing the provider reference or value.
- Inject the access service; do not read environment variables directly.
- Reveal only at the provider client boundary and discard promptly.
- Never include a revealed value in an exception, log field, audit payload,
  metric label, trace attribute, model prompt, Telegram message, or worker
  request.
- Test missing, provider-denied, unavailable, rotated, and cross-role cases.
- Keep Robinhood read and write provider references, workload identities,
  clients, and processes distinct.
