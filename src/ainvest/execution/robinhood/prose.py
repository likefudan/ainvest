"""Discard provider-controlled prose at the gateway boundary (P06-T0).

The independent `rh-mcp` reviews record this as an ainvest consumer
requirement and standing residual risk: provider ``guide`` text, tool
``description`` text, and JSON-Schema ``description`` text ride inside result
envelopes **and** inside the reviewed manifest's own entries. `rh-mcp` does
not execute that prose and does not strip it. It is provider-controlled
prompt-injection material, and it must not reach a model prompt, Telegram, CLI
output, or a log.

The delivery path is concrete rather than hypothetical. Every one of the 55
reviewed ``v0.4.1`` capabilities declares an output schema of the shape
``{"data": {...}, "guide": ...}`` with **both** keys ``required``, so every
successful read carries a provider ``guide`` sibling of its payload; and the
schemas nested under ``data`` carry ``description`` on individual fields.

So the rule here is structural and applies at every depth: a mapping key in
:data:`PROVIDER_PROSE_KEYS` is dropped, key and value together.

Two consequences are deliberate and are the fail-closed direction:

* A *legitimate* field named ``description`` in a provider payload is dropped
  too. P06-T1 owns normalization into ainvest schemas and can carry any field
  it decides is data rather than prose — but it has to decide that explicitly,
  which is the point. Silently forwarding provider free text is the failure
  this module exists to prevent.
* Dropping happens before anything is returned, logged, or rendered, not at
  the sink. A sink-side filter would have to be repeated at every future sink;
  P06-T2 adds two more (CLI, Telegram).

:func:`contains_provider_prose` exists so the adapter can assert its own
post-condition rather than trusting that it remembered to call the scrubber.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from ainvest.execution.robinhood.errors import GatewayReadError, GatewayReadErrorCode

#: Mapping keys whose values are provider-authored free text.
#:
#: ``guide`` is the per-result provider narrative. ``description`` is both the
#: manifest entry's tool description and every JSON-Schema field description.
#: ``rationale`` is the human reviewer's manifest note, which is not provider
#: controlled but is still prose with no place in a payload, a log line, or a
#: prompt.
PROVIDER_PROSE_KEYS: Final[frozenset[str]] = frozenset({"description", "guide", "rationale"})

#: Structural rail so a hostile or cyclic structure raises the sanitized error
#: contract instead of ``RecursionError``. Independent of the payload bounds
#: check: this function must not depend on a caller having walked first.
MAX_PROSE_WALK_DEPTH: Final = 64


def discard_provider_prose(
    value: Any,
    *,
    code: GatewayReadErrorCode = GatewayReadErrorCode.ENVELOPE_INVALID,
) -> Any:
    """Return ``value`` with every :data:`PROVIDER_PROSE_KEYS` entry removed.

    Mappings become plain ``dict``, sequences become plain ``list``, so the
    result is detached from the caller's objects and cannot be mutated back
    into carrying prose.
    """
    return _walk(value, code=code, depth=0)


def contains_provider_prose(
    value: Any,
    *,
    code: GatewayReadErrorCode = GatewayReadErrorCode.ENVELOPE_INVALID,
) -> bool:
    """Whether any :data:`PROVIDER_PROSE_KEYS` key survives at any depth."""
    return _find(value, code=code, depth=0)


def _walk(value: Any, *, code: GatewayReadErrorCode, depth: int) -> Any:
    if depth > MAX_PROSE_WALK_DEPTH:
        raise GatewayReadError(code)
    if isinstance(value, Mapping):
        return {
            key: _walk(item, code=code, depth=depth + 1)
            for key, item in value.items()
            if not (isinstance(key, str) and key in PROVIDER_PROSE_KEYS)
        }
    if isinstance(value, (str, bytes)):
        return value
    if isinstance(value, Sequence):
        return [_walk(item, code=code, depth=depth + 1) for item in value]
    return value


def _find(value: Any, *, code: GatewayReadErrorCode, depth: int) -> bool:
    if depth > MAX_PROSE_WALK_DEPTH:
        raise GatewayReadError(code)
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key in PROVIDER_PROSE_KEYS:
                return True
            if _find(item, code=code, depth=depth + 1):
                return True
        return False
    if isinstance(value, (str, bytes)):
        return False
    if isinstance(value, Sequence):
        return any(_find(item, code=code, depth=depth + 1) for item in value)
    return False


__all__ = [
    "MAX_PROSE_WALK_DEPTH",
    "PROVIDER_PROSE_KEYS",
    "contains_provider_prose",
    "discard_provider_prose",
]
