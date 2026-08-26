# Robinhood Display-Only CLI

`ainvest-robinhood-read` exposes the normalized P06-T1 Robinhood read models
for local inspection. It is a display surface, not a trading-data handoff:
every successful document says `usable_for_trading=false`, and the process has
no Paper, strategy, sizing, risk, mutation, or order-submission path.

## Safety boundary

The CLI opens the independently reviewed, artifact-pinned `rh-mcp` gateway,
verifies its read projection and readiness, and calls one existing named
`RobinhoodReadClient.read_*` method. There is no generic capability argument,
raw MCP envelope, automatic retry, pagination URL handling, or fallback to
Alpaca, yfinance, or another provider. The first delivery reads one provider
page and preserves normalized `has_more`.

Provider instructions, guides, and tool/schema descriptions are discarded at
the gateway boundary. Bounded provider result text is emitted only as
JSON-escaped `UntrustedDisplayText`. Rejected text remains the stable
`[unavailable: untrusted text omitted]` marker with its
`omitted_untrusted_fields` path. This text must not be copied into a prompt or
log by a later adapter.

Partial instrument references remain `identity_verified=false`. Account-bound
commands remain `account_binding=unverified`; market-session-dependent reads
remain `session_evidence=unverified`. A quote less than the fixed 15-second
display age is still `live_eligible=false` and `session_unverified`. Values
whose pinned schema supplies no currency retain `unit=UNSPECIFIED` and
`comparable=false`; the CLI does not convert, total, rank, or label them as
USD.

## Commands

The command surface is closed:

```text
ainvest-robinhood-read status
ainvest-robinhood-read accounts
ainvest-robinhood-read portfolio [--account-number-stdin]
ainvest-robinhood-read positions [--account-number-stdin]
ainvest-robinhood-read orders [--account-number-stdin] --view open|closed [filters]
ainvest-robinhood-read quotes SYMBOL [SYMBOL ...]
ainvest-robinhood-read price-book SYMBOL [SYMBOL ...]
ainvest-robinhood-read tradability [--account-number-stdin] SYMBOL [SYMBOL ...]
ainvest-robinhood-read historicals SYMBOL [SYMBOL ...] --start-time RFC3339 [options]
ainvest-robinhood-read fundamentals SYMBOL [SYMBOL ...] [--bounds BOUNDS]
ainvest-robinhood-read financials SYMBOL [SYMBOL ...] [--period quarterly|annual] [--limit N]
```

Symbols are exact uppercase tickers. Per-call limits are 20 for quotes, 4 for
price books, 10 for tradability, 10 for historicals, 10 for fundamentals, and
20 for financials. Duplicate symbols are rejected.

Historical options are `--end-time`, a pinned `--interval`, a pinned
`--bounds`, and `--adjustment-type none|split|all`. Start and end values must be
valid RFC 3339 timestamps, and end cannot precede start. Fundamental bounds are
`regular`, `trading`, `extended`, or `24_5`. Financial `--limit` is 1 through
40 and defaults to 4.

Orders require `--view`. Optional filters are `--symbol`, `--order-id`,
`--state`, `--created-at-gte`, and `--placed-agent`. Open states are `new`,
`queued`, `confirmed`, `unconfirmed`, and `partially_filled`; closed states are
`filled`, `cancelled`, `rejected`, `failed`, and `voided`. A contradictory
view/state pair is rejected before the gateway opens.

## Account input

`portfolio`, `positions`, `orders`, and `tradability` require one account
number supplied by the operator. The value is never inferred from `accounts`,
accepted as a command-line value, persisted, normalized, displayed, or logged.

On an interactive terminal, omit `--account-number-stdin`; the CLI obtains one
non-echoed value with the operating system's password-style prompt. In a pipe
or automation, include `--account-number-stdin` and provide exactly one value
terminated by LF or EOF:

```text
secret-source | ainvest-robinhood-read portfolio --account-number-stdin
```

The non-interactive value must contain 1 through 128 visible ASCII characters
with no whitespace, controls, CR, second line, or trailing input. The flag is
rejected on a TTY, and a non-TTY without the flag is rejected. Account-input
errors never echo the rejected value.

## JSON and exit contract

Success exits 0, writes one compact UTF-8 JSON document plus LF to stdout, and
writes no CLI-owned stderr. Its exact top-level keys are:

```json
{
  "schema_version": "1.0",
  "command": "status",
  "ready": true,
  "posture": {"read_only": true, "mode": "display_only", "execution": "disabled"},
  "limitations": {
    "usable_for_trading": false,
    "identity": "not_applicable",
    "account_binding": "not_applicable",
    "session_evidence": "not_applicable"
  },
  "data": {"ready": true}
}
```

Usage and account-input errors exit 2. Startup, gateway, mapping, or rendering
failures exit 1. Every failure leaves stdout empty and writes one sanitized
JSON document plus LF to stderr. Errors carry only a stable `code` and
`retryable` boolean; they never carry arguments, account numbers, provider
messages, payloads, tracebacks, or untrusted result text. The CLI never retries
automatically.

## Current real-provider readiness

Offline fixture, contract, and integration tests do not require credentials or
network access. Ainvest pins the independently audited `rh-mcp` `v0.3.3`
artifact and its 54-entry `2026.08.22` manifest. Owner-assisted validation of
the installed artifact against the current live provider surface remains
pending and is not an offline test blocker. Never bypass or special-case a
not-ready result: any future artifact, manifest, schema, classification, or
provider-surface drift still fails closed and requires a separately reviewed
release plus a deliberate pin update.
