# Robinhood Read-Account Binding

`ainvest-robinhood-account` securely discovers and installs the one active
Agentic account used by Telegram display-only commands. It never asks the
operator to copy an account number and never prints the provider result or the
installed value.

This utility does not enable trading. It opens only ainvest's pinned read
gateway and calls the named `get_accounts` projection exactly once. It refuses
zero or multiple Agentic candidates and any inactive, malformed, or insecure
result.

## Storage contract

The sole production source is:

```text
<explicit-secrets-dir>/ROBINHOOD_READ_ACCOUNT_NUMBER
```

The file is an owner-only regular file at exact mode `0600`. Its content is
1–128 visible ASCII bytes followed by exactly one LF when provisioned. The
loader does not follow symlinks, accept case variants, or search an implicit
directory. Account assignments in the process environment, the explicit
`.env` document, YAML, argv, stdin, Telegram, or SQLite are rejected. Do not
put even a placeholder assignment for this key in the `.env` file.

Do not display the file with `cat`, paste it into an issue, or add it to Git.
The fixed-shape command result and sanitized Telegram display envelope are the
only safe validation evidence.

## Provision staging

First stop the staging `ainvest-telegram-read` process using its process
manager. Keeping the poller stopped is the authoritative safety boundary; the
database lease below is only best-effort coordination.

From the repository root, run:

```bash
./scripts/dev setup
./scripts/dev broker-install
```

Then provision the binding:

```bash
uv run --extra broker ainvest-robinhood-account provision \
  --environment staging \
  --env-file /Users/kel/.config/ainvest/staging.env \
  --secrets-dir /Users/kel/.config/ainvest/secrets \
  --database /Users/kel/.local/share/ainvest/staging.sqlite3 \
  --confirm-poller-stopped
```

Success has only this shape:

```json
{"command":"provision","environment":"staging","status":"ok"}
```

Provisioning never overwrites an existing target, even if it contains the same
value. That refusal prevents an accidental implicit rotation.

## Validate

Validation is read-only and does not require stopping the poller or opening the
SQLite database:

```bash
uv run --extra broker ainvest-robinhood-account validate \
  --environment staging \
  --env-file /Users/kel/.config/ainvest/staging.env \
  --secrets-dir /Users/kel/.config/ainvest/secrets
```

It loads the exact `0600` file, makes one fresh named account read, compares the
two values without disclosing either, and emits only:

```json
{"command":"validate","environment":"staging","status":"ok"}
```

After validation, restart the same staging poller and test `/portfolio` plus
one of `/positions`, `/orders open`, or `/tradability AAPL`. Share only the
sanitized display envelopes. Never share the account secret or raw provider
response.

## Disable or rotate

Stop the selected poller first. Disable removes only the exact account-secret
file and is idempotent when it is already absent:

```bash
uv run --extra broker ainvest-robinhood-account disable \
  --environment staging \
  --env-file /Users/kel/.config/ainvest/staging.env \
  --secrets-dir /Users/kel/.config/ainvest/secrets \
  --database /Users/kel/.local/share/ainvest/staging.sqlite3 \
  --confirm-poller-stopped
```

First-release rotation is deliberately explicit: keep the reader stopped,
run `disable`, then run `provision`, then `validate`, and only then restart the
poller. There is no rotate or overwrite command.

Production uses the same three commands with `--environment production` and
its owner-controlled paths. Production provisioning remains a separate owner
decision under `DEC-010`.

## Failure handling

Failures are fixed value-free JSON error codes on stderr. A failure never
includes paths, provider messages, account suffixes, fingerprints, digests,
candidate counts, or tracebacks. Do not work around a failure by manually
copying an account value into another source. Resolve the file mode/path,
poller stop, database lease, gateway readiness, or candidate ambiguity and
rerun the same command.
