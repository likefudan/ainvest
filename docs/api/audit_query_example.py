"""Documentation-only query recipe; no endpoint, credentials or database opening.

The caller supplies an already authenticated, scoped, audited authorization gate.
Passing a no-op gate is NOT authentication. See README.md before adapting this.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import SecretBytes, SecretStr

from ainvest.audit.envelope import AuditEventType
from ainvest.audit.service import AuditService
from ainvest.execution.state_machine import CancelCommandState, OrderLifecycleState
from ainvest.observability.alerts import redacted_reference

type Selector = Literal["proposal", "correlation"]
type AuthorizationGate = Callable[[Selector, str], bool]


def _state(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    allowed = {item.value for item in OrderLifecycleState} | {
        item.value for item in CancelCommandState
    }
    return status if isinstance(status, str) and status in allowed else None


def query_timeline(
    audit: AuditService,
    *,
    selector: Selector,
    identifier: str,
    authorize: AuthorizationGate,
    key: SecretBytes,
) -> list[dict[str, str | int | None]]:
    """Read exactly one authorized selector; export only an allowlisted projection.

    This is a local composition example, not a production authorization boundary.
    The gate must return exactly True; any denial/failure precedes database reads.
    """
    if selector not in ("proposal", "correlation") or not identifier.strip():
        raise ValueError("invalid audit selector")
    if len(key.get_secret_value()) < 32:
        raise ValueError("invalid redaction key")
    if authorize(selector, identifier) is not True:
        raise PermissionError("audit access denied")
    rows = (
        audit.timeline_for_proposal(identifier)
        if selector == "proposal"
        else audit.list_by_correlation(identifier)
    )

    def reference(value: str | None) -> str | None:
        return None if value is None else redacted_reference(SecretStr(value), key=key)

    allowed_events = {kind.value for kind in AuditEventType}
    return [
        {
            "position": index,
            "event_ref": reference(row.event_id),
            "event_type": row.event_type if row.event_type in allowed_events else "OTHER",
            "correlation_ref": reference(row.correlation_id),
            "causation_ref": reference(row.causation_id),
            "before": _state(row.before_state),
            "after": _state(row.after_state),
        }
        for index, row in enumerate(rows)
    ]
