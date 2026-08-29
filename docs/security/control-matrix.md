# Security control and evidence matrix

Status: living control register implemented by `P08-T6`

Last reviewed: 2026-08-06

Authority: [`threat-model.md`](threat-model.md), [`data-flow.md`](data-flow.md),
[`secrets.md`](secrets.md), `design.md`, and `IMPLEMENTATION_TODO.md`

The authoritative register is the machine-readable
[`control-evidence.json`](control-evidence.json). The table below is a derived
human summary; unit tests render it from the manifest and require an exact
match, so prose cannot silently drift from evidence state. The register records
what the repository can prove today. It is not a list of intended features
presented as completed work. A control marked `partial`,
`planned`, or `blocked` is **not** release evidence for the missing portion.
Paper remains the default, broker writes remain unavailable, and every such row
blocks the affected release capability until its owner supplies passing evidence.

## Status and evidence vocabulary

- `implemented`: the named control is present and its named automated evidence
  is passing in the canonical test or CI gate.
- `partial`: some named controls have passing evidence, but the residual-risk
  text identifies missing work. Partial is never interpreted as release-ready.
- `planned`: the owning task has not landed. A test ID is a future evidence
  requirement, not a passing test.
- `not_applicable`: the attack surface is deliberately absent. Enabling that
  surface requires changing the row to `planned` or `partial` and adding tests.
- Evidence state `passing`, `partial`, `planned`, `blocked`, and
  `not_applicable` has the same fail-closed meaning. Only `passing` is complete
  automated evidence.

Owners are accountable capability roles, not the author of this document.
Every evidence ID in the manifest has a kind, path, exact pytest node or CI
context, state, and explicit threat support. `SEC-*` targets name required
future evidence from the accepted threat model. An implemented threat must use
the reviewed completion allowlist enforced by
`tests/unit/security/test_control_matrix.py`; an arbitrary existing file or
unrelated test cannot make it pass. CI evidence is reproducible only when the
named required workflow check ran against the exact commit.

## Threat-to-control register

<!-- control-matrix:start -->
| Threat | Severity | Applicability | Accountable owner | Evidence state | Status | Evidence IDs | Release disposition |
|---|---|---|---|---|---|---|---|
| `T-001` | `critical` | `paper_and_live` | Strategy Runtime owner | `partial` | `partial` | `E-ARCH-BOUNDARIES`, `E-WORKER-ISOLATION`, `P-KERNEL-SANDBOX` | Live blocked until kernel-level isolation evidence and R-003 treatment exist. |
| `T-002` | `critical` | `all` | Supply-chain Security owner | `partial` | `partial` | `E-CI-POLICY`, `E-CI-VERIFY`, `E-CI-SECRET`, `E-CI-DEPENDENCY`, `E-CI-SAST`, `P-CODEOWNERS` | Scans are required merge checks; Live remains blocked on security-path ownership and R-002 treatment. |
| `T-003` | `critical` | `paper_and_live` | Approval owner | `partial` | `partial` | `E-APPR-NONCE`, `E-APPR-CONCURRENCY`, `P-WEBAUTHN-REPLAY` | Live blocked until WebAuthn replay and approval handoff evidence exists. |
| `T-004` | `critical` | `paper_and_live` | Approval and Execution owner | `partial` | `partial` | `E-ORDER-HASH`, `E-ORDER-TAMPER`, `P-LIVE-HASH` | Live blocked until UI and Execution pre-submit hash evidence exists. |
| `T-005` | `conditional_critical` | `telegram_paper_with_live_escalation` | Approval owner | `partial` | `partial` | `E-APPROVAL-SCOPE`, `E-SECRET-ROLES`, `P-TELEGRAM-IDENTITY` | Telegram transport is absent and cannot authorize Live; transport evidence is required before enablement. |
| `T-006` | `conditional_high` | `telegram_paper` | Approval Transport owner | `planned` | `planned` | `P-TELEGRAM-POLL` | Telegram transport remains disabled until polling evidence passes. |
| `T-007` | `high` | `data_and_research` | Data and Research Security owner | `partial` | `partial` | `E-WORKER-ISOLATION`, `E-YAHOO-LIVE-DENY`, `P-RESEARCH-SSRF`, `P-MCP-ALLOWLIST`, `P-GATEWAY-PROSE` | Research URL policy, production egress, and external gateway allowlist evidence remain required. The rh-mcp v0.4.1 artifact audit retains the consumer requirement on this path: provider guide, tool description, schema description, and news article prose rides inside results or the reviewed manifest, is not executed by the gateway, and must be discarded before any model, Telegram, CLI, or log context. It is tracked by planned evidence P-GATEWAY-PROSE (SEC-PROSE-*), which is planned and not passing; P06-T0 and P06-T2 owe it. |
| `T-008` | `conditional_critical` | `future_webhook` | Approval Transport owner | `not_applicable` | `not_applicable` | `A-WEBHOOK-ABSENT`, `P-WEBHOOK-SECURITY` | Adding a webhook route is blocked until this row gains passing security evidence. |
| `T-009` | `critical` | `paper_and_live` | Execution Safety owner | `partial` | `partial` | `E-RUNTIME-MODES`, `E-APPROVAL-SCOPE`, `P-SAFE-LIVE` | Live remains blocked until handoff, broker guard, gate, and safety-suite evidence exists. |
| `T-010` | `critical` | `paper_and_live` | Workflow and Execution owner | `partial` | `partial` | `E-WORKFLOW-IDEMPOTENCY`, `E-PRETRADE-DUPLICATE`, `E-RECONCILIATION-IDEMPOTENT`, `P-LIVE-DELIVERY` | Live blocked until concurrent broker delivery and submit-boundary evidence exists. |
| `T-011` | `critical` | `future_operator_control_plane` | Operator Security owner | `blocked` | `planned` | `A-OPERATOR-ABSENT`, `E-SECRET-ROLES`, `P-OPERATOR-AUTH` | Remote administration and Live remain blocked until DEC-018 and P08-T14 evidence exists. |
| `T-012` | `high` | `database_and_operator` | Audit and Operator owner | `partial` | `partial` | `E-AUDIT-APPEND`, `E-AUDIT-REDACT`, `P-AUDIT-AUTH` | Production audit access remains blocked pending provider DB grants, retention decisions, and operator authorization. |
| `T-013` | `critical` | `live_cancel` | Execution and Operator owner | `partial` | `partial` | `E-CANCEL-UNKNOWN`, `E-KILL-NO-AUTOCANCEL`, `P-BROKER-CANCEL` | Broker cancel and operator authorization evidence are required before Live. |
| `T-014` | `high` | `all_sinks` | Observability and Security owner | `partial` | `partial` | `E-LOG-REDACT`, `E-TRACE-SANITIZE`, `E-METRIC-LABELS`, `E-CI-SECRET`, `P-EXPORTER-SINK` | Exporter, Telegram, and deployment sink evidence remains required before each is enabled. |
| `T-015` | `high` | `paper_and_live` | Risk and Scheduling owner | `passing` | `implemented` | `E-TIME-CALENDAR`, `E-TIME-TTL`, `E-TIME-STALE`, `E-CI-VERIFY` | Production NTP monitoring remains a deployment responsibility; safe halt on bad time is accepted. |
| `T-016` | `critical` | `robinhood_read_and_live` | Read Broker owner | `partial` | `partial` | `E-SECRET-ROLES`, `E-YAHOO-LIVE-DENY`, `E-HEALTH-NOTREADY`, `P-MCP-ALLOWLIST`, `P-GATEWAY-PROSE` | The exact external rh-mcp v0.4.1 release artifacts and stable P06-T0 ainvest adapter boundary are independently audited; the executable pin implementation remains subject to exact-head review before merge. P06-T0 pins and verifies the release, artifact and manifests; imports only the published gateway surface; explicitly keeps allow_mutations false; exposes the same named 10-operation projection over the 36 mutates=false capabilities; rejects the 11 approved non-trading mutations and all 8 denied trading capabilities; sanitizes failures and SDK-neutral envelopes; and discards provider-controlled prose at the adapter boundary with deterministic fixtures. P06-T1 normalization and the P06-T2 Part 1 display sink are merged. T-016 remains partial, not passing or complete: P06-T2 Part 2 still requires trustworthy canonical instrument identity, verified Agentic-account binding, and regular-session evidence; deployment evidence, owner-assisted real status/read validation against v0.4.1, and P-GATEWAY-PROSE end-to-end evidence also remain pending. |
<!-- control-matrix:end -->

## Cross-cutting release evidence

| Evidence | Current state | What it proves | Explicit limitation |
|---|---|---|---|
| `Verify` | implemented | Lock consistency, formatting, lint, strict typing, all test layers, schema snapshots, and coverage | It does not replace threat-specific safety or deployment tests. |
| `Secret scan` | implemented | Gitleaks scans repository history and pull-request changes | It does not prove runtime secret-manager or log-sink configuration. |
| `Dependency audit` | implemented | Every locked group and optional extra is exported and audited | It detects known advisories, not zero-days or malicious behavior without an advisory. |
| `SAST` | required merge gate | GitHub CodeQL analyzes Python on pushes and pull requests; ruleset `19761285` requires the `SAST` workflow job context | Findings must remain visible and triaged; the secondary CodeQL reporting context is not a separate required check, and SAST does not prove authorization correctness. |
| Telemetry data minimization | implemented by `P08-T4` | Metrics reject free-form labels; traces reject arbitrary attributes and omit exception messages; health uses bounded names and reasons | No exporter, collector, HTTP endpoint, or production resource configuration exists; deployment-specific sink evidence remains pending. |
| Development-only Yahoo boundary | implemented by `P04-T1` | Live construction fails before transport/dependency access, data stays delayed/unverified, errors are sanitized, and recorded tests use no public network | It is an offline research adapter, not Robinhood evidence, a Live fallback, or production network-policy evidence. |
| Container scan | not_applicable | No container artifact exists in the repository | Becomes mandatory when a container definition or image build is introduced. |
| IaC scan | not_applicable | No deployment IaC exists in the repository | Becomes mandatory when deployable IaC is introduced. |
| Independent Live review | blocked | No Live broker implementation or production deployment exists to review | `P08-T15` must require review by someone other than the implementing agent before Live. |
| External `rh-mcp` evidence | partial | `rh-mcp` `v0.4.1` is independently audited (`APPROVED_FOR_AINVEST_PIN`, 2026-08-29, bound to tag commit `6dfe4a7`) and its tag, artifact SHA-256 digests, signed build provenance, manifest version, expected full-manifest digest, envelope version, and strict mutation gate are pinned in `docs/tasks/status.md`; the ainvest P06-T0 adapter, ten-read projection, sanitization, and deterministic fixtures remain deliberately narrow; P06-T1 normalization and P06-T2 Part 1 display are merged through #111/#114/#117 | The artifact audit applies only to the exact v0.4.1 artifacts. The pin implementation still requires exact-head independent review and green merge gates. T-016 remains partial pending P06-T2 Part 2's trustworthy canonical identity, verified Agentic-account binding, and regular-session evidence; deployment evidence, owner-assisted real status/read validation against v0.4.1, and end-to-end P-GATEWAY-PROSE evidence also remain pending. |

## Release rule

The automated evidence gate validates traceability, exact test nodes and CI
contexts, the reviewed completion allowlist, derived-document consistency, and
honest evidence state. It does not turn `planned`, `partial`, or `blocked`
controls into passing controls.
A release gate for a capability must select its relevant threat rows and reject
the release unless every required row has `Status=implemented` and
`Evidence state=passing`, or the row is genuinely `not_applicable` because the
surface remains absent. High/Critical residual-risk acceptance must be recorded
by the product/account owner as required by the threat model; this document
does not grant that acceptance.
