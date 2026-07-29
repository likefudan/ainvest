"""Runtime mode and fail-closed startup tests (P08-T0)."""

from __future__ import annotations

import copy
import io
import pickle
import subprocess
import sys
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import SecretStr

from ainvest.approval.order_hash import attach_order_hash
from ainvest.config import (
    AinvestEnv,
    ApprovalMethod,
    ApprovalScope,
    Settings,
    TradingMode,
    WebAuthnSettings,
)
from ainvest.execution import BrokerWritePort, PaperBroker, PaperCostModel
from ainvest.execution.broker import BrokerSubmitRequest, BrokerSubmitResult
from ainvest.runtime import (
    MODE_CAPABILITY_MATRIX,
    BrokerCapability,
    LiveCancelDelegate,
    LiveGateContext,
    LiveSubmitDelegate,
    RejectingLiveGuard,
    Runtime,
    RuntimePackage,
    RuntimeSecret,
    RuntimeStartupError,
    RuntimeStartupErrorCode,
    SchedulerJob,
    start_runtime,
)
from ainvest.schemas.approval import ApprovalEvent
from ainvest.schemas.broker import BrokerFill, BrokerOrder, CancelCommand, CancelResult
from ainvest.schemas.examples import (
    approval_event_example,
    cancel_command_example,
    order_proposal_valid,
)
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.orders import OrderProposal
from ainvest.schemas.portfolio import AccountScope, PortfolioSnapshot, PositionSnapshot

REPO_ROOT = Path(__file__).resolve().parents[2]


def _paper_broker() -> PaperBroker:
    now = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)
    return PaperBroker(
        cost_model=PaperCostModel(
            fee_bps=Decimal("0"),
            half_spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        ),
        clock=lambda: now,
        initial_cash=Decimal("10000"),
    )


def _live_settings() -> Settings:
    return Settings(
        trading_mode=TradingMode.LIVE,
        live_trading_enabled=True,
        webauthn=WebAuthnSettings(
            origin="https://approve.example.com",
            rp_id="approve.example.com",
            credential_ids=("primary", "recovery"),
            approval_method=ApprovalMethod.WEBAUTHN,
            approval_scope=ApprovalScope.LIVE,
            bootstrap_closed=True,
        ),
    )


def _live_submit_request() -> BrokerSubmitRequest:
    proposal = OrderProposal.model_validate(
        attach_order_hash(
            {
                **order_proposal_valid(),
                "account_scope": "agentic",
            }
        )
    )
    approval = ApprovalEvent.model_validate(
        {
            **approval_event_example(),
            "proposal_id": proposal.proposal_id,
            "order_hash": proposal.order_hash,
            "method": "webauthn",
            "scope": "live",
        }
    )
    return BrokerSubmitRequest(
        proposal=proposal,
        approval=approval,
        client_order_id="live_client_order_1",
    )


def _live_cancel_command(request: BrokerSubmitRequest) -> CancelCommand:
    return CancelCommand.model_validate(
        {
            **cancel_command_example(),
            "proposal_id": request.proposal.proposal_id,
            "order_hash": request.proposal.order_hash,
            "account_scope": "agentic",
        }
    )


class _WritePort:
    def submit(self, request: BrokerSubmitRequest) -> BrokerSubmitResult:
        raise NotImplementedError(request)

    def cancel(self, command: CancelCommand) -> CancelResult:
        raise NotImplementedError(command)


class _CountingWritePort:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.cancel_calls = 0
        self.last_submit_request: BrokerSubmitRequest | None = None
        self.last_cancel_command: CancelCommand | None = None
        self.submit_result = cast(BrokerSubmitResult, object())
        self.cancel_result = cast(CancelResult, object())

    def submit(self, request: BrokerSubmitRequest) -> BrokerSubmitResult:
        self.submit_calls += 1
        self.last_submit_request = request
        return self.submit_result

    def cancel(self, command: CancelCommand) -> CancelResult:
        self.cancel_calls += 1
        self.last_cancel_command = command
        return self.cancel_result


class _FailingWritePort:
    def __init__(self, failure: RuntimeError) -> None:
        self.failure = failure

    def submit(self, request: BrokerSubmitRequest) -> BrokerSubmitResult:
        del request
        raise self.failure

    def cancel(self, command: CancelCommand) -> CancelResult:
        del command
        raise self.failure


class _ReadPort:
    def get_account(self, account_scope: AccountScope) -> PortfolioSnapshot:
        raise NotImplementedError(account_scope)

    def get_positions(self, account_scope: AccountScope) -> tuple[PositionSnapshot, ...]:
        raise NotImplementedError(account_scope)

    def get_quotes(self, instrument_ids: tuple[str, ...]) -> tuple[MarketQuote, ...]:
        raise NotImplementedError(instrument_ids)

    def get_orders(
        self,
        account_scope: AccountScope,
        *,
        broker_order_ids: tuple[str, ...] | None = None,
        client_order_ids: tuple[str, ...] | None = None,
    ) -> tuple[BrokerOrder, ...]:
        raise NotImplementedError(account_scope, broker_order_ids, client_order_ids)

    def get_fills(
        self,
        account_scope: AccountScope,
        *,
        broker_order_ids: tuple[str, ...] | None = None,
    ) -> tuple[BrokerFill, ...]:
        raise NotImplementedError(account_scope, broker_order_ids)


class _LeakyReadPort(_ReadPort, _WritePort):
    pass


class _FailingGuard:
    def authorize_startup(self, *, context: LiveGateContext) -> None:
        del context
        raise ValueError("unsafe guard failure")

    def submit(
        self,
        *,
        context: LiveGateContext,
        request: BrokerSubmitRequest,
        delegate: LiveSubmitDelegate,
    ) -> BrokerSubmitResult:
        del context, request, delegate
        raise AssertionError("startup must fail first")

    def cancel(
        self,
        *,
        context: LiveGateContext,
        command: CancelCommand,
        delegate: LiveCancelDelegate,
    ) -> CancelResult:
        del context, command, delegate
        raise AssertionError("startup must fail first")


class _AllowingTestGuard:
    def __init__(self) -> None:
        self.startup_contexts: list[LiveGateContext] = []
        self.submit_calls: list[tuple[LiveGateContext, BrokerSubmitRequest]] = []
        self.cancel_calls: list[tuple[LiveGateContext, CancelCommand]] = []

    def authorize_startup(
        self,
        *,
        context: LiveGateContext,
    ) -> None:
        assert context.mode is TradingMode.LIVE
        self.startup_contexts.append(context)

    def submit(
        self,
        *,
        context: LiveGateContext,
        request: BrokerSubmitRequest,
        delegate: LiveSubmitDelegate,
    ) -> BrokerSubmitResult:
        self.submit_calls.append((context, request))
        return delegate(request)

    def cancel(
        self,
        *,
        context: LiveGateContext,
        command: CancelCommand,
        delegate: LiveCancelDelegate,
    ) -> CancelResult:
        self.cancel_calls.append((context, command))
        return delegate(command)


class _MutableTestGuard(_AllowingTestGuard):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.allowed = True

    def revoke(self) -> None:
        with self._lock:
            self.allowed = False

    def submit(
        self,
        *,
        context: LiveGateContext,
        request: BrokerSubmitRequest,
        delegate: LiveSubmitDelegate,
    ) -> BrokerSubmitResult:
        with self._lock:
            self._assert_allowed()
            return super().submit(context=context, request=request, delegate=delegate)

    def cancel(
        self,
        *,
        context: LiveGateContext,
        command: CancelCommand,
        delegate: LiveCancelDelegate,
    ) -> CancelResult:
        with self._lock:
            self._assert_allowed()
            return super().cancel(context=context, command=command, delegate=delegate)

    def _assert_allowed(self) -> None:
        if self.allowed:
            return
        raise RuntimeStartupError(
            "test kill switch is active",
            code=RuntimeStartupErrorCode.LIVE_GUARD_REJECTED,
        )


class _PausingMutableGuard(_MutableTestGuard):
    """Pause request preprocessing before the final locked gate decision."""

    def __init__(self) -> None:
        super().__init__()
        self.preprocessing_started = threading.Event()
        self.continue_to_gate = threading.Event()

    def submit(
        self,
        *,
        context: LiveGateContext,
        request: BrokerSubmitRequest,
        delegate: LiveSubmitDelegate,
    ) -> BrokerSubmitResult:
        self.preprocessing_started.set()
        if not self.continue_to_gate.wait(timeout=5):
            raise TimeoutError("test did not release request preprocessing")
        return super().submit(context=context, request=request, delegate=delegate)


class _SpoofedProductionGuard(_AllowingTestGuard):
    @property
    def production_ready(self) -> bool:
        """Adversarial legacy readiness flag that P08-T0 must ignore."""
        return True


@pytest.mark.unit
def test_capability_matrix_has_mode_specific_boundaries() -> None:
    research = MODE_CAPABILITY_MATRIX[TradingMode.RESEARCH]
    paper = MODE_CAPABILITY_MATRIX[TradingMode.PAPER]
    live = MODE_CAPABILITY_MATRIX[TradingMode.LIVE]

    assert research.packages == frozenset({RuntimePackage.DATA, RuntimePackage.RESEARCH_AGENT})
    assert research.broker == frozenset()
    assert RuntimePackage.STRATEGY_EXECUTION not in research.packages
    assert RuntimePackage.APPROVAL_SERVICE not in research.packages

    assert BrokerCapability.PAPER_WRITE in paper.broker
    assert BrokerCapability.READ_ONLY in paper.broker
    assert BrokerCapability.ROBINHOOD_WRITE not in paper.broker
    assert RuntimePackage.TELEGRAM_PAPER_APPROVAL in paper.packages
    assert RuntimePackage.WEBAUTHN_LIVE_APPROVAL not in paper.packages
    assert RuntimeSecret.WEBAUTHN_SERVER not in paper.secrets
    assert research.scheduler_jobs == frozenset({SchedulerJob.RESEARCH})
    assert paper.scheduler_jobs == frozenset(
        {
            SchedulerJob.RESEARCH,
            SchedulerJob.STRATEGY_EVALUATION,
            SchedulerJob.SIGNAL_EXPIRY,
            SchedulerJob.APPROVAL_EXPIRY,
            SchedulerJob.PAPER_EXECUTION,
            SchedulerJob.ORDER_MONITORING,
            SchedulerJob.RECONCILIATION,
        }
    )

    assert BrokerCapability.ROBINHOOD_WRITE in live.broker
    assert BrokerCapability.PAPER_WRITE not in live.broker
    assert RuntimePackage.WEBAUTHN_LIVE_APPROVAL in live.packages
    assert RuntimePackage.TELEGRAM_PAPER_APPROVAL not in live.packages
    assert RuntimeSecret.ROBINHOOD_WRITE in live.secrets
    assert live.scheduler_jobs == frozenset(
        {
            SchedulerJob.RESEARCH,
            SchedulerJob.STRATEGY_EVALUATION,
            SchedulerJob.SIGNAL_EXPIRY,
            SchedulerJob.APPROVAL_EXPIRY,
            SchedulerJob.LIVE_EXECUTION,
            SchedulerJob.ORDER_MONITORING,
            SchedulerJob.RECONCILIATION,
        }
    )


@pytest.mark.unit
def test_research_starts_without_execution_capabilities() -> None:
    runtime = start_runtime(Settings(trading_mode=TradingMode.RESEARCH))

    assert runtime.mode is TradingMode.RESEARCH
    assert runtime.broker_read is None
    assert runtime.broker_write is None
    assert runtime.active_broker_capabilities == frozenset()


@pytest.mark.unit
def test_importing_runtime_does_not_load_research_forbidden_packages() -> None:
    script = (
        "import sys; import ainvest.runtime; "
        "forbidden = {'ainvest.execution', 'ainvest.approval', 'ainvest.strategies'}; "
        "loaded = forbidden.intersection(sys.modules); "
        "assert not loaded, loaded"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.unit
def test_research_rejects_every_broker_or_live_capability() -> None:
    settings = Settings(trading_mode=TradingMode.RESEARCH)

    with pytest.raises(RuntimeStartupError) as exc_info:
        start_runtime(settings, paper_broker=_paper_broker())

    assert exc_info.value.code is RuntimeStartupErrorCode.CAPABILITY_NOT_ALLOWED


@pytest.mark.unit
def test_paper_uses_only_a_concrete_paper_broker_write_view() -> None:
    runtime = start_runtime(
        Settings(trading_mode=TradingMode.PAPER),
        paper_broker=_paper_broker(),
    )

    assert isinstance(runtime.broker_write, BrokerWritePort)
    assert runtime.broker_write is not None
    assert not hasattr(runtime.broker_write, "get_account")
    assert BrokerCapability.PAPER_WRITE in runtime.capabilities.broker
    assert BrokerCapability.ROBINHOOD_WRITE not in runtime.capabilities.broker
    assert runtime.active_broker_capabilities == frozenset({BrokerCapability.PAPER_WRITE})


@pytest.mark.unit
def test_paper_may_read_real_account_but_rejects_a_leaky_read_port() -> None:
    settings = Settings(trading_mode=TradingMode.PAPER)
    runtime = start_runtime(
        settings,
        broker_read=_ReadPort(),
        paper_broker=_paper_broker(),
    )
    assert runtime.active_broker_capabilities == frozenset(
        {BrokerCapability.READ_ONLY, BrokerCapability.PAPER_WRITE}
    )

    with pytest.raises(RuntimeStartupError) as exc_info:
        start_runtime(
            settings,
            broker_read=_LeakyReadPort(),
            paper_broker=_paper_broker(),
        )
    assert exc_info.value.code is RuntimeStartupErrorCode.CAPABILITY_NOT_ALLOWED


@pytest.mark.unit
def test_paper_rejects_missing_or_non_paper_write_capability() -> None:
    settings = Settings(trading_mode=TradingMode.PAPER)

    with pytest.raises(RuntimeStartupError) as missing:
        start_runtime(settings)
    assert missing.value.code is RuntimeStartupErrorCode.PAPER_BROKER_REQUIRED

    with pytest.raises(RuntimeStartupError) as wrong:
        start_runtime(settings, paper_broker=_WritePort())
    assert wrong.value.code is RuntimeStartupErrorCode.INVALID_BROKER_PORT


@pytest.mark.unit
def test_paper_rejects_live_guard_and_write_factory() -> None:
    with pytest.raises(RuntimeStartupError) as exc_info:
        start_runtime(
            Settings(trading_mode=TradingMode.PAPER),
            paper_broker=_paper_broker(),
            live_guard=RejectingLiveGuard(),
            live_write_factory=_WritePort,
        )

    assert exc_info.value.code is RuntimeStartupErrorCode.CAPABILITY_NOT_ALLOWED


@pytest.mark.unit
def test_live_requires_guard_before_factory_can_run() -> None:
    factory_called = False

    def factory() -> BrokerWritePort:
        nonlocal factory_called
        factory_called = True
        return _WritePort()

    with pytest.raises(RuntimeStartupError) as exc_info:
        start_runtime(_live_settings(), live_write_factory=factory)

    assert exc_info.value.code is RuntimeStartupErrorCode.LIVE_GUARD_REQUIRED
    assert factory_called is False


@pytest.mark.unit
def test_default_live_guard_rejects_without_calling_factory() -> None:
    factory_called = False

    def factory() -> BrokerWritePort:
        nonlocal factory_called
        factory_called = True
        return _WritePort()

    with pytest.raises(RuntimeStartupError) as exc_info:
        start_runtime(
            _live_settings(),
            live_guard=RejectingLiveGuard(),
            live_write_factory=factory,
        )

    assert exc_info.value.code is RuntimeStartupErrorCode.LIVE_GUARD_REJECTED
    assert factory_called is False


@pytest.mark.unit
def test_production_live_unconditionally_rejects_spoofed_ready_guard() -> None:
    guard = _SpoofedProductionGuard()
    live = _live_settings().model_copy(update={"ainvest_env": AinvestEnv.PRODUCTION})
    factory_called = False

    def factory() -> BrokerWritePort:
        nonlocal factory_called
        factory_called = True
        return _WritePort()

    with pytest.raises(RuntimeStartupError) as exc_info:
        start_runtime(
            live,
            broker_read=_LeakyReadPort(),
            paper_broker=_paper_broker(),
            live_guard=guard,
            live_write_factory=factory,
        )

    assert exc_info.value.code is RuntimeStartupErrorCode.PRODUCTION_LIVE_DISABLED
    assert guard.startup_contexts == []
    assert guard.submit_calls == []
    assert guard.cancel_calls == []
    assert factory_called is False


@pytest.mark.unit
def test_live_write_is_constructed_only_through_explicit_guard() -> None:
    guard = _AllowingTestGuard()

    runtime = start_runtime(
        _live_settings(),
        live_guard=guard,
        live_write_factory=_WritePort,
    )

    assert guard.startup_contexts == [
        LiveGateContext(environment=AinvestEnv.DEVELOPMENT, mode=TradingMode.LIVE)
    ]
    assert isinstance(runtime.broker_write, BrokerWritePort)
    assert BrokerCapability.ROBINHOOD_WRITE in runtime.capabilities.broker
    assert runtime.active_broker_capabilities == frozenset({BrokerCapability.ROBINHOOD_WRITE})


@pytest.mark.unit
def test_live_guard_owns_payload_aware_submit_and_cancel_delegation() -> None:
    guard = _AllowingTestGuard()
    delegate = _CountingWritePort()
    runtime = start_runtime(
        _live_settings(),
        live_guard=guard,
        live_write_factory=lambda: delegate,
    )
    write = runtime.broker_write
    assert write is not None
    submit_request = _live_submit_request()
    cancel_command = _live_cancel_command(submit_request)

    assert write.submit(submit_request) is delegate.submit_result
    assert write.cancel(cancel_command) is delegate.cancel_result

    context = LiveGateContext(environment=AinvestEnv.DEVELOPMENT, mode=TradingMode.LIVE)
    assert guard.submit_calls == [(context, submit_request)]
    assert guard.cancel_calls == [(context, cancel_command)]
    assert delegate.last_submit_request is submit_request
    assert delegate.last_cancel_command is cancel_command


@pytest.mark.unit
def test_guarded_live_port_preserves_delegate_failures() -> None:
    failure = RuntimeError("sanitized broker failure")
    runtime = start_runtime(
        _live_settings(),
        live_guard=_AllowingTestGuard(),
        live_write_factory=lambda: _FailingWritePort(failure),
    )
    write = runtime.broker_write
    assert write is not None

    with pytest.raises(RuntimeError) as exc_info:
        write.submit(_live_submit_request())

    assert exc_info.value is failure


@pytest.mark.unit
def test_live_guard_blocks_every_write_after_kill_switch_flip() -> None:
    guard = _MutableTestGuard()
    delegate = _CountingWritePort()
    runtime = start_runtime(
        _live_settings(),
        live_guard=guard,
        live_write_factory=lambda: delegate,
    )
    write = runtime.broker_write
    assert write is not None
    submit_request = _live_submit_request()
    cancel_command = _live_cancel_command(submit_request)

    assert write.submit(submit_request) is delegate.submit_result
    assert write.cancel(cancel_command) is delegate.cancel_result
    assert delegate.submit_calls == 1
    assert delegate.cancel_calls == 1

    guard.revoke()
    with pytest.raises(RuntimeStartupError) as submit_rejected:
        write.submit(submit_request)
    with pytest.raises(RuntimeStartupError) as cancel_rejected:
        write.cancel(cancel_command)

    assert submit_rejected.value.code is RuntimeStartupErrorCode.LIVE_GUARD_REJECTED
    assert cancel_rejected.value.code is RuntimeStartupErrorCode.LIVE_GUARD_REJECTED
    assert delegate.submit_calls == 1
    assert delegate.cancel_calls == 1


@pytest.mark.unit
def test_concurrent_kill_flip_during_preprocessing_prevents_broker_send() -> None:
    guard = _PausingMutableGuard()
    delegate = _CountingWritePort()
    runtime = start_runtime(
        _live_settings(),
        live_guard=guard,
        live_write_factory=lambda: delegate,
    )
    write = runtime.broker_write
    assert write is not None
    request = _live_submit_request()
    failures: list[BaseException] = []

    def submit_in_worker() -> None:
        try:
            write.submit(request)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=submit_in_worker)
    worker.start()
    assert guard.preprocessing_started.wait(timeout=5)

    guard.revoke()
    guard.continue_to_gate.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], RuntimeStartupError)
    assert failures[0].code is RuntimeStartupErrorCode.LIVE_GUARD_REJECTED
    assert delegate.submit_calls == 0
    assert delegate.last_submit_request is None


@pytest.mark.unit
def test_live_write_factory_failure_is_sanitized_and_fail_closed() -> None:
    adapter_secret = "adapter-secret-that-must-not-escape"

    def failing_factory() -> BrokerWritePort:
        raise RuntimeError(adapter_secret)

    with pytest.raises(RuntimeStartupError) as exc_info:
        start_runtime(
            _live_settings(),
            live_guard=_AllowingTestGuard(),
            live_write_factory=failing_factory,
        )

    error = exc_info.value
    assert error.code is RuntimeStartupErrorCode.LIVE_WRITE_FACTORY_FAILED
    assert adapter_secret not in str(error)
    assert adapter_secret not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.unit
def test_live_runtime_and_write_capability_reject_copy_and_serialization_without_leaks() -> None:
    secret = "runtime-capability-secret-that-must-not-leak"
    settings = _live_settings().model_copy(
        update={
            "database_password": SecretStr(secret),
            "robinhood_oauth_token": SecretStr(secret),
        }
    )
    runtime = start_runtime(
        settings,
        live_guard=_AllowingTestGuard(),
        live_write_factory=_CountingWritePort,
    )
    write = runtime.broker_write
    assert write is not None

    for capability in (runtime, write):
        with pytest.raises(TypeError):
            copy.copy(capability)
        with pytest.raises(TypeError):
            copy.deepcopy(capability)

        serialized = io.BytesIO()
        with pytest.raises(TypeError):
            pickle.Pickler(serialized).dump(capability)
        assert secret.encode() not in serialized.getvalue()
        assert secret not in repr(capability)


@pytest.mark.unit
def test_live_guard_error_and_combined_read_write_port_fail_closed() -> None:
    with pytest.raises(RuntimeStartupError) as guard_error:
        start_runtime(
            _live_settings(),
            live_guard=_FailingGuard(),
            live_write_factory=_WritePort,
        )
    assert guard_error.value.code is RuntimeStartupErrorCode.LIVE_GUARD_REJECTED

    guard = _AllowingTestGuard()
    with pytest.raises(RuntimeStartupError) as combined_port:
        start_runtime(
            _live_settings(),
            live_guard=guard,
            live_write_factory=_LeakyReadPort,
        )
    assert combined_port.value.code is RuntimeStartupErrorCode.CAPABILITY_NOT_ALLOWED


@pytest.mark.unit
def test_live_rejects_paper_broker_combination() -> None:
    with pytest.raises(RuntimeStartupError) as exc_info:
        start_runtime(
            _live_settings(),
            paper_broker=_paper_broker(),
            live_guard=_AllowingTestGuard(),
            live_write_factory=_WritePort,
        )

    assert exc_info.value.code is RuntimeStartupErrorCode.CAPABILITY_NOT_ALLOWED


@pytest.mark.unit
def test_health_summary_redacts_secret_values_and_is_deterministic() -> None:
    secret = "super-secret-value-that-must-not-leak"
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        database_password=SecretStr(secret),
        robinhood_oauth_token=SecretStr(secret),
    )
    runtime = start_runtime(settings, paper_broker=_paper_broker())

    summary = runtime.health_summary()
    rendered = repr(summary)

    assert summary["status"] == "ready"
    assert summary["mode"] == "paper"
    assert secret not in rendered
    assert "[REDACTED]" in rendered
    assert summary == runtime.health_summary()


@pytest.mark.unit
def test_runtime_direct_construction_and_object_bypass_cannot_report_ready() -> None:
    with pytest.raises(RuntimeStartupError) as direct:
        Runtime(
            mode=TradingMode.LIVE,
            capabilities=MODE_CAPABILITY_MATRIX[TradingMode.LIVE],
            active_broker_capabilities=frozenset({BrokerCapability.ROBINHOOD_WRITE}),
            broker_write=_WritePort(),
        )
    assert direct.value.code is RuntimeStartupErrorCode.UNVALIDATED_RUNTIME

    forged = object.__new__(Runtime)
    with pytest.raises(RuntimeStartupError) as health:
        forged.health_summary()
    with pytest.raises(RuntimeStartupError) as write:
        _ = forged.broker_write

    assert health.value.code is RuntimeStartupErrorCode.UNVALIDATED_RUNTIME
    assert write.value.code is RuntimeStartupErrorCode.UNVALIDATED_RUNTIME
