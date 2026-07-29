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
from threading import Lock, get_ident
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    Literal,
    Never,
    Protocol,
    SupportsIndex,
    cast,
    runtime_checkable,
)

from ainvest.config import AinvestEnv, Settings, TradingMode

if TYPE_CHECKING:
    from ainvest.execution.broker import (
        BrokerReadPort,
        BrokerSubmitRequest,
        BrokerSubmitResult,
        BrokerWritePort,
    )
    from ainvest.schemas.broker import CancelCommand, CancelResult


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
    SIGNAL_EXPIRY = "signal_expiry"
    APPROVAL_EXPIRY = "approval_expiry"
    PAPER_EXECUTION = "paper_execution"
    LIVE_EXECUTION = "live_execution"
    ORDER_MONITORING = "order_monitoring"
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
            SchedulerJob.SIGNAL_EXPIRY,
            SchedulerJob.APPROVAL_EXPIRY,
            SchedulerJob.PAPER_EXECUTION,
            SchedulerJob.ORDER_MONITORING,
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
            SchedulerJob.SIGNAL_EXPIRY,
            SchedulerJob.APPROVAL_EXPIRY,
            SchedulerJob.LIVE_EXECUTION,
            SchedulerJob.ORDER_MONITORING,
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
    PRODUCTION_LIVE_DISABLED = "RUNTIME_PRODUCTION_LIVE_DISABLED"
    LIVE_GUARD_REJECTED = "RUNTIME_LIVE_GUARD_REJECTED"
    LIVE_WRITE_FACTORY_REQUIRED = "RUNTIME_LIVE_WRITE_FACTORY_REQUIRED"
    LIVE_WRITE_FACTORY_FAILED = "RUNTIME_LIVE_WRITE_FACTORY_FAILED"
    UNVALIDATED_RUNTIME = "RUNTIME_UNVALIDATED"


class RuntimeStartupError(RuntimeError):
    """Fail-closed startup error with a stable machine-readable code."""

    def __init__(self, message: str, *, code: RuntimeStartupErrorCode) -> None:
        self.code = code
        super().__init__(message)


LiveWriteFactory = Callable[[], "BrokerWritePort"]
LiveSubmitDelegate = Callable[["BrokerSubmitRequest"], "BrokerSubmitResult"]
LiveCancelDelegate = Callable[["CancelCommand"], "CancelResult"]
_UNSET = object()


@dataclass(frozen=True, slots=True)
class LiveGateContext:
    """Minimal non-secret runtime identity supplied to a LiveGuard."""

    environment: AinvestEnv
    mode: TradingMode


@runtime_checkable
class LiveGuard(Protocol):
    """Own startup authorization and each request-aware broker delegation.

    Implementations must raise :class:`RuntimeStartupError` whenever any P07-T4
    prerequisite is absent, disabled, stale, or uncertain. Submit/cancel
    implementations receive the concrete payload and a call-scoped, single-use
    delegate. They must return the exact result from exactly one delegate call,
    synchronously on the same thread. Implementations must hold their lock or
    lease across final validation and that call; spawned-worker delegation is
    rejected before the raw broker is reached. Submit and cancel authorization
    are operation-specific: disabling new submissions must not implicitly
    disable a separately authorized risk-reducing cancellation. P08-T0 supplies
    no production implementation.
    """

    def authorize_startup(self, *, context: LiveGateContext) -> None:
        """Authorize non-production Live composition before factory invocation."""
        ...

    def submit(
        self,
        *,
        context: LiveGateContext,
        request: BrokerSubmitRequest,
        delegate: LiveSubmitDelegate,
    ) -> BrokerSubmitResult:
        """Validate ``request`` and atomically decide whether to delegate it."""
        ...

    def cancel(
        self,
        *,
        context: LiveGateContext,
        command: CancelCommand,
        delegate: LiveCancelDelegate,
    ) -> CancelResult:
        """Validate ``command`` and atomically decide whether to delegate it."""
        ...


class RejectingLiveGuard:
    """Default LiveGuard: reject startup and every broker-write operation."""

    def authorize_startup(self, *, context: LiveGateContext) -> None:
        del context
        self._reject()

    def submit(
        self,
        *,
        context: LiveGateContext,
        request: BrokerSubmitRequest,
        delegate: LiveSubmitDelegate,
    ) -> BrokerSubmitResult:
        del context, request, delegate
        self._reject()

    def cancel(
        self,
        *,
        context: LiveGateContext,
        command: CancelCommand,
        delegate: LiveCancelDelegate,
    ) -> CancelResult:
        del context, command, delegate
        self._reject()

    @staticmethod
    def _reject() -> Never:
        raise RuntimeStartupError(
            "Live writes remain disabled until the production LiveGuard is installed",
            code=RuntimeStartupErrorCode.LIVE_GUARD_REJECTED,
        )


class Runtime:
    """Factory-controlled validated runtime returned by :func:`start_runtime`.

    Direct construction is rejected. This prevents ordinary callers from
    manufacturing a ``ready`` Live runtime around an unguarded write port.
    """

    __slots__ = (
        "__active_broker_capabilities",
        "__broker_read",
        "__broker_write",
        "__capabilities",
        "__factory_token",
        "__mode",
    )

    def __init__(
        self,
        *,
        _factory_token: object | None = None,
        mode: TradingMode | None = None,
        capabilities: ModeCapabilities | None = None,
        active_broker_capabilities: frozenset[BrokerCapability] | None = None,
        broker_read: BrokerReadPort | None = None,
        broker_write: BrokerWritePort | None = None,
    ) -> None:
        if _factory_token is not _RUNTIME_FACTORY_TOKEN:
            raise RuntimeStartupError(
                "Runtime instances must be created by start_runtime",
                code=RuntimeStartupErrorCode.UNVALIDATED_RUNTIME,
            )
        if mode is None or capabilities is None or active_broker_capabilities is None:
            raise RuntimeStartupError(
                "Validated runtime state is incomplete",
                code=RuntimeStartupErrorCode.UNVALIDATED_RUNTIME,
            )
        self.__factory_token = _factory_token
        self.__mode = mode
        self.__capabilities = capabilities
        self.__active_broker_capabilities = active_broker_capabilities
        self.__broker_read = broker_read
        self.__broker_write = broker_write

    def _assert_validated(self) -> None:
        try:
            factory_token = self.__factory_token
        except AttributeError as exc:
            raise RuntimeStartupError(
                "Runtime was not created by start_runtime",
                code=RuntimeStartupErrorCode.UNVALIDATED_RUNTIME,
            ) from exc
        if factory_token is not _RUNTIME_FACTORY_TOKEN:
            raise RuntimeStartupError(
                "Runtime validation token is invalid",
                code=RuntimeStartupErrorCode.UNVALIDATED_RUNTIME,
            )

    def __copy__(self) -> Runtime:
        raise TypeError("Runtime capability containers cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> Runtime:
        del memo
        raise TypeError("Runtime capability containers cannot be deep-copied")

    def __reduce_ex__(self, protocol: SupportsIndex) -> tuple[object, ...]:
        del protocol
        raise TypeError("Runtime capability containers cannot be serialized")

    @property
    def mode(self) -> TradingMode:
        """Validated configured runtime mode."""
        self._assert_validated()
        return self.__mode

    @property
    def capabilities(self) -> ModeCapabilities:
        """Allowed capabilities from the authoritative mode matrix."""
        self._assert_validated()
        return self.__capabilities

    @property
    def active_broker_capabilities(self) -> frozenset[BrokerCapability]:
        """Broker capabilities actually installed in this runtime."""
        self._assert_validated()
        return self.__active_broker_capabilities

    @property
    def broker_read(self) -> BrokerReadPort | None:
        """Validated read-only broker port, when configured."""
        self._assert_validated()
        return self.__broker_read

    @property
    def broker_write(self) -> BrokerWritePort | None:
        """Validated write-only broker port, guarded for Live per operation."""
        self._assert_validated()
        return self.__broker_write

    def health_summary(self) -> dict[str, object]:
        """Return a deterministic health payload with all secrets redacted."""
        self._assert_validated()
        return {
            "status": "ready",
            "mode": self.__mode.value,
            "capabilities": {
                "packages_allowed": sorted(item.value for item in self.__capabilities.packages),
                "broker_active": sorted(item.value for item in self.__active_broker_capabilities),
                "scheduler_jobs_allowed": sorted(
                    item.value for item in self.__capabilities.scheduler_jobs
                ),
                "secret_classes_allowed": {
                    item.value: "[REDACTED]"
                    for item in sorted(self.__capabilities.secrets, key=lambda item: item.value)
                },
            },
        }


_RUNTIME_FACTORY_TOKEN = object()


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

    # Import broker contracts only after Research mode has returned, so a
    # Research-only process does not import the execution package.
    from ainvest.execution.broker import (
        BrokerReadPort,
        assert_read_port_has_no_write_methods,
    )

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
    from ainvest.execution.broker import (
        READ_METHOD_NAMES,
        BrokerWritePort,
        assert_no_replace_operation,
    )

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


def _authorize_live_startup(guard: LiveGuard, *, context: LiveGateContext) -> None:
    try:
        guard.authorize_startup(context=context)
    except RuntimeStartupError:
        raise
    except Exception:
        raise RuntimeStartupError(
            "LiveGuard failed closed while authorizing startup",
            code=RuntimeStartupErrorCode.LIVE_GUARD_REJECTED,
        ) from None


@dataclass(frozen=True, slots=True)
class _DelegateSnapshot[ResultT]:
    """Atomic state captured when a call-scoped delegate is closed."""

    started: bool
    completed: bool
    misused: bool
    result: ResultT | object
    error: Exception | None


class _SingleUseLiveDelegate[RequestT, ResultT]:
    """Thread-safe raw broker capability valid for one guard method only."""

    __slots__ = (
        "__active",
        "__completed",
        "__delegate",
        "__error",
        "__lock",
        "__misused",
        "__owner_thread_id",
        "__result",
        "__started",
    )

    def __init__(self, delegate: Callable[[RequestT], ResultT]) -> None:
        self.__delegate = delegate
        self.__lock = Lock()
        self.__owner_thread_id = get_ident()
        self.__active = True
        self.__started = False
        self.__completed = False
        self.__misused = False
        self.__result: ResultT | object = _UNSET
        self.__error: Exception | None = None

    def __call__(self, payload: RequestT) -> ResultT:
        rejected = False
        with self.__lock:
            if get_ident() != self.__owner_thread_id or not self.__active or self.__started:
                self.__misused = True
                rejected = True
            else:
                self.__started = True
        if rejected:
            raise RuntimeStartupError(
                "Live broker delegate is cross-thread, expired, or already used",
                code=RuntimeStartupErrorCode.LIVE_GUARD_REJECTED,
            ) from None

        try:
            result = self.__delegate(payload)
        except Exception as exc:
            with self.__lock:
                self.__error = exc
            raise

        with self.__lock:
            self.__completed = True
            self.__result = result
        return result

    def close(self) -> _DelegateSnapshot[ResultT]:
        """Expire the delegate and capture state for outcome classification."""
        with self.__lock:
            self.__active = False
            return _DelegateSnapshot(
                started=self.__started,
                completed=self.__completed,
                misused=self.__misused,
                result=self.__result,
                error=self.__error,
            )


def _raise_guard_rejected(operation: Literal["submit", "cancel"]) -> Never:
    raise RuntimeStartupError(
        f"LiveGuard failed closed while authorizing {operation}",
        code=RuntimeStartupErrorCode.LIVE_GUARD_REJECTED,
    )


def _raise_unknown_live_outcome(
    *,
    operation: Literal["submit", "cancel"],
    idempotency_key: str,
) -> Never:
    from ainvest.execution.broker import BrokerUnknownOutcomeError

    raise BrokerUnknownOutcomeError(
        "Live broker outcome is unknown; reconcile before any further write",
        reason_code="LIVE_GUARD_OUTCOME_UNKNOWN",
        operation=operation,
        idempotency_key=idempotency_key,
    )


def _resolve_guarded_write[ResultT](
    *,
    operation: Literal["submit", "cancel"],
    idempotency_key: str,
    snapshot: _DelegateSnapshot[ResultT],
    guard_result: ResultT | object,
    guard_error: Exception | None,
) -> ResultT:
    """Classify one guard invocation without retaining exception context."""
    from ainvest.execution.broker import BrokerError

    # Only a BrokerError recorded at the raw delegate boundary is established
    # broker-domain evidence. A guard cannot manufacture one and bypass the
    # per-write fail-closed boundary.
    if isinstance(snapshot.error, BrokerError):
        raise snapshot.error

    if not snapshot.started:
        if isinstance(guard_error, RuntimeStartupError):
            raise guard_error
        _raise_guard_rejected(operation)

    if (
        not snapshot.completed
        or snapshot.misused
        or snapshot.error is not None
        or guard_error is not None
        or guard_result is not snapshot.result
    ):
        _raise_unknown_live_outcome(
            operation=operation,
            idempotency_key=idempotency_key,
        )

    return cast(ResultT, snapshot.result)


def _invoke_guarded_write[RequestT, ResultT](
    *,
    operation: Literal["submit", "cancel"],
    idempotency_key: str,
    raw_delegate: Callable[[RequestT], ResultT],
    guard_call: Callable[[Callable[[RequestT], ResultT]], ResultT],
) -> ResultT:
    """Expose one scoped delegate and close it on every guard exit path."""
    scoped_delegate = _SingleUseLiveDelegate(raw_delegate)
    guard_result: ResultT | object = _UNSET
    guard_error: Exception | None = None
    try:
        try:
            guard_result = guard_call(scoped_delegate)
        except Exception as exc:
            guard_error = exc
    finally:
        snapshot = scoped_delegate.close()

    return _resolve_guarded_write(
        operation=operation,
        idempotency_key=idempotency_key,
        snapshot=snapshot,
        guard_result=guard_result,
        guard_error=guard_error,
    )


class _GuardedLiveWritePort:
    """Write-only proxy whose guard owns final validation and delegation."""

    __slots__ = ("__context", "__delegate", "__guard")

    def __init__(
        self,
        *,
        guard: LiveGuard,
        context: LiveGateContext,
        delegate: BrokerWritePort,
    ) -> None:
        self.__guard = guard
        self.__context = context
        self.__delegate = delegate

    def submit(self, request: BrokerSubmitRequest) -> BrokerSubmitResult:
        return _invoke_guarded_write(
            operation="submit",
            idempotency_key=request.client_order_id,
            raw_delegate=self.__delegate.submit,
            guard_call=lambda delegate: self.__guard.submit(
                context=self.__context,
                request=request,
                delegate=delegate,
            ),
        )

    def cancel(self, command: CancelCommand) -> CancelResult:
        return _invoke_guarded_write(
            operation="cancel",
            idempotency_key=command.idempotency_key,
            raw_delegate=self.__delegate.cancel,
            guard_call=lambda delegate: self.__guard.cancel(
                context=self.__context,
                command=command,
                delegate=delegate,
            ),
        )

    def __copy__(self) -> _GuardedLiveWritePort:
        raise TypeError("Live broker-write capabilities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> _GuardedLiveWritePort:
        del memo
        raise TypeError("Live broker-write capabilities cannot be deep-copied")

    def __reduce_ex__(self, protocol: SupportsIndex) -> tuple[object, ...]:
        del protocol
        raise TypeError("Live broker-write capabilities cannot be serialized")


def _new_runtime(
    *,
    mode: TradingMode,
    capabilities: ModeCapabilities,
    active_broker_capabilities: frozenset[BrokerCapability],
    broker_read: BrokerReadPort | None,
    broker_write: BrokerWritePort | None,
) -> Runtime:
    return Runtime(
        _factory_token=_RUNTIME_FACTORY_TOKEN,
        mode=mode,
        capabilities=capabilities,
        active_broker_capabilities=active_broker_capabilities,
        broker_read=broker_read,
        broker_write=broker_write,
    )


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
        return _new_runtime(
            mode=mode,
            capabilities=capabilities,
            active_broker_capabilities=frozenset(),
            broker_read=None,
            broker_write=None,
        )

    if mode is TradingMode.LIVE and settings.is_production:
        raise RuntimeStartupError(
            "Production Live mode is disabled until P07-T4 installs its trusted integration",
            code=RuntimeStartupErrorCode.PRODUCTION_LIVE_DISABLED,
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

        # Local import keeps the Paper execution implementation out of a
        # Research-only process.
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
        return _new_runtime(
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
    live_context = LiveGateContext(
        environment=settings.ainvest_env,
        mode=settings.trading_mode,
    )
    _authorize_live_startup(live_guard, context=live_context)

    factory_failed = False
    raw_write_candidate: object | None = None
    try:
        raw_write_candidate = live_write_factory()
    except Exception:
        factory_failed = True
    if factory_failed:
        # Raise outside the adapter exception handler so its message and
        # traceback cannot become an implicit cause/context carrying secrets.
        raise RuntimeStartupError(
            "Live write factory failed closed",
            code=RuntimeStartupErrorCode.LIVE_WRITE_FACTORY_FAILED,
        )

    raw_write = _validate_write_port(raw_write_candidate)
    broker_write = _validate_write_port(
        _GuardedLiveWritePort(
            guard=live_guard,
            context=live_context,
            delegate=raw_write,
        )
    )
    active_broker = {BrokerCapability.ROBINHOOD_WRITE}
    if broker_read is not None:
        active_broker.add(BrokerCapability.READ_ONLY)
    return _new_runtime(
        mode=mode,
        capabilities=capabilities,
        active_broker_capabilities=frozenset(active_broker),
        broker_read=broker_read,
        broker_write=broker_write,
    )


__all__ = [
    "MODE_CAPABILITY_MATRIX",
    "BrokerCapability",
    "LiveCancelDelegate",
    "LiveGateContext",
    "LiveGuard",
    "LiveSubmitDelegate",
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
