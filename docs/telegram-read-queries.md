# Telegram Robinhood Read Queries

`ainvest-telegram-read` exposes the normalized Robinhood Non-Trading Preview
to one configured private Telegram Bot environment. It is display-only: it
cannot approve, mutate, submit, cancel, size, or route an order, and every data
reply is marked `READ ONLY - NOT FOR TRADING`.

## Run the poller

Provision and validate the selected Bot first, stop any other poller for that
environment, and migrate the SQLite database through the current Alembic head.
Then install the `approval` and `broker` extras and run:

```text
ainvest-telegram-read \
  --environment staging \
  --database /absolute/path/to/ainvest.sqlite3 \
  --env-file /absolute/path/to/.env \
  --secrets-dir /absolute/path/to/secrets
```

`--environment` and `--database` are required. The database must already exist,
be a regular SQLite file, and contain the current migrations; the command never
creates or migrates it. SIGINT and SIGTERM stop polling, finish bounded cleanup,
release the polling lease, and close the database engine. Live trading mode is
rejected.

The long-running process initializes one `python-telegram-bot` Bot and its two
HTTP clients, then reuses that same client for Bot identity, polling, and reply
delivery. Normal stop, startup failure, polling failure, and cancellation close
the owned clients exactly once.

The selected Telegram Bot must be enabled with its exact Bot ID, token, and at
least one bound numeric `(user_id, private_chat_id)` pair. P05-T5 silently
discards every non-private, unbound, forwarded, edited, callback, wrong-Bot, or
otherwise unauthorized update before this query adapter runs.

## Account-bound reads

`/portfolio`, `/positions`, `/orders`, and `/tradability` require the exact
file-secret:

```text
<secrets-dir>/ROBINHOOD_READ_ACCOUNT_NUMBER
```

The file contains 1–128 visible ASCII characters, optionally followed by one
LF. CRLF, whitespace, controls, extra lines, invalid UTF-8, symlinks, and
oversized values are rejected. The chat cannot provide or select this value.
It is revealed only inside the named account-bound display call and never
appears in a reply, log, database row, snapshot, or error.

Account configuration is resolved lazily and independently from global startup
configuration. Its precedence is explicit value, environment, dotenv, exact
file secret, then an injected YAML value. Missing and invalid sources become
the fixed `account_secret_missing` and `account_secret_invalid` replies only
for the four account-bound commands. They cannot block `/help`, `/rh_status`,
`/accounts`, `/quotes`, `/pricebook`, `/history`, `/fundamentals`, or
`/financials` and cannot open the gateway for an account-bound command.

## Exact commands

```text
/help
/rh_status
/accounts
/portfolio
/positions
/orders open [SYMBOL]
/orders closed [SYMBOL]
/quotes SYMBOL [SYMBOL ...]
/pricebook SYMBOL [SYMBOL]
/tradability SYMBOL [SYMBOL ...]
/history SYMBOL 1d|5d|1m|3m|1y
/fundamentals SYMBOL [regular|trading|extended|24_5]
/financials SYMBOL [quarterly|annual] [1|2|3|4]
```

Commands are case-sensitive visible ASCII with exactly one space between
tokens. Symbols are exact uppercase ainvest `Symbol` values. Quoting, flags,
Bot mentions, free text, duplicate symbols, and extra arguments are rejected.
`/help` is static and never opens the gateway.

Each authorized user receives at most six total reply attempts per 60-second
process-local window, with only one query in flight. Excess updates are silent
and terminal. The limiter resets on process restart and is abuse control, not
authorization. A gateway phase has a 12-second budget, the one Telegram send
attempt has a 4-second budget, and the enclosing P05-T5 handler retains a
4-second unwind margin within its 20-second deadline.

Replies are one plain message (`parse_mode=None`) of at most 3,500 Unicode code
points. Oversized results are replaced by a bounded error rather than split or
truncated. Compact JSON is ASCII-escaped at this boundary, so provider-owned
Unicode format, bidi, control, and separator characters never reach Telegram
raw. A crash after a send but before P05-T5 commits the update can replay
the read and reply; delivery is intentionally bounded at-least-once and no
Telegram `message_id` is persisted.

This query-only runner parks every authorized callback by returning
`RETRY_LATER`: it sends no reply, writes no terminal/digest row, and does not
advance the offset. That deliberately blocks later updates with bounded P05-T5
backoff until a future P05-T1 composite callback router is deployed. The
availability cost is preferable to irreversibly consuming an approval callback
in the display-only process.

Real Bot and Robinhood reads remain owner-assisted validation. Offline tests
use fake transports, synthetic identifiers, a synthetic account reference,
and no public network.
