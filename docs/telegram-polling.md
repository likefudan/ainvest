# Telegram long polling ingress

P05-T5 provides the transport-neutral Telegram ingress boundary for the
first-release, private-chat workflow. It classifies inputs and delegates typed
authorized updates to an injected handler. It does not interpret approval
callbacks, answer portfolio queries, call an LLM, or contact a broker.

## Runtime contract

- Each process validates `getMe` against the configured numeric bot ID before
  acquiring a polling lease.
- Staging and production use separate bot tokens, recipient bindings, cursor
  rows, and leases. There is no fallback between environments.
- `getUpdates` is bounded to a 25-second long poll, a 100-update batch,
  `message` and `callback_query` updates, and a 35-second outer deadline.
- Authorization requires an exact configured `(user_id, private_chat_id)` pair
  and a Telegram `private` chat. Pair fields are never combined as independent
  allowlists.
- One 75-second owner/epoch lease fences multiple workers. It is renewed after
  polling, before every authorized handler call, and before every terminal
  database write. Handler execution is bounded to 20 seconds and leaves at
  least a 10-second commit margin.
- A terminal processed marker and `next_offset = max(current, update_id + 1)`
  commit in one transaction. Gaps are accepted, exact batch duplicates are
  collapsed, conflicting duplicates stop the poller, and callback-query IDs
  are deduplicated using a domain-separated SHA-256 digest.
- Handler failures and `retry_later` preserve the cursor, release the lease,
  and use a processing backoff independent from network and rate-limit delays.
  Shutdown interrupts delays and does not start another poll.

## Data minimization

The two ingress tables contain only cursor/lease fields and terminal update
metadata. They never store raw Telegram payloads, message text, callback data,
callback-query IDs, user IDs, chat IDs, message IDs, bot tokens, nonces, or
business results. The typed input models redact all human input and identities
from their representations. Retention is intentionally deferred; deleting
terminal markers without a coordinated offset policy would break durable
deduplication.

## Deployment note

All automated coverage uses offline transports and synthetic identities. The
owner-assisted real-bot check remains pending under proposed DEC-010: validate
identity, verify an exact private recipient pair, confirm restart resumes at the
durable offset, and confirm staging and production do not cross.
