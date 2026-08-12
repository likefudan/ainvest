# Telegram Private Notifications

P05-T4 provides an outbound-only Telegram adapter for order notifications. It
does not receive Telegram updates, decide approvals, persist delivery intents,
change proposal state, or call a Paper or Live broker.

## Security boundary

- Staging and production use separate Bot tokens and different expected
  numeric Bot IDs.
- A destination is authorized only by an exact configured
  `(user_id, private_chat_id)` pair. Usernames and the cross-product of known
  IDs are not authorization inputs.
- Before sending, the adapter requires an exact `getMe.id` match and verifies
  that the configured target is the exact private chat.
- PAPER messages carry only a P05-T0 opaque callback nonce. LIVE messages carry
  only an already-created HTTPS link at the configured fixed WebAuthn origin.
- Message delivery is not approval. A Telegram response, callback, message ID,
  timeout, or delivery result changes no application state.
- `sendMessage` is attempted at most once per adapter call. A timeout,
  disconnect, or cancellation after the attempt begins returns
  `delivery_unknown` with `retryable=false`.

The message is deterministic plain text (`parse_mode=None`), limited to 3,500
Unicode code points, and includes the complete server-owned order, currency,
time-in-force, expiry, strategy version, and approved risk summary. Protected
fields are never truncated or inferred. Invalid bindings, controls, unsafe
links, or oversized messages fail before delivery.

## Configuration

Both environments remain disabled until proposed `DEC-010` is accepted and
the owner provisions real values outside Git. The deployable nested keys are:

```text
TELEGRAM_STAGING__ENABLED
TELEGRAM_STAGING__EXPECTED_BOT_ID
TELEGRAM_STAGING__ALLOWED_RECIPIENTS
TELEGRAM_STAGING__BOT_TOKEN

TELEGRAM_PRODUCTION__ENABLED
TELEGRAM_PRODUCTION__EXPECTED_BOT_ID
TELEGRAM_PRODUCTION__ALLOWED_RECIPIENTS
TELEGRAM_PRODUCTION__BOT_TOKEN
```

`ALLOWED_RECIPIENTS` is a JSON array of bound records:

```json
[{"user_id": 900000101, "private_chat_id": 900000201}]
```

The numbers above are synthetic documentation values. Do not copy them into a
real deployment.

Tokens may be supplied through environment/dotenv configuration or the two
exact file-secret names `TELEGRAM_STAGING__BOT_TOKEN` and
`TELEGRAM_PRODUCTION__BOT_TOKEN`. File-secret discovery is never implicit:

```python
from ainvest.config import load_settings

settings = load_settings(secrets_dir="/explicit/protected/directory")
```

Source priority remains explicit overrides, environment, dotenv, file secrets,
YAML, then fail-closed defaults. The notification sender receives validated
`Settings`; it never reads a file or environment variable.

## Offline and owner-assisted validation

Automated tests use synthetic identities, fake tokens, and an injected fake
transport. They make no Telegram network call. After owner values are
provisioned, environment validation must separately confirm:

1. staging and production `getMe.id` values exactly match their configured Bot
   IDs;
2. each configured destination is the expected private chat;
3. only synthetic non-trading notifications are sent during validation; and
4. tokens, callback nonces, links, and account identifiers do not enter logs.

Until that owner-assisted check is complete, real Telegram integration remains
disabled and unverified. `DEC-010` therefore remains proposed.
