# Bounded research narrative agent

P04-T6 implements an offline library, not an enabled AI service. DEC-004 fixes
OpenAI `gpt-5.6-sol`, Pydantic AI and Responses, `medium` reasoning,
`store=false`, strict native JSON Schema, no built-in tools and no fallback.
DEC-009 still requires the owner to provision a project/key and approve a monthly
budget. The default agent returns `AI_DISABLED`; no credential is read from the
environment, secret storage or staging configuration. There is no activation CLI.

The API contract follows the official [Responses migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses),
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs)
and [strict function calling requirements](https://developers.openai.com/api/docs/guides/function-calling#strict-mode).
The locked Pydantic AI 1.107.1 and OpenAI 2.48.0 SDKs are exercised with an explicit
in-memory `httpx.MockTransport`; the adapter refuses any other transport type,
uses a synthetic placeholder, disables ambient proxy configuration and SDK retries,
and never establishes a provider network connection. Existing locked versions
are also installed in development for unskipped SDK contract tests; core-only
deployments still need no research SDK.

The offline constructor supplies explicit empty admin/organization/project/webhook
selectors and a fixed URL/key, so SDK environment fallbacks are not used. The
locked SDK otherwise reads `OPENAI_CUSTOM_HEADERS` unconditionally; if that
variable is present the adapter returns `OFFLINE_CONFIG_UNSAFE` before constructing
the SDK, without reading its value or changing the process environment. This
presence check is repeated at invocation, not only when creating the adapter.

## Independent context and read boundary

Each attempt constructs a new Pydantic AI agent with no message history,
`previous_response_id` or server conversation identifier. Local messages inside
one attempt retain tool results so the model can cite them. Eight strict,
parameter-free named functions are exposed: quote, price book, history,
indicators, filings, news, concentration and buying power. Instrument, cutoff
and historical grid are caller-bound, not model-controlled. No generic broker
dispatcher, OAuth/MCP session, account identifier or write tool crosses the
boundary. Actual data readers retain the trust/timeout limitations documented
in [research-tools.md](research-tools.md).

Source text is untrusted data, explicitly separated from the fixed system
permissions. It cannot select another model, add tools or mutate configuration.
The prompt is packaged under `agents/prompts` and versioned independently of
the run. Built-in web search and tracing of source/model bodies are disabled.

## Evidence and output policy

The intermediate narrative has bull case, bear case, risks and open questions.
Every claim has one to eight same-run evidence IDs and an explicit kind:

- `observation`: exact copy of one returned citation summary; unsupported
  paraphrases/factual assertions are rejected rather than declared verified.
- `hypothesis`: bounded qualitative interpretation, always `PARTIAL`.

Only citations actually returned through this attempt/run's named wrappers may
be used, even if the underlying tool registry already contains other citations.
Numbers, quantities, trade directions and performance guarantees are prohibited
in narrative text. Unicode normalization, numeric-character detection and a
conservative English/Chinese lexical guard supplement the prompt. This is not
a general semantic safety classifier: arbitrary language may evade lexical
checks, which is another reason free interpretations can never be complete.
Deterministic numeric data remains in tool output for P04-T7, not model text.

An intermediate `complete` result therefore means verified extractive summaries
with clean required tools and fully observed usage, not a trading recommendation
or a final `ResearchPacket`. A hypothesis, partial source or unknown usage retains
quality flags. An error withholds narrative and citations entirely. The module
does not construct orders, proposals or packets, and never persists operational
records or enables strategies.

## Bounds and failures

Engineering defaults (not monetary authorization) are sixty seconds total,
four provider requests including retry, eight tool calls, thirty-two thousand
input tokens and four thousand output tokens. Callers may select stricter values
within validated hard ceilings. Concurrency is one across library agents with
immediate `RUN_BUSY`, not an unbounded waiting queue. An agent instance accepts
at most 128 unique run IDs and rejects reuse. Async model ports must cooperate
with cancellation; deterministic readers must honor their own network deadlines.
Timeout closes the underlying tool run and late wrapper results are rejected.

There is one application retry only for positively identified HTTP 429 or
network/timeout causes. Schema errors, refusals, unknown tools, incomplete
responses, model mismatches and other HTTP errors are not retried. SDK/model
validation repair retries are zero. Retry shares the original deadline and
remaining request/token/tool allowances; it never switches models.

Run records retain fixed model/prompt/tool versions, prompt and schema digests,
input/output and returned tool digests, attempts, provider HTTP request IDs,
response IDs and observed token/request usage. Failed calls retain known metadata;
unknown consumption is explicitly `usage_complete=false` and cannot yield a
complete result. Invalid/refused output is not logged. These records are returned
in memory only; durable storage/evidence assembly belongs to P04-T7. Financial
reservation, monthly budget enforcement and actual API activation remain closed
until DEC-009 and a separately reviewed operational composition exist.

Agent results now include bounded immutable JSON captures of returned named
tools, including earlier captures when a later model call fails. Failed model
narrative and its citation list remain withheld. [Research assembly](research-builder.md)
validates these captures and can archive both successful and failed runs.

Offline tests cover fixed wire settings, fresh context, no additional tools,
same-run evidence, unsupported claims, trade/numeric prose, refusal, invalid
schema, model mismatch, incomplete responses, transient retry/exhaustion, unknown
usage, deadlines, tool/request ceilings, run reuse and concurrent requests.
