# Telegram Private Notifications

P05-T4 provides an outbound-only Telegram adapter for order notifications. It
does not receive Telegram updates, decide approvals, persist delivery intents,
change proposal state, or call a Paper or Live broker.

## Security boundary

- Staging and production use separate Bot tokens and different expected
  numeric Bot IDs.
- A destination is authorized only by an exact configured
  `(user_id, private_chat_id)` pair. Usernames and the cross-product of known
  IDs are not authorization inputs. The same owner/pair may be separately
  bound through both Bots; isolation comes from the selected Bot environment,
  not a requirement to create a second Telegram user account.
- Before sending, the adapter requires an exact `getMe.id` match and verifies
  that the configured target is the exact private chat.
- PAPER messages carry only a P05-T0 opaque callback nonce. LIVE messages carry
  only an already-created HTTPS link at the configured fixed WebAuthn origin.
- Message delivery is not approval. A Telegram response, callback, message ID,
  timeout, or delivery result changes no application state.
- `sendMessage` is attempted at most once per adapter call. A timeout,
  disconnect, or cancellation after the attempt begins returns
  `delivery_unknown` with `retryable=false`. Unexpected exceptions and an
  unusable post-send response are equally unknown; `delivery_failed` is
  reserved for a definitive Telegram API rejection.

The message is deterministic plain text (`parse_mode=None`), limited to 3,500
Unicode code points, and includes the complete server-owned order, currency,
time-in-force, expiry, strategy version, and approved risk summary. Protected
fields are never truncated or inferred. Invalid bindings, controls, unsafe
links, or oversized messages fail before delivery. Human-visible dynamic text
and links reject Unicode control/format characters, including bidi controls,
C0/C1 controls, and line/paragraph separators; ordinary international text and
HTML-special characters remain literal plain text.

## Configuration

Both environments remain disabled until proposed `DEC-010` is accepted and
the owner provisions real values outside Git. The deployable non-secret nested
keys are:

```text
TELEGRAM_STAGING__ENABLED
TELEGRAM_STAGING__EXPECTED_BOT_ID
TELEGRAM_STAGING__ALLOWED_RECIPIENTS

TELEGRAM_PRODUCTION__ENABLED
TELEGRAM_PRODUCTION__EXPECTED_BOT_ID
TELEGRAM_PRODUCTION__ALLOWED_RECIPIENTS
```

`ALLOWED_RECIPIENTS` is a JSON array of bound records:

```json
[{"user_id": 900000101, "private_chat_id": 900000201}]
```

The numbers above are synthetic documentation values. Do not copy them into a
real deployment.

The P05-T10 provisioner fixes tokens to the two exact file-secret names
`TELEGRAM_STAGING__BOT_TOKEN` and `TELEGRAM_PRODUCTION__BOT_TOKEN` only.
Although lower-level Settings precedence still recognizes legacy
environment/dotenv token input, the provisioning utility rejects every
case-variant nested token assignment and every top-level Telegram JSON object
whose case-variant/alias `bot_token` could populate either environment. It
never writes or automatically deletes such an assignment; it fails with a
value-free instruction to remove the plaintext secret manually and retry.
File-secret discovery is never implicit:

```python
from ainvest.config import load_settings

settings = load_settings(secrets_dir="/explicit/protected/directory")
```

Source priority remains explicit overrides, environment, dotenv, file secrets,
YAML, then fail-closed defaults. P05-T10 file-only validation therefore uses an
empty injected environment and proves only the explicit files it inspects; the
deployment separately enforces that the launched process has no ambient nested
token or top-level Telegram JSON override. The notification sender receives
validated `Settings`; it never reads a file or environment variable. At the
existing file-secret layer, no top-level Telegram JSON file, case variant, or
alternate nested filename may provide a Bot token. Malformed, oversized,
non-UTF-8, or unreadable exact token files fail with a stable redacted
configuration error. The provisioner additionally requires the exact target to be
non-symlinked, regular, and `0600` before validation accepts it. The raw file
may contain the token bytes exactly or add one terminal LF. Leading/trailing
spaces, tabs, CRLF, repeated LF, and every other control/whitespace variation
are rejected rather than normalized.

## Operator provisioning workflow

Install the `approval` deployment profile, then use only the dedicated
`ainvest-telegram-provision` executable. It has exactly four commands. Every
invocation selects one environment and supplies explicit configuration and
secret paths:

```text
ainvest-telegram-provision add --environment staging \
  --env-file /protected/ainvest.env --secrets-dir /protected/secrets \
  --database /protected/ainvest.sqlite3 --confirm-poller-stopped

ainvest-telegram-provision validate --environment staging \
  --env-file /protected/ainvest.env --secrets-dir /protected/secrets

ainvest-telegram-provision rotate-token --environment staging \
  --env-file /protected/ainvest.env --secrets-dir /protected/secrets \
  --database /protected/ainvest.sqlite3 --confirm-poller-stopped

ainvest-telegram-provision disable --environment staging \
  --env-file /protected/ainvest.env --secrets-dir /protected/secrets \
  --database /protected/ainvest.sqlite3 --confirm-poller-stopped
```

Before `add`, `rotate-token`, or `disable`, stop that environment's poller with
the deployment/process manager and verify it is stopped. The acknowledgement
flag records that manual step; the utility neither stops nor starts services.
Its database lease is only best-effort detection of a conforming worker and is
not an atomic fence for filesystem writes. If an operation fails, keep the
poller stopped, inspect the fixed error code, and retry only after correcting
the cause. `add` and `rotate-token` require the selected environment to be
disabled; both activate only as their final write. `disable` retains the Bot
identity, recipient binding, and token.

Create the Bot manually in BotFather, send it a private message, and run `add`.
The token prompt uses a controlling TTY without echo. Discovery displays only
numeric user/private-chat pairs; select one and confirm the exact
`user_id:private_chat_id` value. Discovery deliberately omits a Telegram update
offset, so it does not acknowledge messages or advance the durable poller
cursor. The utility requires `getMe` identity, an empty webhook URL, and an
exact private `getChat` result before activation. It never deletes a webhook.

`validate` inspects only the explicit files with an empty injected process
environment and performs no configuration, secret, database, cursor, or
provider mutation. It sends no message by default. Add `--send-test` only when
one fixed, plain-text test delivery is desired; a started send is never retried.
Run the same workflow separately for production. The Bots and token bytes must
differ, but the same owner and private-chat pair may be bound independently
through both Bots.

## Offline and owner-assisted validation

Automated tests use synthetic identities, fake tokens, and an injected fake
transport. They make no Telegram network call. After owner values are
provisioned, environment validation must separately confirm:

1. staging and production `getMe.id` values exactly match their configured Bot
   IDs;
2. each configured destination is the expected private chat;
3. only synthetic non-trading notifications are sent during validation;
4. tokens, callback nonces, links, and account identifiers do not enter logs;
5. the deployment/process manager stopped the target poller before each
   state-changing P05-T10 operation and the operator explicitly acknowledged
   that stop; this is the sole authoritative exclusion boundary, while the
   database lease is only best-effort coordination and cannot atomically fence
   a filesystem replace. First acquisition may create the payload-free
   poll-state row at offset zero, but an existing offset remains exact and no
   processed update is written; and
6. the launched service has no ambient Telegram token override.

Until that owner-assisted check is complete, real Telegram integration remains
disabled and unverified. `DEC-010` therefore remains proposed.
