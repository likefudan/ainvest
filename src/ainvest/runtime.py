"""Fail-closed runtime modes and startup capability gates.

This module is the composition boundary for Research, Paper, and Live
processes.  It describes the packages, secret classes, broker capabilities,
and scheduler jobs that each mode may receive, then validates concrete broker
capabilities before returning a ready runtime.

Live writes can only be constructed by a :class:`LiveGuard`.  The provided
guard rejects every request; P07-T4 may later provide a production guard
without weakening the mode matrix here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from ainvest.config import Settings, TradingMode
from ainvest.execution.broker import (
    READ_METHOD_NAMES,
    BrokerReadPort,
    BrokerWritePort,
    assert_no_replace_operation,
    assert_read_port_has_no_write_methods,
)


class RuntimePackage(StrEnum):
    """Loadable application package groups exposed at the composition root."""

    DATA = "data"
    RESEARCH_AGENT = "agents.research"
    STRATEGY_EXECUTION = "strategies.execution"
    RISK_ENGINE = "risk"
    APPROVAL_SERVICE = "approval"
    EXECUTION_SERVICE = "execution"
    TELEGRAM_PAPER_APPROVAL = "approval.telegram.paper"
    WEBAUTHN_LIVE_APPROVAL = "approval.webauthn.live"


class RuntimeSecret(StrEnum):
    """Secret classes a mode may request; never the secret values themselves."""

    OPENAI_API = "openai_api"
    DATA_PROVIDER = "data_provider"
    TELEGRAM_BOT = "telegram_bot"
    ROBINHOOD_READ = "robinhood_read"
    WEBAUTHN_SERVER = "webauthn_server"
    ROBINHOOD_WRITE = "robinhood_write"


class BrokerCapability(StrEnum):
    """Broker authority available to a runtime."""

    READ_ONLY = "read_only"
    PAPER_WRITE = "paper_write"
    ROBINHOOD_WRITE = "robinhood_write"


class SchedulerJob(StrEnum):
    """Job classes a mode may schedule once P08-T1 supplies scheduling."""

    RESEARCH = "research"
    STRATEGY_EVALUATION = "strategy_evaluation"
    PAPER_EXECUTION = "paper_execution"
    LIVE_EXECUTION = "live_execution"
    RECONCILIATION = "reconciliation"


@dataclass(frozen=True, slots=True)
class ModeCapabilities:
    """The complete allowed capability set for one runtime mode."""

    packages: frozenset[RuntimePackage]
    secrets: frozenset[RuntimeSecret]
    broker: frozenset[BrokerCapability]
    scheduler_jobs: frozenset[SchedulerJob]


_RESEARCH_CAPABILITIES = ModeCapabilities(
    packages=frozenset(
        {
            RuntimePackage.DATA,
            RuntimePackage.RESEARCH_AGENT,
        }
    ),
    secrets=frozenset(
        {
            RuntimeSecret.OPENAI_API,
            RuntimeSecret.DATA_PROVIDER,
        }
    ),
    broker=frozenset(),
    scheduler_jobs=frozenset({SchedulerJob.RESEARCH}),
)

_PAPER_CAPABILITIES = ModeCapabilities(
    packages=frozenset(
        {
            RuntimePackage.DATA,
            RuntimePackage.RESEARCH_AGENT,
            RuntimePackage.STRATEGY_EXECUTION,
            RuntimePackage.RISK_ENGINE,
            RuntimePackage.APPROVAL_SERVICE,
            RuntimePackage.EXECUTION_SERVICE,
            RuntimePackage.TELEGRAM_PAPER_APPROVAL,
        }
    ),
    secrets=frozenset(
        {
            RuntimeSecret.OPENAI_API,
            RuntimeSecret.DATA_PROVIDER,
            RuntimeSecret.TELEGRAM_BOT,
            RuntimeSecret.ROBINHOOD_READ,
        }
    ),
    broker=frozenset(
        {
            BrokerCapability.READ_ONLY,
            BrokerCapability.PAPER_WRITE,
        }
    ),
    scheduler_jobs=frozenset(
        {
            SchedulerJob.RESEARCH,
            SchedulerJob.STRATEGY_EVALUATION,
            SchedulerJob.PAPER_EXECUTION,
            SchedulerJob.RECONCILIATION,
        }
    ),
)

_LIVE_CAPABILITIES = ModeCapabilities(
    packages=frozenset(
        {
            RuntimePackage.DATA,
            RuntimePackage.RESEARCH_AGENT,
            RuntimePackage.STRATEGY_EXECUTION,
            RuntimePackage.RISK_ENGINE,
            RuntimePackage.APPROVAL_SERVICE,
            RuntimePackage.EXECUTION_SERVICE,
            RuntimePackage.WEBAUTHN_LIVE_APPROVAL,
        }
    ),
    secrets=frozenset(
        {
            RuntimeSecret.OPENAI_API,
            RuntimeSecret.DATA_PROVIDER,
            RuntimeSecret.ROBINHOOD_READ,
            RuntimeSecret.WEBAUTHN_SERVER,
            RuntimeSecret.ROBINHOOD_WRITE,
        }
    ),
    broker=frozenset(
        {
            BrokerCapability.READ_ONLY,
            BrokerCapability.ROBINHOOD_WRITE,
        }
    ),
    scheduler_jobs=frozenset(
        {
            SchedulerJob.RESEARCH,
            SchedulerJob.STRATEGY_EVALUATION,
            SchedulerJob.LIVE_EXECUTION,
            SchedulerJob.RECONCILIATION,
        }
    ),
)

# One immutable authority for every runtime capability decision.
MODE_CAPABILITY_MATRIX: Mapping[TradingMode, ModeCapabilities] = MappingProxyType(
    {
        TradingMode.RESEARCH: _RESEARCH_CAPABILITIES,
        TradingMode.PAPER: _PAPER_CAPABILITIES,
        TradingMode.LIVE: _LIVE_CAPABILITIES,
    }
)


class RuntimeStartupErrorCode(StrEnum):
    """Stable codes for startup failures; callers must not parse messages."""

    INVALID_SETTINGS = "RUNTIME_INVALID_SETTINGS"
    CAPABILITY_NOT_ALLOWED = "RUNTIME_CAPABILITY_NOT_ALLOWED"
    PAPER_BROKER_REQUIRED = "RUNTIME_PAPER_BROKER_REQUIRED"
    INVALID_BROKER_PORT = "RUNTIME_INVALID_BROKER_PORT"
    LIVE_GUARD_REQUIRED = "RUNTIME_LIVE_GUARD_REQUIRED"
    LIVE_GUARD_NOT_PRODUCTION_READY = "RUNTIME_LIVE_GUARD_NOT_PRODUCTION_READY"
    LIVE_GUARD_REJECTED = "RUNTIME_LIVE_GUARD_REJECTED"
    LIVE_WRITE_FACTORY_REQUIRED = "RUNTIME_LIVE_WRITE_FACTORY_REQUIRED"


class RuntimeStartupError(RuntimeError):
    """Fail-closed startup error with a stable machine-readable code."""

    def __init__(self, message: str, *, code: RuntimeStartupErrorCode) -> None:
        self.code = code
        super().__init__(message)


LiveWriteFactory = Callable[[], BrokerWritePort]


@runtime_checkable
class LiveGuard(Protocol):
    """Construct the isolated live write capability after all live gates pass.

    A guard controls whether ``factory`` is invoked.  Implementations must
    validate the independent live prerequisites from P07-T4 and must raise
    :class:`RuntimeStartupError` when any prerequisite is absent or uncertain.
    """

    @property
    def production_ready(self) -> bool:
        """Whether this implementation completed the P07-T4 production gate."""
        ...

    def construct_write_capability(
        self,
        *,
        settings: Settings,
        factory: LiveWriteFactory,
    ) -> BrokerWritePort:
        """Return a write-only broker port, or reject startup."""
        ...


class RejectingLiveGuard:
    """Default LiveGuard: never invoke the factory and always reject."""

    @property
    def production_ready(self) -> bool:
        """The built-in guard is intentionally never production-ready."""
        return False

    def construct_write_capability(
        self,
        *,
        settings: Settings,
        factory: LiveWriteFactory,
    ) -> BrokerWritePort:
        del settings, factory
        raise RuntimeStartupError(
            "Live writes remain disabled until the production LiveGuard is installed",
            code=RuntimeStartupErrorCode.LIVE_GUARD_REJECTED,
        )


@dataclass(frozen=True, slots=True)
class Runtime:
    """Validated runtime capabilities returned by :func:`start_runtime`."""

    mode: TradingMode
    capabilities: ModeCapabilities
    active_broker_capabilities: frozenset[BrokerCapability]
    broker_read: BrokerReadPort | None
    broker_write: BrokerWritePort | None

    def health_summary(self) -> dict[str, object]:
        """Return a deterministic health payload with all secrets redacted."""
        return {
            "status": "ready",
            "mode": self.mode.value,
            "capabilities": {
                "packages_allowed": sorted(item.value for item in self.capabilities.packages),
                "broker_active": sorted(item.value for item in self.active_broker_capabilities),
                "scheduler_jobs_allowed": sorted(
                    item.value for item in self.capabilities.scheduler_jobs
                ),
                "secret_classes_allowed": {
                    item.value: "[REDACTED]"
                    for item in sorted(self.capabilities.secrets, key=lambda item: item.value)
                },
            },
        }


def _validate_settings(settings: Settings) -> None:
    if not settings.regular_trading_hours_only:
        raise RuntimeStartupError(
            "Regular-hours-only must remain enabled (DEC-001)",
            code=RuntimeStartupErrorCode.INVALID_SETTINGS,
        )
    if not settings.require_complete_risk_limits:
        raise RuntimeStartupError(
            "Complete risk limits must remain required (DEC-002)",
            code=RuntimeStartupErrorCode.INVALID_SETTINGS,
        )
    if settings.trading_mode is TradingMode.LIVE:
        if not settings.live_trading_enabled or not settings.require_human_approval:
            raise RuntimeStartupError(
                "Live mode requires its enable flag and human approval",
                code=RuntimeStartupErrorCode.INVALID_SETTINGS,
            )
    elif settings.live_trading_enabled:
        raise RuntimeStartupError(
            "Only Live mode may enable live trading",
            code=RuntimeStartupErrorCode.INVALID_SETTINGS,
        )


def _validate_read_port(port: BrokerReadPort | None) -> None:
    if port is None:
        return
    if not isinstance(port, BrokerReadPort):
        raise RuntimeStartupError(
            "broker_read must implement BrokerReadPort",
            code=RuntimeStartupErrorCode.INVALID_BROKER_PORT,
        )
    try:
        assert_read_port_has_no_write_methods(port)
    except AssertionError as exc:
        raise RuntimeStartupError(
            "broker_read must not expose write methods",
            code=RuntimeStartupErrorCode.CAPABILITY_NOT_ALLOWED,
        ) from exc


def _validate_write_port(port: object) -> BrokerWritePort:
    if not isinstance(port, BrokerWritePort):
        raise RuntimeStartupError(
            "broker write capability must implement BrokerWritePort",
            code=RuntimeStartupErrorCode.INVALID_BROKER_PORT,
        )
    try:
        assert_no_replace_operation(port)
    except AssertionError as exc:
        raise RuntimeStartupError(
            "broker write capability must not expose in-place replacement",
            code=RuntimeStartupErrorCode.CAPABILITY_NOT_ALLOWED,
        ) from exc
    for name in READ_METHOD_NAMES:
        if callable(getattr(port, name, None)):
            raise RuntimeStartupError(
                "broker write capability must not expose read methods",
                code=RuntimeStartupErrorCode.CAPABILITY_NOT_ALLOWED,
            )
    return port


def start_runtime(
    settings: Settings,
    *,
    broker_read: BrokerReadPort | None = None,
    paper_broker: object | None = None,
    live_guard: LiveGuard | None = None,
    live_write_factory: LiveWriteFactory | None = None,
) -> Runtime:
    """Validate mode-specific capabilities and build the safe broker boundary.

    Research rejects every execution/approval capability.  Paper requires the
    concrete deterministic :class:`PaperBroker` and exposes only its write-only
    view.  Live rejects Paper/Telegram capability and delegates construction of
    its write-only port to the explicitly supplied :class:`LiveGuard`.
    """
    _validate_settings(settings)
    mode = settings.trading_mode
    capabilities = MODE_CAPABILITY_MATRIX[mode]

    if mode is TradingMode.RESEARCH:
        if any(
            candidate is not None
            for candidate in (broker_read, paper_broker, live_guard, live_write_factory)
        ):
            raise RuntimeStartupError(
                "Research mode cannot receive broker read/write or LiveGuard capabilities",
                code=RuntimeStartupErrorCode.CAPABILITY_NOT_ALLOWED,
            )
        return Runtime(
            mode=mode,
            capabilities=capabilities,
            active_broker_capabilities=frozenset(),
            broker_read=None,
            broker_write=None,
        )

    _validate_read_port(broker_read)

    if mode is TradingMode.PAPER:
        if live_guard is not None or live_write_factory is not None:
            raise RuntimeStartupError(
                "Paper mode cannot load a LiveGuard or Robinhood write factory",
                code=RuntimeStartupErrorCode.CAPABILITY_NOT_ALLOWED,
            )
        if paper_broker is None:
            raise RuntimeStartupError(
                "Paper mode requires a deterministic PaperBroker",
                code=RuntimeStartupErrorCode.PAPER_BROKER_REQUIRED,
            )

        # Local import prevents Research-only processes from loading the Paper
        # execution implementation merely by importing this composition module.
        from ainvest.execution.paper import PaperBroker, as_write_port

        if not isinstance(paper_broker, PaperBroker):
            raise RuntimeStartupError(
                "Paper mode write capability must be a PaperBroker",
                code=RuntimeStartupErrorCode.INVALID_BROKER_PORT,
            )
        broker_write = _validate_write_port(as_write_port(paper_broker))
        active_broker = {BrokerCapability.PAPER_WRITE}
        if broker_read is not None:
            active_broker.add(BrokerCapability.READ_ONLY)
        return Runtime(
            mode=mode,
            capabilities=capabilities,
            active_broker_capabilities=frozenset(active_broker),
            broker_read=broker_read,
            broker_write=broker_write,
        )

    if paper_broker is not None:
        raise RuntimeStartupError(
            "Live mode cannot load PaperBroker",
            code=RuntimeStartupErrorCode.CAPABILITY_NOT_ALLOWED,
        )
    if live_guard is None:
        raise RuntimeStartupError(
            "Live mode requires an explicit production LiveGuard",
            code=RuntimeStartupErrorCode.LIVE_GUARD_REQUIRED,
        )
    if live_write_factory is None:
        raise RuntimeStartupError(
            "Live mode requires a guarded write-capability factory",
            code=RuntimeStartupErrorCode.LIVE_WRITE_FACTORY_REQUIRED,
        )
    if not isinstance(live_guard, LiveGuard):
        raise RuntimeStartupError(
            "live_guard must implement LiveGuard",
            code=RuntimeStartupErrorCode.LIVE_GUARD_REQUIRED,
        )
    if settings.is_production and not live_guard.production_ready:
        raise RuntimeStartupError(
            "Production Live mode requires a P07-T4 production-ready LiveGuard",
            code=RuntimeStartupErrorCode.LIVE_GUARD_NOT_PRODUCTION_READY,
        )

    try:
        guarded_write = live_guard.construct_write_capability(
            settings=settings, factory=live_write_factory
        )
    except RuntimeStartupError:
        raise
    except Exception as exc:
        raise RuntimeStartupError(
            "LiveGuard failed closed while authorizing write capability",
            code=RuntimeStartupErrorCode.LIVE_GUARD_REJECTED,
        ) from exc

    broker_write = _validate_write_port(guarded_write)
    active_broker = {BrokerCapability.ROBINHOOD_WRITE}
    if broker_read is not None:
        active_broker.add(BrokerCapability.READ_ONLY)
    return Runtime(
        mode=mode,
        capabilities=capabilities,
        active_broker_capabilities=frozenset(active_broker),
        broker_read=broker_read,
        broker_write=broker_write,
    )


__all__ = [
    "MODE_CAPABILITY_MATRIX",
    "BrokerCapability",
    "LiveGuard",
    "LiveWriteFactory",
    "ModeCapabilities",
    "RejectingLiveGuard",
    "Runtime",
    "RuntimePackage",
    "RuntimeSecret",
    "RuntimeStartupError",
    "RuntimeStartupErrorCode",
    "SchedulerJob",
    "start_runtime",
]
