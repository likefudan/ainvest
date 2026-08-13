"""Thin ainvest read adapter over the pinned `rh-mcp` gateway (P06-T0).

This is a composition boundary, not a mapper and not a service. It:

1. narrows the gateway to a **read projection** and proves the narrowing
   against the reviewed manifest at startup;
2. verifies the pinned ``manifest_version`` and full ``manifest_digest`` at
   readiness, and the ``envelope_version`` on every result, **before**
   consuming any payload;
3. validates a bounded SDK-neutral result envelope and produces stable
   sanitized errors;
4. discards provider-controlled prose; and
5. logs only approved metadata.

What it deliberately does not do: no Robinhood-to-ainvest domain mapping and
no normalized schemas (P06-T1); no CLI, service composition, or Paper
integration (P06-T2). It imports no ``mcp.*`` type, never obtains or refreshes
a token, never accepts a raw session, never discovers a tool, and never
exposes a ``CallToolResult``. Everything it touches from the gateway is
reached through the structural protocols below, so this module names no
`rh-mcp` symbol at all — :mod:`ainvest.execution.robinhood.composition` is the
single place that does.

**Three obligations here are ainvest's own, not the gateway's**, and each is
recorded as conditions of the independently reviewed `rh-mcp` releases:

* `rh-mcp` ships **no read-only projection**. ``invoke()`` accepts any
  *allowed* capability, including the 11 approved non-trading mutations. So
  :func:`verify_read_projection` asserts at startup that every capability in
  the ainvest allowlist is ``allowed`` **and** ``mutates=false``, and that the
  reviewed manifest still splits exactly 35 / 11 / 8 with the exact names
  P06-T0 pinned. A manifest that later reclassifies a capability fails closed
  rather than silently widening the surface.
* Only `rh-mcp`'s published surface may be imported — never
  ``rh_mcp.transport._open_provider_session``, ``_PrivateSession``,
  ``StoredTokenProvider``, ``open_credential_store``, or any underscore name.
  The reviewer accepted that residual on the basis that the consumer carries
  it; ``tests/unit/execution/robinhood/test_published_surface.py`` is where it
  is carried.
* Provider ``guide``, tool descriptions, and schema descriptions ride inside
  result envelopes and inside the reviewed manifest. `rh-mcp` neither executes
  nor strips them. :mod:`ainvest.execution.robinhood.prose` discards them here,
  at the boundary, before anything is returned or logged.

There is **no** automatic data-provider fallback and no place to add one: this
adapter holds exactly one gateway, and a failure is a sanitized error
(DEC-003, `IMPLEMENTATION_TODO.md` rule 19).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Any, Final, Protocol

from ainvest.execution.robinhood.errors import (
    GatewayReadError,
    GatewayReadErrorCode,
    translate_gateway_failure,
)
from ainvest.execution.robinhood.pins import (
    APPROVED_NON_TRADING_MUTATIONS,
    DENIED_TRADING_CAPABILITIES,
    EXPECTED_MANIFEST_DIGEST,
    EXPECTED_MANIFEST_ENTRY_COUNT,
    MANIFEST_READ_CAPABILITIES,
    MAX_ENVELOPE_WARNINGS,
    MAX_LOGGED_DURATION_MS,
    MAX_PAYLOAD_DEPTH,
    MAX_PAYLOAD_NODES,
    MAX_PAYLOAD_STRING_LENGTH,
    MAX_WARNING_LENGTH,
    READINESS_KEYS,
    RESULT_ENVELOPE_KEYS,
    SUPPORTED_ENVELOPE_VERSIONS,
    SUPPORTED_MANIFEST_VERSIONS,
    ReadCapability,
)
from ainvest.execution.robinhood.prose import contains_provider_prose, discard_provider_prose
from ainvest.observability import get_logger

DIGEST_PATTERN: Final = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
RFC3339_DATETIME_PATTERN: Final = re.compile(
    r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])\Z"
)

#: The structured log event name. One event, two statuses.
READ_LOG_EVENT: Final = "robinhood_gateway_read"

#: Exactly the fields a successful read may log. Approved metadata only:
#: capability name, bounded duration, manifest/result digest, status. A token,
#: credential, MCP/provider type, raw session, raw account payload, arbitrary
#: tool name, or any provider prose is absent by construction — every value
#: below is either an ainvest constant or a digest.
READ_LOG_FIELDS_OK: Final[frozenset[str]] = frozenset(
    {"capability", "status", "duration_ms", "manifest_digest", "result_digest"}
)

#: Exactly the fields a failed read may log.
READ_LOG_FIELDS_ERROR: Final[frozenset[str]] = frozenset(
    {"capability", "status", "duration_ms", "manifest_digest", "error_code"}
)


class ReadRejection(StrEnum):
    """Which fail-closed check refused. ainvest-owned machine metadata."""

    # Read projection / reviewed manifest
    MALFORMED_LISTING = "malformed_listing"
    DUPLICATE_CAPABILITY = "duplicate_capability"
    ENTRY_COUNT_MISMATCH = "entry_count_mismatch"
    READ_SET_MISMATCH = "read_set_mismatch"
    MUTATION_SET_MISMATCH = "mutation_set_mismatch"
    DENIED_SET_MISMATCH = "denied_set_mismatch"
    DENIED_ENTRY_NOT_MUTATING = "denied_entry_not_mutating"
    PROJECTION_NOT_ALLOWED = "projection_not_allowed"
    PROJECTION_MUTATES = "projection_mutates"
    # Readiness
    MALFORMED_READINESS = "malformed_readiness"
    READINESS_MANIFEST_VERSION_UNSUPPORTED = "readiness_manifest_version_unsupported"
    READINESS_MANIFEST_DIGEST_MISMATCH = "readiness_manifest_digest_mismatch"
    READINESS_EXPECTED_DIGEST_MISMATCH = "readiness_expected_digest_mismatch"
    GATEWAY_NOT_READY = "gateway_not_ready"
    # Call sequencing and capability narrowing
    STARTUP_NOT_VERIFIED = "startup_not_verified"
    CAPABILITY_NOT_IN_PROJECTION = "capability_not_in_projection"
    # Result envelope
    MALFORMED_ENVELOPE = "malformed_envelope"
    ENVELOPE_KEYS_MISMATCH = "envelope_keys_mismatch"
    ENVELOPE_VERSION_UNSUPPORTED = "envelope_version_unsupported"
    ENVELOPE_MANIFEST_VERSION_UNSUPPORTED = "envelope_manifest_version_unsupported"
    ENVELOPE_MANIFEST_DIGEST_MISMATCH = "envelope_manifest_digest_mismatch"
    ENVELOPE_CAPABILITY_MISMATCH = "envelope_capability_mismatch"
    ENVELOPE_DIGEST_MALFORMED = "envelope_digest_malformed"
    ENVELOPE_OBSERVED_AT_MALFORMED = "envelope_observed_at_malformed"
    ENVELOPE_WARNINGS_INVALID = "envelope_warnings_invalid"
    PAYLOAD_NOT_OBJECT = "payload_not_object"
    PAYLOAD_TOO_DEEP = "payload_too_deep"
    PAYLOAD_TOO_MANY_NODES = "payload_too_many_nodes"
    PAYLOAD_STRING_TOO_LONG = "payload_string_too_long"
    PAYLOAD_NOT_JSON = "payload_not_json"
    PROSE_NOT_DISCARDED = "prose_not_discarded"


#: The wire strings :class:`ReadRejection` is allowed to carry.
READ_REJECTION_WIRE_NAMES: Final[dict[str, str]] = {
    "MALFORMED_LISTING": "malformed_listing",
    "DUPLICATE_CAPABILITY": "duplicate_capability",
    "ENTRY_COUNT_MISMATCH": "entry_count_mismatch",
    "READ_SET_MISMATCH": "read_set_mismatch",
    "MUTATION_SET_MISMATCH": "mutation_set_mismatch",
    "DENIED_SET_MISMATCH": "denied_set_mismatch",
    "DENIED_ENTRY_NOT_MUTATING": "denied_entry_not_mutating",
    "PROJECTION_NOT_ALLOWED": "projection_not_allowed",
    "PROJECTION_MUTATES": "projection_mutates",
    "MALFORMED_READINESS": "malformed_readiness",
    "READINESS_MANIFEST_VERSION_UNSUPPORTED": "readiness_manifest_version_unsupported",
    "READINESS_MANIFEST_DIGEST_MISMATCH": "readiness_manifest_digest_mismatch",
    "READINESS_EXPECTED_DIGEST_MISMATCH": "readiness_expected_digest_mismatch",
    "GATEWAY_NOT_READY": "gateway_not_ready",
    "STARTUP_NOT_VERIFIED": "startup_not_verified",
    "CAPABILITY_NOT_IN_PROJECTION": "capability_not_in_projection",
    "MALFORMED_ENVELOPE": "malformed_envelope",
    "ENVELOPE_KEYS_MISMATCH": "envelope_keys_mismatch",
    "ENVELOPE_VERSION_UNSUPPORTED": "envelope_version_unsupported",
    "ENVELOPE_MANIFEST_VERSION_UNSUPPORTED": "envelope_manifest_version_unsupported",
    "ENVELOPE_MANIFEST_DIGEST_MISMATCH": "envelope_manifest_digest_mismatch",
    "ENVELOPE_CAPABILITY_MISMATCH": "envelope_capability_mismatch",
    "ENVELOPE_DIGEST_MALFORMED": "envelope_digest_malformed",
    "ENVELOPE_OBSERVED_AT_MALFORMED": "envelope_observed_at_malformed",
    "ENVELOPE_WARNINGS_INVALID": "envelope_warnings_invalid",
    "PAYLOAD_NOT_OBJECT": "payload_not_object",
    "PAYLOAD_TOO_DEEP": "payload_too_deep",
    "PAYLOAD_TOO_MANY_NODES": "payload_too_many_nodes",
    "PAYLOAD_STRING_TOO_LONG": "payload_string_too_long",
    "PAYLOAD_NOT_JSON": "payload_not_json",
    "PROSE_NOT_DISCARDED": "prose_not_discarded",
}


# ---------------------------------------------------------------------------
# Structural view of the gateway. No `rh_mcp` symbol is named in this module.
# ---------------------------------------------------------------------------


class CapabilityView(Protocol):
    """One reviewed capability, as the gateway's capability listing reports it.

    Only three fields are read. The listing also carries ``description``,
    ``rationale``, and ``input_schema``; those are prose or schema material
    this adapter must not retain, so the protocol simply does not name them.
    """

    @property
    def capability(self) -> str: ...

    @property
    def read_allowed(self) -> bool: ...

    @property
    def mutates(self) -> bool: ...


class JsonDocument(Protocol):
    """Anything the gateway renders as an SDK-neutral JSON object."""

    def to_json_dict(self) -> dict[str, Any]: ...


class GatewayPort(Protocol):
    """The only gateway surface this adapter uses.

    Deliberately three members. There is no property or method here that could
    yield a session, a transport, a token, a credential store, or a raw
    provider result, so nothing downstream of this adapter can ask for one.
    """

    def capabilities(self) -> Sequence[CapabilityView]: ...

    async def readiness(self) -> JsonDocument: ...

    async def invoke(
        self,
        capability: object,
        arguments: Mapping[str, Any] | None = None,
    ) -> JsonDocument: ...


LogSink = Callable[[str, Mapping[str, object]], None]
MonotonicClock = Callable[[], float]


# ---------------------------------------------------------------------------
# Verification records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReadProjectionVerification:
    """What the reviewed manifest was proved to say at startup.

    Names, counts, and booleans only. No description, rationale, or schema
    survives into this record, so a manifest's own prose cannot reach a log or
    a prompt by way of a startup report.
    """

    manifest_read_capabilities: frozenset[str]
    approved_non_trading_mutations: frozenset[str]
    denied_trading_capabilities: frozenset[str]
    ainvest_read_projection: frozenset[str]


@dataclass(frozen=True, slots=True)
class ReadinessVerification:
    """The pinned manifest identity the gateway reported at readiness."""

    manifest_version: str
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class StartupVerification:
    """Both startup checks, in the order they must happen."""

    projection: ReadProjectionVerification
    readiness: ReadinessVerification


@dataclass(frozen=True, slots=True)
class GatewayReadResult:
    """One validated, prose-discarded read.

    ``payload`` is the envelope's ``data`` after prose removal. It is *not* a
    normalized ainvest domain object — turning it into one is P06-T1's job,
    and this is the input boundary P06-T1 consumes.
    """

    capability: str
    manifest_version: str
    manifest_digest: str
    schema_digest: str
    result_digest: str
    observed_at: str
    payload: Mapping[str, Any]
    warnings: tuple[str, ...]


# ---------------------------------------------------------------------------
# Startup verification
# ---------------------------------------------------------------------------


def verify_read_projection(
    capabilities: Sequence[CapabilityView],
) -> ReadProjectionVerification:
    """Prove the reviewed manifest still supports the ainvest read projection.

    Fails closed on any drift: a changed disposition, a changed ``mutates``
    flag, an added or removed capability, or a changed 35 / 11 / 8 split.
    """
    reads: set[str] = set()
    mutations: set[str] = set()
    denied: set[str] = set()
    seen: set[str] = set()

    for view in capabilities:
        name = getattr(view, "capability", None)
        allowed = getattr(view, "read_allowed", None)
        mutates = getattr(view, "mutates", None)
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(allowed, bool)
            or not isinstance(mutates, bool)
        ):
            raise _drift(ReadRejection.MALFORMED_LISTING)
        if name in seen:
            raise _drift(ReadRejection.DUPLICATE_CAPABILITY)
        seen.add(name)
        if not allowed:
            if not mutates:
                raise _drift(ReadRejection.DENIED_ENTRY_NOT_MUTATING)
            denied.add(name)
        elif mutates:
            mutations.add(name)
        else:
            reads.add(name)

    # The obligation `rh-mcp` does not carry for us, checked first so that it
    # is the reason reported: every capability this adapter can invoke must be
    # `allowed` AND `mutates=false` in the pinned manifest. Compared against
    # what the listing actually said, never against the pinned name sets — a
    # listing that reclassified one of our reads as a mutation is refused here
    # even in the case where the aggregate sets below would still balance.
    for member in ReadCapability:
        if member.value not in reads:
            rejection = (
                ReadRejection.PROJECTION_MUTATES
                if member.value in mutations
                else ReadRejection.PROJECTION_NOT_ALLOWED
            )
            raise _drift(rejection, capability=member.value)

    if len(seen) != EXPECTED_MANIFEST_ENTRY_COUNT:
        raise _drift(ReadRejection.ENTRY_COUNT_MISMATCH)
    if reads != MANIFEST_READ_CAPABILITIES:
        raise _drift(ReadRejection.READ_SET_MISMATCH)
    if mutations != APPROVED_NON_TRADING_MUTATIONS:
        raise _drift(ReadRejection.MUTATION_SET_MISMATCH)
    if denied != DENIED_TRADING_CAPABILITIES:
        raise _drift(ReadRejection.DENIED_SET_MISMATCH)

    return ReadProjectionVerification(
        manifest_read_capabilities=frozenset(reads),
        approved_non_trading_mutations=frozenset(mutations),
        denied_trading_capabilities=frozenset(denied),
        ainvest_read_projection=frozenset(member.value for member in ReadCapability),
    )


def verify_readiness(readiness: JsonDocument | Mapping[str, Any]) -> ReadinessVerification:
    """Verify the pinned manifest version and full-manifest digest.

    Note what is *not* required: a package-version field. Neither the gateway
    readiness report nor the result envelope carries one, and demanding one
    would fail closed against a conforming gateway. Package identity is
    verified separately, from installation metadata, in
    :mod:`ainvest.execution.robinhood.artifact`.

    ``findings`` — which the gateway also reports, and whose tool labels are
    provider-derived — is read past and never retained.
    """
    document = _as_json_document(readiness, ReadRejection.MALFORMED_READINESS)
    if not set(document) >= READINESS_KEYS:
        raise _drift(ReadRejection.MALFORMED_READINESS)

    version = document["manifest_version"]
    digest = document["manifest_digest"]
    expected = document["expected_manifest_digest"]
    ready = document["ready"]
    if (
        not isinstance(version, str)
        or not isinstance(digest, str)
        or not isinstance(expected, str)
        or not isinstance(ready, bool)
    ):
        raise _drift(ReadRejection.MALFORMED_READINESS)

    if version not in SUPPORTED_MANIFEST_VERSIONS:
        raise _drift(ReadRejection.READINESS_MANIFEST_VERSION_UNSUPPORTED)
    if digest != EXPECTED_MANIFEST_DIGEST:
        raise _drift(ReadRejection.READINESS_MANIFEST_DIGEST_MISMATCH)
    # The gateway echoes back the digest it was configured to expect. If that
    # is not ours, the gateway is enforcing a different permission contract
    # than the one this adapter pinned, even when both sides are internally
    # consistent.
    if expected != EXPECTED_MANIFEST_DIGEST:
        raise _drift(ReadRejection.READINESS_EXPECTED_DIGEST_MISMATCH)
    if not ready:
        raise _drift(ReadRejection.GATEWAY_NOT_READY)

    return ReadinessVerification(manifest_version=version, manifest_digest=digest)


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class RobinhoodReadClient:
    """The ainvest read projection over one pinned `rh-mcp` gateway.

    Every capability is reached through its own named ``read_*`` method. There
    is deliberately **no** public generic ``invoke(capability, arguments)``
    entry point: `design.md` §5.1 forbids handing one to Research, Strategy,
    Paper, Telegram, or a model, and a private one keeps the wire name a
    constant of this module rather than an argument a caller chose.
    """

    __slots__ = ("_clock", "_gateway", "_log", "_startup")

    def __init__(
        self,
        gateway: GatewayPort,
        *,
        log_sink: LogSink | None = None,
        clock: MonotonicClock = time.monotonic,
    ) -> None:
        self._gateway = gateway
        self._log: LogSink = emit_read_log if log_sink is None else log_sink
        self._clock = clock
        self._startup: StartupVerification | None = None

    @property
    def startup(self) -> StartupVerification | None:
        """The recorded startup verification, or ``None`` before it ran."""
        return self._startup

    async def verify_startup(self) -> StartupVerification:
        """Prove the read projection, then readiness. Reads are refused until
        this has succeeded."""
        self._startup = None
        try:
            projection = verify_read_projection(self._gateway.capabilities())
            readiness = verify_readiness(await self._gateway.readiness())
            verification = StartupVerification(projection=projection, readiness=readiness)
        except GatewayReadError:
            raise
        except Exception as exc:
            error = translate_gateway_failure(exc)
        else:
            self._startup = verification
            return verification

        # Raise outside the handler so the provider exception is not retained
        # as context on the sanitized ainvest error.
        raise error from None

    # -- named read operations ---------------------------------------------

    async def read_accounts(self, arguments: Mapping[str, Any] | None = None) -> GatewayReadResult:
        """Account records, including buying power."""
        return await self._read(ReadCapability.GET_ACCOUNTS, arguments)

    async def read_portfolio(self, arguments: Mapping[str, Any] | None = None) -> GatewayReadResult:
        """Portfolio totals."""
        return await self._read(ReadCapability.GET_PORTFOLIO, arguments)

    async def read_equity_positions(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        """Equity positions."""
        return await self._read(ReadCapability.GET_EQUITY_POSITIONS, arguments)

    async def read_equity_orders(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        """Order history and open orders."""
        return await self._read(ReadCapability.GET_EQUITY_ORDERS, arguments)

    async def read_equity_quotes(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        """Live quotes. The only permitted live quote source (rule 19)."""
        return await self._read(ReadCapability.GET_EQUITY_QUOTES, arguments)

    async def read_equity_price_book(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        """Level 2 price book, for spread and depth checks (rule 19)."""
        return await self._read(ReadCapability.GET_EQUITY_PRICE_BOOK, arguments)

    async def read_equity_historicals(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        """Historical OHLCV."""
        return await self._read(ReadCapability.GET_EQUITY_HISTORICALS, arguments)

    async def read_equity_fundamentals(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        """Standardized fundamentals."""
        return await self._read(ReadCapability.GET_EQUITY_FUNDAMENTALS, arguments)

    async def read_equity_tradability(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        """Symbol-keyed tradability and session/halt flags."""
        return await self._read(ReadCapability.GET_EQUITY_TRADABILITY, arguments)

    async def read_financials(
        self, arguments: Mapping[str, Any] | None = None
    ) -> GatewayReadResult:
        """Quarterly or annual financial periods."""
        return await self._read(ReadCapability.GET_FINANCIALS, arguments)

    # -- internals ---------------------------------------------------------

    async def _read(
        self,
        capability: ReadCapability,
        arguments: Mapping[str, Any] | None,
    ) -> GatewayReadResult:
        # Defence against a future refactor handing `_read` a string: the name
        # that reaches the gateway must come from this module's closed enum.
        if not isinstance(capability, ReadCapability):
            raise GatewayReadError(
                GatewayReadErrorCode.CAPABILITY_DENIED,
                rejection=ReadRejection.CAPABILITY_NOT_IN_PROJECTION.value,
            )
        startup = self._startup
        if startup is None:
            raise GatewayReadError(
                GatewayReadErrorCode.NOT_READY,
                rejection=ReadRejection.STARTUP_NOT_VERIFIED.value,
                capability=capability.value,
            )
        if capability.value not in startup.projection.ainvest_read_projection:
            raise GatewayReadError(
                GatewayReadErrorCode.CAPABILITY_DENIED,
                rejection=ReadRejection.CAPABILITY_NOT_IN_PROJECTION.value,
                capability=capability.value,
            )

        started = self._clock()
        try:
            envelope = await self._gateway.invoke(capability.value, arguments)
            result = self._validate_envelope(envelope, capability)
        except GatewayReadError as error:
            self._log_error(capability, error, started)
            raise
        except Exception as exc:
            # Every gateway failure becomes a sanitized ainvest error with no
            # provider text, no traceback chaining, and no fallback. `except
            # Exception` is the point: an unrecognized failure must still fail
            # closed rather than escape as an unknown provider type.
            translated = translate_gateway_failure(exc, capability=capability.value)
            self._log_error(capability, translated, started)
            raise translated from None

        self._log(
            READ_LOG_EVENT,
            {
                "capability": capability.value,
                "status": "ok",
                "duration_ms": self._duration_ms(started),
                "manifest_digest": EXPECTED_MANIFEST_DIGEST,
                "result_digest": result.result_digest,
            },
        )
        return result

    def _log_error(
        self,
        capability: ReadCapability,
        error: GatewayReadError,
        started: float,
    ) -> None:
        self._log(
            READ_LOG_EVENT,
            {
                "capability": capability.value,
                "status": "error",
                "duration_ms": self._duration_ms(started),
                "manifest_digest": EXPECTED_MANIFEST_DIGEST,
                "error_code": error.code.value,
            },
        )

    def _duration_ms(self, started: float) -> int:
        elapsed = (self._clock() - started) * 1000.0
        if not elapsed > 0:
            return 0
        return min(int(elapsed), MAX_LOGGED_DURATION_MS)

    def _validate_envelope(
        self,
        envelope: JsonDocument | Mapping[str, Any],
        capability: ReadCapability,
    ) -> GatewayReadResult:
        """Validate the envelope, then and only then consume its payload.

        The order below is the contract: ``envelope_version``,
        ``manifest_version``, and ``manifest_digest`` are all decided before
        ``data`` is fetched, let alone walked. A test drives this with a
        payload that raises on any access, so reordering these checks fails
        the suite rather than merely reading oddly.
        """
        fail = _envelope_failure(capability)
        document = _as_json_document(
            envelope, ReadRejection.MALFORMED_ENVELOPE, capability=capability.value
        )
        if set(document) != RESULT_ENVELOPE_KEYS:
            raise fail(ReadRejection.ENVELOPE_KEYS_MISMATCH)

        if document["envelope_version"] not in SUPPORTED_ENVELOPE_VERSIONS:
            raise fail(ReadRejection.ENVELOPE_VERSION_UNSUPPORTED)
        if document["manifest_version"] not in SUPPORTED_MANIFEST_VERSIONS:
            raise fail(ReadRejection.ENVELOPE_MANIFEST_VERSION_UNSUPPORTED)
        if document["manifest_digest"] != EXPECTED_MANIFEST_DIGEST:
            raise fail(ReadRejection.ENVELOPE_MANIFEST_DIGEST_MISMATCH)
        if document["capability"] != capability.value:
            raise fail(ReadRejection.ENVELOPE_CAPABILITY_MISMATCH)

        schema_digest = document["schema_digest"]
        result_digest = document["result_digest"]
        if not _is_digest(schema_digest) or not _is_digest(result_digest):
            raise fail(ReadRejection.ENVELOPE_DIGEST_MALFORMED)

        observed_at = document["observed_at"]
        if not _is_rfc3339_datetime(observed_at):
            raise fail(ReadRejection.ENVELOPE_OBSERVED_AT_MALFORMED)

        warnings = _validated_warnings(document["warnings"], fail)

        data = document["data"]
        if not isinstance(data, Mapping):
            raise fail(ReadRejection.PAYLOAD_NOT_OBJECT)
        _enforce_payload_bounds(data, fail)

        payload = discard_provider_prose(data)
        if contains_provider_prose(payload):
            raise fail(ReadRejection.PROSE_NOT_DISCARDED)

        return GatewayReadResult(
            capability=capability.value,
            manifest_version=document["manifest_version"],
            manifest_digest=document["manifest_digest"],
            schema_digest=schema_digest,
            result_digest=result_digest,
            observed_at=observed_at,
            payload=payload,
            warnings=warnings,
        )


def emit_read_log(event: str, fields: Mapping[str, object]) -> None:
    """Default sink: the repository's redacted structured logger."""
    get_logger("execution.robinhood").info(event, **fields)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drift(rejection: ReadRejection, *, capability: str | None = None) -> GatewayReadError:
    return GatewayReadError(
        GatewayReadErrorCode.NOT_READY,
        rejection=rejection.value,
        capability=capability,
    )


def _envelope_failure(
    capability: ReadCapability,
) -> Callable[[ReadRejection], GatewayReadError]:
    def fail(rejection: ReadRejection) -> GatewayReadError:
        code = (
            GatewayReadErrorCode.RESPONSE_TOO_LARGE
            if rejection
            in {
                ReadRejection.PAYLOAD_TOO_DEEP,
                ReadRejection.PAYLOAD_TOO_MANY_NODES,
                ReadRejection.PAYLOAD_STRING_TOO_LONG,
            }
            else GatewayReadErrorCode.ENVELOPE_INVALID
        )
        return GatewayReadError(code, rejection=rejection.value, capability=capability.value)

    return fail


def _as_json_document(
    value: JsonDocument | Mapping[str, Any],
    rejection: ReadRejection,
    *,
    capability: str | None = None,
) -> Mapping[str, Any]:
    """Render an SDK-neutral document without importing the SDK's types."""
    if isinstance(value, Mapping):
        return value
    render = getattr(value, "to_json_dict", None)
    if callable(render):
        rendered = render()
        if isinstance(rendered, Mapping):
            return rendered
    raise GatewayReadError(
        GatewayReadErrorCode.ENVELOPE_INVALID,
        rejection=rejection.value,
        capability=capability,
    )


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and DIGEST_PATTERN.fullmatch(value) is not None


def _is_rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str) or RFC3339_DATETIME_PATTERN.fullmatch(value) is None:
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _validated_warnings(
    value: object,
    fail: Callable[[ReadRejection], GatewayReadError],
) -> tuple[str, ...]:
    """Bound the gateway's own warning strings.

    These are `rh-mcp`-authored, not provider-authored, but they are still
    text this adapter passes on, so they are bounded in count and length and
    never logged.
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise fail(ReadRejection.ENVELOPE_WARNINGS_INVALID)
    if len(value) > MAX_ENVELOPE_WARNINGS:
        raise fail(ReadRejection.ENVELOPE_WARNINGS_INVALID)
    warnings: list[str] = []
    for item in value:
        if not isinstance(item, str) or len(item) > MAX_WARNING_LENGTH:
            raise fail(ReadRejection.ENVELOPE_WARNINGS_INVALID)
        warnings.append(item)
    return tuple(warnings)


def _enforce_payload_bounds(
    data: Mapping[str, Any],
    fail: Callable[[ReadRejection], GatewayReadError],
) -> None:
    """Require bounded JSON-compatible data at this trust boundary."""
    nodes = 0

    def walk(value: Any, depth: int, active_containers: set[int]) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_PAYLOAD_NODES:
            raise fail(ReadRejection.PAYLOAD_TOO_MANY_NODES)
        if depth > MAX_PAYLOAD_DEPTH:
            raise fail(ReadRejection.PAYLOAD_TOO_DEEP)

        if isinstance(value, str):
            if len(value) > MAX_PAYLOAD_STRING_LENGTH:
                raise fail(ReadRejection.PAYLOAD_STRING_TOO_LONG)
            return
        if value is None or isinstance(value, (bool, int)):
            return
        if isinstance(value, float):
            if not isfinite(value):
                raise fail(ReadRejection.PAYLOAD_NOT_JSON)
            return
        if not isinstance(value, (Mapping, list, tuple)):
            raise fail(ReadRejection.PAYLOAD_NOT_JSON)

        identity = id(value)
        if identity in active_containers:
            raise fail(ReadRejection.PAYLOAD_NOT_JSON)
        active_containers.add(identity)
        try:
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise fail(ReadRejection.PAYLOAD_NOT_JSON)
                    if len(key) > MAX_PAYLOAD_STRING_LENGTH:
                        raise fail(ReadRejection.PAYLOAD_STRING_TOO_LONG)
                    walk(item, depth + 1, active_containers)
            else:
                for item in value:
                    walk(item, depth + 1, active_containers)
        finally:
            active_containers.remove(identity)

    walk(data, 0, set())


__all__ = [
    "DIGEST_PATTERN",
    "READ_LOG_EVENT",
    "READ_LOG_FIELDS_ERROR",
    "READ_LOG_FIELDS_OK",
    "READ_REJECTION_WIRE_NAMES",
    "CapabilityView",
    "GatewayPort",
    "GatewayReadResult",
    "JsonDocument",
    "LogSink",
    "MonotonicClock",
    "ReadProjectionVerification",
    "ReadRejection",
    "ReadinessVerification",
    "RobinhoodReadClient",
    "StartupVerification",
    "emit_read_log",
    "verify_read_projection",
    "verify_readiness",
]
