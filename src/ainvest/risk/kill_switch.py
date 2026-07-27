"""Kill switch for blocking new order submissions (P03-T12 / DEC-008).

Configured and operational sources are independent. Any active source blocks
new orders and records an alert. Automatic cancellation of existing orders is
intentionally unsupported until a separate owner decision (DEC-019).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints

from ainvest.risk.models import KillSwitchSnapshot
from ainvest.schemas.common import DomainModel, UtcDateTime, ensure_utc


class KillSwitchAlertKind(StrEnum):
    """Alert kinds emitted when the kill switch activates or blocks."""

    ACTIVATED = "ACTIVATED"
    BLOCKED_NEW_ORDER = "BLOCKED_NEW_ORDER"
    DEACTIVATED = "DEACTIVATED"


class KillSwitchAlert(DomainModel):
    """Non-secret alert payload for operators / audit handoff."""

    kind: KillSwitchAlertKind
    reason: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    observed_at: UtcDateTime
    sources: tuple[str, ...] = ()
    operator_id: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None


class KillSwitch:
    """In-process kill switch with configured + operational sources.

    Does not cancel, mutate, or enumerate open orders. Callers must alert on
    :meth:`drain_alerts` and reject new submissions when :meth:`is_active`.
    """

    def __init__(self, *, configured_active: bool = False) -> None:
        self._configured_active = configured_active
        self._operational_active = False
        self._reason: str | None = None
        self._updated_at: datetime | None = None
        self._alerts: list[KillSwitchAlert] = []

    @property
    def configured_active(self) -> bool:
        return self._configured_active

    @property
    def operational_active(self) -> bool:
        return self._operational_active

    def is_active(self) -> bool:
        return self._configured_active or self._operational_active

    def set_configured(self, active: bool, *, reason: str, as_of: datetime) -> None:
        """Flip the configured (policy) source and emit an alert on change."""
        clock = ensure_utc(as_of)
        if active == self._configured_active:
            self._reason = reason
            self._updated_at = clock
            return
        self._configured_active = active
        self._reason = reason
        self._updated_at = clock
        kind = KillSwitchAlertKind.ACTIVATED if active else KillSwitchAlertKind.DEACTIVATED
        self._alerts.append(
            KillSwitchAlert(
                kind=kind,
                reason=reason,
                observed_at=clock,
                sources=self.snapshot().active_sources or ("CONFIGURED",),
            )
        )

    def activate_operational(
        self,
        *,
        reason: str,
        as_of: datetime,
        operator_id: str | None = None,
    ) -> None:
        """Activate the operational source (DEC-008: block new orders + alert)."""
        clock = ensure_utc(as_of)
        already = self._operational_active
        self._operational_active = True
        self._reason = reason
        self._updated_at = clock
        if already:
            return
        self._alerts.append(
            KillSwitchAlert(
                kind=KillSwitchAlertKind.ACTIVATED,
                reason=reason,
                observed_at=clock,
                sources=("OPERATIONAL",),
                operator_id=operator_id,
            )
        )

    def deactivate_operational(
        self,
        *,
        reason: str,
        as_of: datetime,
        operator_id: str | None = None,
    ) -> None:
        clock = ensure_utc(as_of)
        already_off = not self._operational_active
        self._operational_active = False
        self._reason = reason
        self._updated_at = clock
        if already_off:
            return
        self._alerts.append(
            KillSwitchAlert(
                kind=KillSwitchAlertKind.DEACTIVATED,
                reason=reason,
                observed_at=clock,
                sources=self.snapshot().active_sources,
                operator_id=operator_id,
            )
        )

    def snapshot(self) -> KillSwitchSnapshot:
        return KillSwitchSnapshot(
            configured_active=self._configured_active,
            operational_active=self._operational_active,
            reason=self._reason,
            updated_at=self._updated_at,
        )

    def record_blocked_submission(self, *, reason: str, as_of: datetime) -> None:
        """Record that a new-order attempt was blocked while active."""
        if not self.is_active():
            return
        self._alerts.append(
            KillSwitchAlert(
                kind=KillSwitchAlertKind.BLOCKED_NEW_ORDER,
                reason=reason,
                observed_at=ensure_utc(as_of),
                sources=self.snapshot().active_sources,
            )
        )

    def drain_alerts(self) -> tuple[KillSwitchAlert, ...]:
        alerts = tuple(self._alerts)
        self._alerts.clear()
        return alerts

    def cancel_open_orders(self) -> None:
        """Unsupported: DEC-008 / DEC-019 forbid automatic cancellation here."""
        raise RuntimeError(
            "kill switch does not auto-cancel open orders; "
            "use the operator cancel path after an accepted DEC-019 policy"
        )


__all__ = [
    "KillSwitch",
    "KillSwitchAlert",
    "KillSwitchAlertKind",
]
