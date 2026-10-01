# Phase 3 Acceptance — Telegram Paper-Only Secure Approval (Gate 3)

**Decision / task:** `P05-T8`  
**Automated gate status:** **passed**  
**Release acceptance status:** **pending owner-assisted iPhone rehearsal**  
**Acceptance candidate date:** 2026-10-01  
**Acceptance baseline:** `567b72b769d917487be363a3b76f61f48a6a8fd9`

## Scope

Gate 3 proves one deliberately narrow authority path:

1. a proposal is created with `account_scope=paper`;
2. a one-time Telegram challenge is bound to one private user, private chat,
   message, environment, proposal, order hash, expiry, and opaque callback;
3. one valid private callback atomically creates a
   `method=telegram`, `scope=paper` approval, audit record, and durable outbox;
4. the handoff reloads trusted database records, rejects changed scope or
   content, emits one workflow command, and reaches only `PaperBroker`; and
5. one injected market event produces one simulated Paper fill.

This gate grants no Live authority. It adds no public approval endpoint,
Robinhood write client, network fallback, or real-money capability. Passkey,
HTTPS/WebAuthn deployment, and Live execution remain outside Gate 3.

## Automated acceptance harness

`tests/gates/test_phase_3_acceptance.py` composes the real approval and Paper
boundaries with synthetic identities and an injected market event. It proves:

- private callback → atomic approval/audit/outbox → durable handoff → workflow
  dispatcher → one `PaperBroker` fill;
- replay after the consumed outbox creates no second workflow or funds effect;
- a persisted Telegram approval forged to `scope=live` is policy-rejected
  before the execution port is called; and
- the Gate 3 composition contains neither a public HTTP approval route nor a
  Robinhood/rh-mcp write path.

Run the standalone acceptance harness with:

```bash
uv run --locked pytest tests/gates/test_phase_3_acceptance.py -q
```

Run the complete evidence slice with:

```bash
uv run --locked pytest \
  tests/gates/test_phase_3_acceptance.py \
  tests/unit/approval/test_telegram_approval.py \
  tests/unit/approval/test_handoff.py \
  tests/contract/approval/test_telegram_updates_contract.py \
  tests/integration/approval/test_telegram_polling.py \
  tests/faults/test_telegram_approval.py \
  tests/faults/test_execution.py \
  tests/integration/test_paper_flow.py -q
```

The repository merge gate remains:

```bash
./scripts/dev setup
./scripts/dev verify
```

Observed automated candidate results on 2026-10-01:

| Gate | Result |
| --- | --- |
| Gate 3 evidence slice | 82 passed |
| Rehearsal affected regression slice | 63 passed |
| Strict mypy | 278 source files passed |
| Unit | 1610 passed |
| Contract | 210 passed |
| Integration | 53 passed |
| Aggregate | 1891 passed |
| Branch coverage | 86.93% |

## Threat and misuse matrix

| Attempt or fault | Required result | Evidence |
| --- | --- | --- |
| Expired nonce | terminal `EXPIRED`; no execution outbox | `test_expired_callback_has_no_execution_outbox_and_replay_is_terminal` |
| Concurrent double-click | one `APPROVED`, one `ALREADY_USED`, one outbox | `test_concurrent_double_click_has_exactly_one_approval` |
| Changed nonce/order binding | `INVALID`; no approval, audit, or outbox | `test_binding_mismatch_or_tampered_nonce_creates_no_business_rows` |
| Plain `approve` text | terminally handled without approval | `test_plain_text_never_approves_and_answer_failure_is_terminal` |
| Wrong environment, user, chat, or message | `INVALID`; zero business rows | binding-mismatch parameter matrix |
| Group/channel or forwarded input | rejected by normalization/authorization before handler | Telegram update contract suite |
| Spoofed callback payload | digest mismatch; zero business rows | binding-mismatch parameter matrix |
| Poller restart | durable offset resumes without replaying handled effect | Telegram polling integration suite |
| Repeated or out-of-order update | one handled callback/effect | P08-T13 `test_repeated_out_of_order_telegram_updates_have_one_effect` |
| Database/outbox rollback | no partial approval/audit/outbox | `test_outbox_failure_rolls_back_approval_and_audit` |
| Handoff crash/redelivery | stable identity; one final funds effect | P08-T13 approval recovery test |
| Forged `telegram+live` record | outbox policy-rejected; execution calls = 0 | Gate 3 live-forgery test |
| Replayed durable command | cached event; one Paper order/fill | Gate 3 Paper-fill test and duplicate-command fault test |
| Unknown broker outcome | reconciliation/manual review; no blind retry | P08-T13 unknown-submit test |

## Scope and authority evidence

Every successful Telegram approval is checked twice:

- `TelegramPaperApprovalHandler` constructs only
  `method=telegram`, `scope=paper`; and
- `ApprovalHandoffService` reloads the stored proposal and event, requires a
  Paper proposal plus an approved Telegram/Paper event, verifies all IDs and
  order hashes, and rejects anything else before dispatch.

The final broker independently requires `account_scope=paper`. The automated
happy path asserts one workflow handler call, one Paper order, and one Paper
fill. The Live-forgery path asserts zero execution calls.

## Secret and data handling evidence

- Tests use synthetic Bot identity, recipient identity, nonce, account data,
  and market observations only.
- The Gate 3 happy path scans persisted approval, outbox, and audit payloads;
  neither the raw callback nonce nor the synthetic Bot token is present.
- Telegram configuration tests separately enforce no token in argv, logs,
  errors, environment aliases, or non-exact secret files.
- CI secret scanning and the repository-wide secrets tests remain mandatory.
- This document contains no real Telegram user/chat/Bot ID, Bot token,
  Robinhood credential, account number, or portfolio value.

## Public-route and broker isolation

Gate 3 has no approval HTTP route. Telegram inbound updates arrive through the
bounded poller and typed authorization boundary. The callback handler imports
neither FastAPI nor a broker. The approval handoff is neutral; its orchestrator
bridge emits a workflow command. The accepted harness registers only a
`PaperBroker` handler, and statically rejects Robinhood/rh-mcp or public-route
references in that composition.

## Owner-assisted iPhone rehearsal

The automated harness reproduces the exact private callback fields and reaches
a deterministic Paper fill, but it cannot prove the owner's physical iPhone
tap or the current external Telegram delivery. The dedicated one-shot command
now owns the complete safe composition; the final Gate 3 acceptance needs one
staging-only run:

1. keep every Live/write service disabled and stop the ordinary Telegram read
   poller so the rehearsal can own its fenced staging poller;
2. run the command below from the repository root;
3. on the bound iPhone account, tap the inline approval button once within the
   bounded timeout;
4. verify the command reports `terminal=FILLED` and `replay_blocked=true`;
5. record only sanitized evidence: timestamp, proposal ID, approval event ID,
   Paper broker order ID, terminal `FILLED`, and confirmation that replay made
   no second order; and
6. never paste the Bot token, raw callback nonce, Telegram numeric identities,
   account value, or provider payload into Git or chat.

```bash
uv run --extra approval ainvest-gate3-rehearsal \
  --env-file /Users/kel/.config/ainvest/staging.env \
  --secrets-dir /Users/kel/.config/ainvest/secrets \
  --database /Users/kel/.local/share/ainvest/staging.sqlite3 \
  --confirm-poller-stopped
```

The command accepts no Bot token or Telegram identity on argv, requires exactly
one configured staging recipient, rejects non-staging or non-Paper settings,
and contains no Robinhood/rh-mcp or Live execution path. Until this rehearsal
is recorded below, the release acceptance status remains pending even though
the automated gate passes.

| Evidence item | Result |
| --- | --- |
| Environment | staging |
| Live/write services | must remain disabled |
| Owner iPhone callback | pending |
| Telegram approval | pending |
| Paper fill | pending |
| Replay creates no second order | pending |

## Defect and sign-off register

| Item | Result |
| --- | --- |
| Automated Gate 3 critical/high defects | 0 |
| Telegram input can create Live scope | no |
| Telegram path can call Robinhood write client | no |
| Public approval route exists | no |
| Automated candidate | pass |
| Owner-assisted staging rehearsal | pending |
| Final Gate 3 result | pending |

No later task may reinterpret this automated candidate as permission to enable
Live trading. Final acceptance changes only after the staging rehearsal is
completed and sanitized evidence is committed.
