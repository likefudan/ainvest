"""Stable sanitized error contract for the Robinhood read adapter (P06-T0).

Nothing produced here carries gateway or provider text. `rh-mcp`
``DESIGN.md`` §12.5 pins the nine ``ErrorCode`` wire strings and
``GatewayError``'s public field set (``code``, ``message``, ``retryable``,
``correlation_id``) but explicitly leaves ``message`` free to change in any
release, including a patch, with no changelog entry — and ``message`` is also
one of the places provider-controlled prose could surface. So this module
branches on ``code`` and ``retryable`` only, and every message ainvest emits
is an ainvest constant chosen by code.

§12.5 also records that ``correlation_id`` is public but is never populated by
any code in the package, so nothing here requires it. And it records that
there is deliberately no ``GatewayError.to_json_dict()`` and none is planned,
so nothing here expects a serialized gateway error.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class GatewayReadErrorCode(StrEnum):
    """Stable ainvest-side codes. Callers branch on these, never on text."""

    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    NOT_READY = "not_ready"
    CAPABILITY_DENIED = "capability_denied"
    ENVELOPE_INVALID = "envelope_invalid"
    INPUT_INVALID = "input_invalid"
    AUTH_REQUIRED = "auth_required"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RESPONSE_TOO_LARGE = "response_too_large"


#: The wire strings :class:`GatewayReadErrorCode` is allowed to carry, as a
#: literal table. ``str(member) == member.value`` would only prove ``StrEnum``
#: works; this is what a rename of either side has to survive.
GATEWAY_READ_ERROR_WIRE_NAMES: Final[dict[str, str]] = {
    "DEPENDENCY_UNAVAILABLE": "dependency_unavailable",
    "NOT_READY": "not_ready",
    "CAPABILITY_DENIED": "capability_denied",
    "ENVELOPE_INVALID": "envelope_invalid",
    "INPUT_INVALID": "input_invalid",
    "AUTH_REQUIRED": "auth_required",
    "TIMEOUT": "timeout",
    "PROVIDER_UNAVAILABLE": "provider_unavailable",
    "RESPONSE_TOO_LARGE": "response_too_large",
}

#: The nine `rh-mcp` ``ErrorCode`` wire strings pinned by ``DESIGN.md`` §12.5.
#: Transcribed, not imported: this adapter must keep working as a contract
#: statement whether or not the dependency is installed.
RH_MCP_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "auth_required",
        "not_ready",
        "capability_denied",
        "input_invalid",
        "provider_error",
        "timeout",
        "response_too_large",
        "protocol_error",
        "configuration_error",
    }
)

#: How each pinned gateway code becomes an ainvest code.
RH_MCP_ERROR_CODE_MAP: Final[dict[str, GatewayReadErrorCode]] = {
    "auth_required": GatewayReadErrorCode.AUTH_REQUIRED,
    "not_ready": GatewayReadErrorCode.NOT_READY,
    "configuration_error": GatewayReadErrorCode.NOT_READY,
    "capability_denied": GatewayReadErrorCode.CAPABILITY_DENIED,
    "input_invalid": GatewayReadErrorCode.INPUT_INVALID,
    "provider_error": GatewayReadErrorCode.PROVIDER_UNAVAILABLE,
    "timeout": GatewayReadErrorCode.TIMEOUT,
    "response_too_large": GatewayReadErrorCode.RESPONSE_TOO_LARGE,
    "protocol_error": GatewayReadErrorCode.ENVELOPE_INVALID,
}

#: Where an unrecognized or absent gateway code lands. Fail closed and
#: non-retryable: "I do not understand this failure" must never become
#: "retry", and must never become "try a different data provider" (DEC-003 —
#: there is no automatic data-provider fallback, ever).
UNMAPPED_GATEWAY_FAILURE: Final = GatewayReadErrorCode.PROVIDER_UNAVAILABLE

#: One fixed ainvest sentence per code. Never interpolated with gateway,
#: provider, capability-argument, or account text.
SANITIZED_MESSAGES: Final[dict[GatewayReadErrorCode, str]] = {
    GatewayReadErrorCode.DEPENDENCY_UNAVAILABLE: (
        "the pinned rh-mcp gateway release is not installed or does not match its pins"
    ),
    GatewayReadErrorCode.NOT_READY: (
        "the rh-mcp gateway is not ready under the pinned manifest version and digest"
    ),
    GatewayReadErrorCode.CAPABILITY_DENIED: (
        "the requested capability is outside the ainvest read projection"
    ),
    GatewayReadErrorCode.ENVELOPE_INVALID: (
        "the gateway result envelope failed ainvest validation"
    ),
    GatewayReadErrorCode.INPUT_INVALID: (
        "the read arguments were rejected by the pinned capability input schema"
    ),
    GatewayReadErrorCode.AUTH_REQUIRED: (
        "the rh-mcp gateway requires an operator-completed Robinhood authorization"
    ),
    GatewayReadErrorCode.TIMEOUT: "the rh-mcp gateway read exceeded its deadline",
    GatewayReadErrorCode.PROVIDER_UNAVAILABLE: "the rh-mcp gateway read did not succeed",
    GatewayReadErrorCode.RESPONSE_TOO_LARGE: (
        "the rh-mcp gateway result exceeded the ainvest payload bounds"
    ),
}

#: Only a deadline is retryable, and only against the same gateway. Nothing
#: here authorizes retrying against a different quote source.
RETRYABLE_CODES: Final[frozenset[GatewayReadErrorCode]] = frozenset({GatewayReadErrorCode.TIMEOUT})


class GatewayReadError(Exception):
    """A read failure, sanitized before construction.

    ``capability`` is optional and, when present, is always one of ainvest's
    own :class:`~ainvest.execution.robinhood.pins.ReadCapability` wire strings
    — never a caller-supplied or provider-supplied tool name.

    ``rejection`` is an optional ainvest-owned machine reason (a
    :class:`~ainvest.execution.robinhood.read_client.ReadRejection` or
    :class:`~ainvest.execution.robinhood.artifact.ArtifactRejection` wire
    string). It exists so a caller — and a test — can distinguish *which*
    fail-closed check refused, without any of them carrying gateway or
    provider text.
    """

    __slots__ = ("capability", "code", "rejection")

    def __init__(
        self,
        code: GatewayReadErrorCode,
        *,
        rejection: str | None = None,
        capability: str | None = None,
    ) -> None:
        super().__init__(SANITIZED_MESSAGES[code])
        self.code = code
        self.rejection = rejection
        self.capability = capability

    @property
    def message(self) -> str:
        """The fixed ainvest sentence for :attr:`code`."""
        return SANITIZED_MESSAGES[self.code]

    @property
    def retryable(self) -> bool:
        """Whether retrying *this same gateway* is permitted."""
        return self.code in RETRYABLE_CODES

    def __repr__(self) -> str:
        return (
            f"GatewayReadError(code={self.code.value!r}, rejection={self.rejection!r}, "
            f"capability={self.capability!r})"
        )


def translate_gateway_failure(
    exc: BaseException,
    *,
    capability: str | None = None,
) -> GatewayReadError:
    """Map any gateway-raised exception onto a sanitized ainvest error.

    Reads ``code`` and nothing else. ``message``, ``args``, and the traceback
    are never inspected, copied, or chained onward, because each of them can
    carry gateway or provider text this adapter is required to discard.
    """
    raw = getattr(exc, "code", None)
    if isinstance(raw, StrEnum):
        key: str | None = raw.value
    elif isinstance(raw, str):
        key = raw
    else:
        key = None
    code = UNMAPPED_GATEWAY_FAILURE if key is None else RH_MCP_ERROR_CODE_MAP.get(key)
    if code is None:
        code = UNMAPPED_GATEWAY_FAILURE
    return GatewayReadError(code, capability=capability)


__all__ = [
    "GATEWAY_READ_ERROR_WIRE_NAMES",
    "RETRYABLE_CODES",
    "RH_MCP_ERROR_CODES",
    "RH_MCP_ERROR_CODE_MAP",
    "SANITIZED_MESSAGES",
    "UNMAPPED_GATEWAY_FAILURE",
    "GatewayReadError",
    "GatewayReadErrorCode",
    "translate_gateway_failure",
]
