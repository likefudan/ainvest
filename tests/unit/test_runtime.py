"""Runtime mode and fail-closed startup tests (P08-T0)."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr

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
    LiveWriteFactory,
    RejectingLiveGuard,
    RuntimePackage,
    RuntimeSecret,
    RuntimeStartupError,
    RuntimeStartupErrorCode,
    SchedulerJob,
    start_runtime,
)
from ainvest.schemas.broker import BrokerFill, BrokerOrder, CancelCommand, CancelResult
from ainvest.schemas.market import MarketQuote
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


class _WritePort:
    def submit(self, request: BrokerSubmitRequest) -> BrokerSubmitResult:
        raise NotImplementedError(request)

    def cancel(self, command: CancelCommand) -> CancelResult:
        raise NotImplementedError(command)


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
    @property
    def production_ready(self) -> bool:
        return False

    def construct_write_capability(
        self,
        *,
        settings: Settings,
        factory: LiveWriteFactory,
    ) -> BrokerWritePort:
        del settings, factory
        raise ValueError("unsafe guard failure")


class _AllowingTestGuard:
    def __init__(self) -> None:
        self.factory_called = False

    @property
    def production_ready(self) -> bool:
        return False

    def construct_write_capability(
        self,
        *,
        settings: Settings,
        factory: LiveWriteFactory,
    ) -> BrokerWritePort:
        assert settings.trading_mode is TradingMode.LIVE
        self.factory_called = True
        return factory()


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
    assert SchedulerJob.PAPER_EXECUTION in paper.scheduler_jobs
    assert SchedulerJob.LIVE_EXECUTION not in paper.scheduler_jobs

    assert BrokerCapability.ROBINHOOD_WRITE in live.broker
    assert BrokerCapability.PAPER_WRITE not in live.broker
    assert RuntimePackage.WEBAUTHN_LIVE_APPROVAL in live.packages
    assert RuntimePackage.TELEGRAM_PAPER_APPROVAL not in live.packages
    assert RuntimeSecret.ROBINHOOD_WRITE in live.secrets
    assert SchedulerJob.LIVE_EXECUTION in live.scheduler_jobs


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
def test_production_live_rejects_non_production_guard_before_factory() -> None:
    guard = _AllowingTestGuard()
    live = _live_settings().model_copy(update={"ainvest_env": AinvestEnv.PRODUCTION})

    with pytest.raises(RuntimeStartupError) as exc_info:
        start_runtime(
            live,
            live_guard=guard,
            live_write_factory=_WritePort,
        )

    assert exc_info.value.code is RuntimeStartupErrorCode.LIVE_GUARD_NOT_PRODUCTION_READY
    assert guard.factory_called is False


@pytest.mark.unit
def test_live_write_is_constructed_only_through_explicit_guard() -> None:
    guard = _AllowingTestGuard()

    runtime = start_runtime(
        _live_settings(),
        live_guard=guard,
        live_write_factory=_WritePort,
    )

    assert guard.factory_called is True
    assert isinstance(runtime.broker_write, BrokerWritePort)
    assert BrokerCapability.ROBINHOOD_WRITE in runtime.capabilities.broker
    assert runtime.active_broker_capabilities == frozenset({BrokerCapability.ROBINHOOD_WRITE})


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
