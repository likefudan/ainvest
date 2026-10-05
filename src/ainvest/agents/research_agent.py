"""Independent bounded research runs; real API activation remains closed (DEC-009)."""

import asyncio
import json
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Literal, cast

from ainvest.agents.prompts import SYSTEM_PROMPT
from ainvest.agents.research_capture import MAX_RUN_CAPTURE_BYTES, CapturedToolResult
from ainvest.agents.research_models import (
    ModelReply,
    ModelTelemetry,
    ResearchAgentResult,
    ResearchLimits,
    ResearchNarrative,
    ResearchRunRecord,
)
from ainvest.agents.tools import HistoryInput, ResearchTools
from ainvest.agents.tools.models import TOOL_NAMES, ToolName, ToolResult
from ainvest.data.indicators import digest_bytes
from ainvest.schemas.common import DomainModel, QualityFlag
from ainvest.schemas.research import EvidenceCitation

if TYPE_CHECKING:
    import httpx

_RUN_LOCK = Lock()
_PROHIBITED = re.compile(
    r"\b(buy|sell|purchase|short|order|shares?|guarantee\w*|profit\w*|"
    r"returns?|percent|million|billion|zero|one|two|three|four|five|six|seven|eight|"
    r"nine|ten|hundred|thousand)\b|买入|卖出|下单|做多|做空|保证|稳赚|收益|[零一二三四五六七八九十百千万亿]",
    re.IGNORECASE,
)


class ModelFailure(Exception):
    """Sanitized failure plus bounded usage metadata, never provider body text."""

    def __init__(self, code: str, telemetry: ModelTelemetry | None = None) -> None:
        self.code = code
        self.telemetry = telemetry or ModelTelemetry()
        super().__init__(code)


class TransientModelError(ModelFailure):
    """Only a positively classified network/rate-limit error permits one retry."""

    def __init__(
        self, code: Literal["NETWORK", "RATE_LIMIT"], telemetry: ModelTelemetry | None = None
    ) -> None:
        if code not in ("NETWORK", "RATE_LIMIT"):
            raise ValueError("not a retryable failure")
        super().__init__(code, telemetry)


class _Rejected(ValueError):
    pass


class ResearchReadToolset:
    """Only eight named run-bound reads; no user-supplied capability parameters."""

    def __init__(
        self, tools: ResearchTools, history: HistoryInput | None, limits: ResearchLimits
    ) -> None:
        self._tools = tools
        self._history = history
        self._limits = limits
        self._active = True
        self._busy = False
        self._digests: list[str] = []
        self._captures: list[CapturedToolResult] = []
        self._capture_bytes = 0
        self._evidence: dict[str, EvidenceCitation] = {}
        self._names: set[ToolName] = set()
        self._flags: set[QualityFlag] = set()
        self._error = False
        self._calls = 0
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-tool")

    async def _read(self, name: ToolName) -> ToolResult[DomainModel]:
        if not self._active or self._busy:
            self._error = True
            raise _Rejected("TOOL_RUN_CLOSED")
        self._calls += 1
        if self._calls > self._limits.tool_calls:
            self._error = True
            raise _Rejected("TOOL_LIMIT")
        if name in ("history", "indicators") and self._history is None:
            self._error = True
            raise _Rejected("HISTORY_INPUT_MISSING")
        self._busy = True
        try:
            reader = getattr(self._tools, name)
            loop = asyncio.get_running_loop()
            if name in ("history", "indicators"):
                result = await loop.run_in_executor(self._executor, reader, self._history)
            else:
                result = await loop.run_in_executor(self._executor, reader)
            if not self._active:
                raise _Rejected("TOOL_RUN_CLOSED")
            # The model only gets the fixed, validated versioned result.
            typed = cast(ToolResult[DomainModel], result)
            capture = CapturedToolResult.capture_json(name, result.model_dump_json())
            self._capture_bytes += len(capture.json_result.encode())
            if self._capture_bytes > MAX_RUN_CAPTURE_BYTES:
                raise _Rejected("TOOL_CAPTURE_LIMIT")
            self._captures.append(capture)
            self._digests.append(capture.digest)
            self._names.add(name)
            self._flags.update(typed.quality_flags)
            self._error |= typed.status == "error"
            self._evidence.update((item.evidence_id, item) for item in typed.evidence)
            return typed
        except BaseException:
            self._error = True
            raise
        finally:
            self._busy = False

    async def quote(self) -> ToolResult[DomainModel]:
        """Read this run's fixed normalized quote."""
        return await self._read("quote")

    async def price_book(self) -> ToolResult[DomainModel]:
        """Read this run's fixed normalized price book."""
        return await self._read("price_book")

    async def history(self) -> ToolResult[DomainModel]:
        """Read the caller-bound historical bar grid, without model parameters."""
        return await self._read("history")

    async def indicators(self) -> ToolResult[DomainModel]:
        """Read deterministic indicators on the caller-bound historical grid."""
        return await self._read("indicators")

    async def filings(self) -> ToolResult[DomainModel]:
        """Read captured filing metadata; it remains partial."""
        return await self._read("filings")

    async def news(self) -> ToolResult[DomainModel]:
        """Read untrusted source metadata and its quality flags."""
        return await self._read("news")

    async def concentration(self) -> ToolResult[DomainModel]:
        """Read deterministic portfolio concentration without account identifiers."""
        return await self._read("concentration")

    async def buying_power(self) -> ToolResult[DomainModel]:
        """Read observed aggregate amounts, never order sizing."""
        return await self._read("buying_power")

    def close(self) -> None:
        self._active = False
        self._executor.shutdown(wait=False, cancel_futures=True)


@dataclass(frozen=True, slots=True)
class AgentContext:
    tools: ResearchReadToolset
    limits: ResearchLimits
    input_json: str
    system_prompt: str = SYSTEM_PROMPT


ModelPort = Callable[[AgentContext], Awaitable[ModelReply]]


class ResearchAgent:
    """Offline orchestration; no ambient credentials or default real provider.

    Fakes and the mock-only SDK adapter are library injection points. There is
    no operational factory, scheduler or API enable switch until DEC-009.
    """

    def __init__(self, model: ModelPort | None = None, *, limits: ResearchLimits | None = None):
        self._model = model
        self._limits = ResearchLimits.model_validate_json(
            (limits or ResearchLimits()).model_dump_json()
        )
        self._used: set[str] = set()

    async def run(
        self, tools: ResearchTools, *, history: HistoryInput | None = None
    ) -> ResearchAgentResult:
        """Produce only an intermediate narrative, never a ResearchPacket/order."""
        limits = self._limits
        history = HistoryInput.model_validate_json(history.model_dump_json()) if history else None
        body = json.dumps(
            {
                "scope": tools.scope.model_dump(mode="json"),
                "history": history.model_dump(mode="json") if history else None,
                "limits": limits.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        surface = json.dumps(
            {
                "names": TOOL_NAMES,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "narrative": ResearchNarrative.model_json_schema(),
            },
            sort_keys=True,
        )
        facade = ResearchReadToolset(tools, history, limits)
        record = ResearchRunRecord(
            run_id=tools.scope.run_id,
            prompt_digest=digest_bytes(SYSTEM_PROMPT.encode()),
            tool_schema_digest=digest_bytes(surface.encode()),
            input_digest=digest_bytes(body.encode()),
            output_digest=None,
            tool_output_digests=(),
            attempts=0,
            request_ids=(),
            response_ids=(),
            input_tokens=0,
            output_tokens=0,
            requests=0,
            usage_complete=True,
        )

        def add_usage(usage: ModelTelemetry) -> None:
            nonlocal record
            record = record.model_copy(
                update={
                    "request_ids": record.request_ids + usage.request_ids,
                    "response_ids": record.response_ids + usage.response_ids,
                    "requests": record.requests + usage.requests,
                    "input_tokens": record.input_tokens + usage.input_tokens,
                    "output_tokens": record.output_tokens + usage.output_tokens,
                    "usage_complete": record.usage_complete and usage.usage_complete,
                }
            )

        def error(code: str) -> ResearchAgentResult:
            facade.close()
            return ResearchAgentResult(
                status="error",
                narrative=None,
                evidence=(),
                error_code=code,
                quality_flags=(QualityFlag.PARTIAL,),
                record=record.model_copy(update={"tool_output_digests": tuple(facade._digests)}),
                captures=tuple(facade._captures),
            )

        if self._model is None:
            return error("AI_DISABLED")
        if not _RUN_LOCK.acquire(blocking=False):
            return error("RUN_BUSY")
        try:
            if tools.scope.run_id in self._used or len(self._used) >= 128:
                return error("RUN_REUSED")
            self._used.add(tools.scope.run_id)
            deadline = time.monotonic() + limits.duration_seconds
            for attempt in (1, 2):
                record = record.model_copy(update={"attempts": attempt})
                try:
                    if (
                        record.requests >= limits.requests
                        or record.input_tokens >= limits.input_tokens
                        or record.output_tokens >= limits.output_tokens
                    ):
                        return error("USAGE_LIMIT")
                    remaining = limits.model_copy(
                        update={
                            "requests": limits.requests - record.requests,
                            "input_tokens": limits.input_tokens - record.input_tokens,
                            "output_tokens": limits.output_tokens - record.output_tokens,
                        }
                    )
                    raw = await asyncio.wait_for(
                        self._model(AgentContext(facade, remaining, body)),
                        timeout=max(0, deadline - time.monotonic()),
                    )
                    reply = ModelReply.model_validate_json(raw.model_dump_json())
                    add_usage(
                        ModelTelemetry(
                            requests=reply.requests,
                            input_tokens=reply.input_tokens,
                            output_tokens=reply.output_tokens,
                            request_ids=reply.request_ids,
                            response_ids=reply.response_ids,
                            usage_complete=reply.usage_complete,
                        )
                    )
                    record = record.model_copy(
                        update={"output_digest": digest_bytes(reply.output_json.encode())}
                    )
                    if (
                        record.requests > limits.requests
                        or record.input_tokens > limits.input_tokens
                        or record.output_tokens > limits.output_tokens
                    ):
                        return error("USAGE_LIMIT")
                    narrative = ResearchNarrative.model_validate_json(reply.output_json)
                    claims = narrative.claims()
                    if not claims:
                        return error("EMPTY_NARRATIVE")
                    if facade._error or not set(limits.required_tools).issubset(facade._names):
                        return error("REQUIRED_TOOL_FAILED")
                    ids = tuple(
                        dict.fromkeys(key for claim in claims for key in claim.evidence_ids)
                    )
                    if any(key not in facade._evidence for key in ids):
                        return error("UNSUPPORTED_EVIDENCE")
                    evidence = tools.resolve_evidence(ids)
                    flags = set(facade._flags)
                    if not record.usage_complete:
                        flags.add(QualityFlag.PARTIAL)
                    for claim in claims:
                        text = unicodedata.normalize("NFKC", claim.text)
                        if (
                            any(char.isnumeric() for char in text)
                            or _PROHIBITED.search(text)
                            or any(unicodedata.category(char) == "Cf" for char in text)
                        ):
                            return error("PROHIBITED_NARRATIVE")
                        summaries = {facade._evidence[key].summary for key in claim.evidence_ids}
                        if claim.kind == "observation" and claim.text not in summaries:
                            return error("UNSUPPORTED_CLAIM")
                        if claim.kind == "hypothesis":
                            flags.add(QualityFlag.PARTIAL)
                    if not tools.is_complete(limits.required_tools):
                        flags.add(QualityFlag.PARTIAL)
                    record = record.model_copy(
                        update={
                            "output_digest": digest_bytes(narrative.model_dump_json().encode()),
                            "tool_output_digests": tuple(facade._digests),
                        }
                    )
                    return ResearchAgentResult(
                        status="partial" if flags else "complete",
                        narrative=narrative,
                        evidence=evidence,
                        quality_flags=tuple(sorted(flags)),
                        error_code=None,
                        record=record,
                        captures=tuple(facade._captures),
                    )
                except TransientModelError as exc:
                    add_usage(exc.telemetry)
                    if attempt == 2:
                        return error("TRANSIENT_RETRY_EXHAUSTED")
                    # Fresh model messages on retry; no provider conversation state.
                except TimeoutError:
                    record = record.model_copy(update={"usage_complete": False})
                    tools.close()
                    return error("TIMEOUT")
                except asyncio.CancelledError:
                    tools.close()
                    raise
                except ModelFailure as exc:
                    add_usage(exc.telemetry)
                    return error(exc.code)
                except _Rejected as exc:
                    return error(str(exc))
                except Exception:
                    return error("MODEL_FAILED")
            return error("MODEL_FAILED")  # defensive; both attempts exit above
        finally:
            facade.close()
            _RUN_LOCK.release()


def offline_responses_model(transport: "httpx.MockTransport") -> ModelPort:
    """Exercise the real SDK only against an explicit in-memory mock transport.

    No secret is read, no network transport can be supplied, no real AI switch
    is exposed. Operational credential/budget composition is a future owner gate.
    """
    import os

    import httpx
    from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
    from pydantic_ai import Agent, NativeOutput, Tool
    from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UsageLimitExceeded
    from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.usage import UsageLimits

    if type(transport) is not httpx.MockTransport:
        raise ValueError("only the offline mock transport is permitted")

    def network_cause(error: BaseException) -> bool:
        cause = error.__cause__
        for _ in range(4):
            if isinstance(cause, (httpx.NetworkError, httpx.TimeoutException)):
                return True
            if not isinstance(cause, (APIConnectionError, APITimeoutError)):
                return False
            cause = cause.__cause__
        return False

    async def invoke(context: AgentContext) -> ModelReply:
        # The locked SDK unconditionally reads this variable in its ordinary
        # client constructor. Check presence only, before construction; never
        # read its value or mutate process-wide environment configuration.
        if "OPENAI_CUSTOM_HEADERS" in os.environ:
            raise ModelFailure(
                "OFFLINE_CONFIG_UNSAFE", ModelTelemetry(requests=0, usage_complete=True)
            )
        request_ids: list[str] = []
        response_ids: list[str] = []
        requests = 0
        input_tokens = 0
        output_tokens = 0
        usage_responses = 0

        def telemetry() -> ModelTelemetry:
            return ModelTelemetry(
                requests=requests,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                request_ids=tuple(request_ids),
                response_ids=tuple(response_ids),
                usage_complete=requests == usage_responses,
            )

        async def before(request: httpx.Request) -> None:
            nonlocal requests
            if requests >= context.limits.requests:
                raise _Rejected("USAGE_LIMIT")
            requests += 1

        async def capture(response: httpx.Response) -> None:
            nonlocal input_tokens, output_tokens, usage_responses
            request_id = response.headers.get("x-request-id")
            if request_id:
                request_ids.append(request_id)
            raw = await response.aread()
            if len(raw) > 262_144:
                raise _Rejected("MODEL_RESULT_TOO_LARGE")
            body = response.json()
            if isinstance(body, dict):
                if response_id := body.get("id"):
                    response_ids.append(response_id)
                usage = body.get("usage")
                if isinstance(usage, dict):
                    incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
                    if (
                        type(incoming) is int
                        and type(outgoing) is int
                        and 0 <= incoming <= 1_000_000
                        and 0 <= outgoing <= 1_000_000
                    ):
                        input_tokens += incoming
                        output_tokens += outgoing
                        usage_responses += 1

        async with httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            timeout=context.limits.duration_seconds,
            event_hooks={"request": [before], "response": [capture]},
        ) as http:
            client = AsyncOpenAI(
                api_key="offline-placeholder-not-a-key",
                admin_api_key="",
                organization="",
                project="",
                webhook_secret="",
                base_url="https://api.openai.com/v1",
                http_client=http,
                max_retries=0,
                timeout=context.limits.duration_seconds,
            )
            model = OpenAIResponsesModel(
                "gpt-5.6-sol", provider=OpenAIProvider(openai_client=client)
            )
            settings = OpenAIResponsesModelSettings(
                openai_reasoning_effort="medium",
                openai_store=False,
                parallel_tool_calls=False,
                max_tokens=context.limits.output_tokens,
            )
            functions = [
                Tool(
                    getattr(context.tools, name),
                    name=name,
                    takes_ctx=False,
                    strict=True,
                    max_retries=0,
                    sequential=True,
                )
                for name in TOOL_NAMES
            ]
            agent: Agent[None, ResearchNarrative] = Agent(
                model,
                output_type=NativeOutput(ResearchNarrative, strict=True),
                system_prompt=context.system_prompt,
                tools=functions,
                retries=0,
                model_settings=settings,
            )
            agent.instrument = False
            try:
                result = await agent.run(
                    context.input_json,
                    usage_limits=UsageLimits(
                        request_limit=context.limits.requests,
                        tool_calls_limit=context.limits.tool_calls,
                        input_tokens_limit=context.limits.input_tokens,
                        output_tokens_limit=context.limits.output_tokens,
                    ),
                )
            except (APIConnectionError, APITimeoutError) as exc:
                if network_cause(exc):
                    raise TransientModelError("NETWORK", telemetry()) from exc
                raise ModelFailure("MODEL_FAILED", telemetry()) from exc
            except ModelHTTPError as exc:
                if exc.status_code == 429:
                    raise TransientModelError("RATE_LIMIT", telemetry()) from exc
                raise ModelFailure("MODEL_HTTP_FAILED", telemetry()) from exc
            except ModelAPIError as exc:
                if network_cause(exc):
                    raise TransientModelError("NETWORK", telemetry()) from exc
                raise ModelFailure("MODEL_FAILED", telemetry()) from exc
            except UsageLimitExceeded as exc:
                raise ModelFailure("USAGE_LIMIT", telemetry()) from exc
            except Exception as exc:
                raise ModelFailure("MODEL_FAILED", telemetry()) from exc
            responses = [message for message in result.all_messages() if message.kind == "response"]
            if any(
                message.model_name != "gpt-5.6-sol"
                or message.finish_reason != "stop"
                or (message.provider_details or {}).get("finish_reason") != "completed"
                for message in responses
            ):
                raise ModelFailure("MODEL_INCOMPLETE", telemetry())
            usage = result.usage
            return ModelReply(
                output_json=result.output.model_dump_json(),
                request_ids=tuple(request_ids),
                response_ids=tuple(
                    message.provider_response_id
                    for message in responses
                    if message.provider_response_id
                ),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                requests=usage.requests,
                usage_complete=telemetry().usage_complete,
            )

    return invoke
