# Funds-safety incidents

P08-T5 supplies versioned state handlers and journal/notification/authorization
ports in `ainvest.observability.alerts`. This is an **offline-tested foundation,
not an installed production alert service**. No broker action, automatic
cancellation, risk bypass, remote operator endpoint or live startup is added.
P07-T2 owns real reconciliation event emission and end-to-end integration.
DEC-008, DEC-018 and DEC-019 remain binding.

## Ownership and independent delivery

Composition must assign `safety_operator` and `incident_commander` roles to
real authenticated on-call people and destinations outside the repository.
These are role names, not invented people or accepted production identities.
Every notice includes an owner: the configured primary role for active faults,
the escalation role for escalated faults, and the last owner for recovery.

An `independent` channel is mandatory, even with optional Telegram delivery.
It must actually be independent of the trading Bot's credentials, process,
failure domain and recipient workflow, e.g. separately provisioned paging or
email. Renaming the same Bot transport is invalid. A log or test sink is not
human notification. Telegram success never marks independent delivery successful.
Credentials, contact details and authentication belong in trusted adapters.
No real notification provider/account/contact has been provisioned by this task.

## Initial response and recovery evidence

All eight kinds are critical. Kind identifies the fault; `active`, `escalated`
and `recovered` describe the incident lifecycle.

| Kind | Immediate owner action | Evidence required before recovery |
| --- | --- | --- |
| `submit_unknown` | Block retry/resubmission; reconcile client ID, idempotency key and broker history. | Authoritative order outcome and fills; timeout is not proof of rejection. |
| `cancel_unknown` | Do not retry cancellation or replace the order; reconcile order/fills. | Authoritative cancellation/fill/open-order outcome and reviewed remaining exposure. |
| `order_hash_mismatch` | Quarantine the approval/handoff; never edit the approved order in place. | Invalid handoff rejected and cause corrected; replacement needs new proposal, hash, risk checks and approval. |
| `duplicate_order` | Stop further submissions and reconcile all candidates/fills. | Reviewed order IDs, quantities, exposure and idempotency history; no blind bulk cancel. |
| `account_mismatch` | Disable affected execution and independently verify account binding. | Fresh authoritative account identity, binding and permissions match configured scope. |
| `position_mismatch` | Hold affected execution and reconcile positions, fills and unsettled changes. | Fresh broker/account/ledger evidence agrees; unexplained differences stay active. |
| `kill_switch` | Verify new submissions are blocked; keep reconciling existing orders. | Every configured/operational source cleared through authorized workflows; clearing only one is insufficient. |
| `unexpected_live_start` | Isolate the unexpected component using approved operational controls. | Component stopped/isolated, configuration/credentials reviewed and disabled/paper posture verified. |

The alert module performs **none** of these actions. It cannot cancel, resubmit,
change a kill switch or authorize an order. Recovery never authorizes live.

## Acknowledge, escalate, resolve

1. The assigned operator locates restricted audit evidence using the redacted
   incident reference. Never paste tokens, account numbers or raw responses
   into chat, logs, tickets or this repository.
2. A trusted adapter independently authenticates and authorizes
   `AcknowledgeRequest` for the exact incident/revision/owner. Telegram identity
   alone is insufficient. The request has an opaque operator reference and
   bounded reason `investigating`. Missing/failed authorization is denied;
   acknowledgement is journaled before success and is not recovery or approval.
3. Worsening risk or an unavailable owner requires a higher-sequence
   `escalated` event from the trusted producer/supervisor. It clears old
   acknowledgement, changes owner and is immediately due despite an old retry
   cooldown. A later `active` event cannot downgrade it. Deployment must set
   and supervise an acknowledgement SLA: no background timer or guessed SLA
   is supplied by this module.
4. After verifying the table's evidence, the trusted producer sends a
   higher-sequence `recovered` event with fresh UTC time and evidence reference.
   Recovery older than the configured freshness bound (default 300 seconds)
   fails. This technical bound is not a trading limit. An arbitrary reference
   is not proof: verification belongs to the producer, which must never accept
   chat/model/unauthenticated remote instructions as authoritative state.
5. Recovery sends its own notice. Recurrence opens a new revision and requires
   attention again. All other risk, approval and release gates remain unchanged.

## Producer and durable journal contract

- An incident is keyed by `(environment, kind, subject_ref)`. Preserve monotonic
  producer sequence across restarts, recovery and reopening. Equal identical
  events are no-ops; equal conflicts raise `sequence_conflict`; stale events
  cannot clear state. Future/regressed observation timestamps are rejected.
- Use `redacted_reference` with a stable dedicated secret key of at least 32
  bytes for subjects/operators/evidence. HMAC prevents simple account-number
  dictionary attacks against plain hashes. Keep the key in secret storage,
  never reuse a broker/Bot token; rotation requires identity migration. No raw
  provider text, exceptions, URLs, tokens or account values enter these models.
- Inject a **durable** `AlertJournal`. `load() == None` means explicitly
  initialized empty storage, never missing/corrupt/unreadable expected data.
  Commit must atomically compare version, persist incident/outbox and preserve
  append-only audit history; return `True` only after durable success. No
  concrete production journal is supplied. The SQLite integration-test adapter
  demonstrates restart/CAS behavior but is not deployment code.
- Hold an exclusive writer lease for the service lifetime, including sends.
  An internal lock serializes threads; CAS rejects stale writes but is not
  cross-process delivery fencing. Ports cannot call back into the locked
  service. Saved policy/channel changes require explicit migration.
- State/outbox is committed before sending; attempt/cooldown before calling the
  provider. Storage errors poison the instance (`reload_required`): repair
  storage, acquire the lease and reload without deleting/resetting history.

## Delivery failure and storm control

A supervised scheduler calls `dispatch_due()`: default batch 16, maximum 100.
Its cadence bounds the global rate; adapters enforce their own network timeout.
Provider acceptance is not human acknowledgement. Failed/malformed receipts
and exceptions remain pending with capped exponential backoff (default 30
seconds, maximum one hour); exception text is never exported.

Delivery keys are stable per incident revision/route. A crash after send but
before receipt commit permits at-least-once delivery: providers must deduplicate
using that key and respect increasing revisions if messages arrive late.
Identical events enqueue nothing; same-state observations advance ordering
without another notice. Transitions supersede unsent old notices while retaining
history. Recovery therefore cannot be followed by an old pending active notice;
new notices include previous state, and escalation is always immediately due.

No history is automatically deleted (DEC-013). Checkpoints default to at most
1000 incidents/delivery records; exhaustion raises `journal_capacity_exceeded`
without eviction. The supervisor must mark this unhealthy, retain producer
replay and hold new execution through the caller's safety workflow. Never swallow
the error. Capacity, partitioning/archival and a scalable durable adapter need
review before deployment; this is not an unbounded production event store.

If independent delivery fails, use a separately tested operational fallback
to reach the owner, keep affected execution disabled, repair delivery and let
pending work retry. Supervise this outside the Bot: a failed channel cannot
reliably alert on itself. Never fake a successful receipt to silence an alarm.

## Verification and deployment prerequisites

`./scripts/dev verify` covers every kind's fault/recovery, duplicate storms,
escalation after acknowledgement, stale/conflicting events, recovery freshness,
denied acknowledgement, independent failure despite Telegram success, storage
failure/conflict, crash around delivery, redaction, bounded batches/retries,
and durable test-journal restart. Real kill-switch integration proves that
notification does not clear the switch or cancel anything.

Before runtime readiness: bind real owners and independently exercise fault
and recovery delivery to them; supply durable journal/lease/replay; connect
real emitters, scheduler, health and external fallback monitoring; approve
capacity/retention handling. Production operator access remains gated by
DEC-018/P08-T14. Offline tests and Gate 3 Paper do not satisfy these prerequisites.
