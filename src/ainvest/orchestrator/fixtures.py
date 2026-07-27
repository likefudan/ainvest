"""Fixed fixtures for the deterministic paper flow (P03-T16).

Used by the CLI and integration tests so both share one ResearchPacket /
risk / sizing baseline. Synthetic risk limits are explicit (DEC-011/012).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Any

from ainvest.orchestrator.paper_loop import PaperFlowConfig
from ainvest.orchestrator.types import DEFAULT_AS_OF, FIXED_OPENING_CASH
from ainvest.portfolio import SizingConfig
from ainvest.risk import (
    AllowlistEntry,
    EligibilityLimits,
    ExposureInputs,
    ExposureLimits,
    InstrumentMetadata,
    MarketQualityLimits,
    OrderConflictLimits,
    PhaseMarketQualityLimits,
    RiskRuleConfig,
    SectorAssignment,
)
from ainvest.schemas.common import AssetType
from ainvest.schemas.examples import (
    market_quote_example,
    strategy_context_example,
)
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.research import research_packet_example
from ainvest.schemas.strategy import StrategyContext, parse_strategy_context

AS_OF = DEFAULT_AS_OF
AS_OF_ISO = "2026-07-23T15:00:00Z"
QUOTE_OBSERVED = "2026-07-23T14:59:50Z"
QUOTE_RECEIVED = "2026-07-23T14:59:55Z"


def _rebase_timestamps(payload: dict[str, Any], *, as_of: str = AS_OF_ISO) -> dict[str, Any]:
    """Recursively replace known example timestamps with the paper-flow clock."""
    replacements = {
        "2026-07-24T18:30:00Z": as_of,
        "2026-07-24T18:29:58Z": QUOTE_OBSERVED,
        "2026-07-24T18:00:00Z": "2026-07-23T14:00:00Z",
    }

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, str):
            return replacements.get(value, value)
        return value

    return walk(payload)  # type: ignore[no-any-return]


def make_cash_portfolio(
    *,
    cash: str = "10000.00",
    as_of: str = AS_OF_ISO,
) -> PortfolioSnapshot:
    return PortfolioSnapshot.model_validate(
        {
            "schema_version": "1.0",
            "snapshot_id": "port_01HZYD4APAPER0001",
            "account_scope": "paper",
            "as_of": as_of,
            "currency": "USD",
            "cash": cash,
            "buying_power": cash,
            "equity": cash,
            "positions": [],
            "open_orders": [],
            "exposure": {
                "cash": cash,
                "equity": cash,
                "gross_market_value": "0",
                "net_market_value": "0",
                "largest_position_weight": "0",
                "position_count": 0,
            },
            "provenance": {
                "source": "ainvest.paper.fixture",
                "observed_at": QUOTE_OBSERVED,
                "received_at": as_of,
                "timezone": "UTC",
                "is_delayed": False,
                "quality_flags": [],
            },
        }
    )


def make_strategy_context(
    *,
    sma_20: str = "211.30",
    sma_50: str = "204.80",
    cash: str = "10000.00",
) -> StrategyContext:
    """MA buy fixture: prior fast_above_slow=False and sma_20 > sma_50."""
    payload = _rebase_timestamps(deepcopy(strategy_context_example()))
    research = _rebase_timestamps(deepcopy(research_packet_example()))
    research["technical"]["sma_20"] = sma_20
    research["technical"]["sma_50"] = sma_50
    research["market"]["last_price"] = "214.50"
    research["market"]["bid"] = "214.48"
    research["market"]["ask"] = "214.52"
    payload["as_of"] = AS_OF_ISO
    payload["research"] = research
    payload["portfolio"] = make_cash_portfolio(cash=cash).model_dump(mode="json")
    payload["strategy_state"] = {
        "strategy": "moving_average",
        "strategy_version": "1.0.0",
        "updated_at": "2026-07-23T14:00:00Z",
        "entries": [
            {
                "key": "fast_above_slow",
                "kind": "BOOLEAN",
                "boolean_value": False,
            }
        ],
    }
    return parse_strategy_context(payload)


def make_quote(**overrides: Any) -> MarketQuote:
    payload: dict[str, Any] = {
        **market_quote_example(),
        "last_price": "214.50",
        "bid": "214.48",
        "ask": "214.52",
        "instrument": {
            "instrument_id": "rh_inst_aapl_xnas",
            "symbol": "AAPL",
            "exchange": "XNAS",
            "currency": "USD",
            "asset_type": "EQUITY",
            "identity_as_of": AS_OF_ISO,
        },
        "provenance": {
            "source": "ainvest.paper.fixture",
            "observed_at": QUOTE_OBSERVED,
            "received_at": QUOTE_RECEIVED,
            "timezone": "UTC",
            "is_delayed": False,
            "quality_flags": [],
        },
    }
    payload.update(overrides)
    return MarketQuote.model_validate(_rebase_timestamps(payload))


def make_instrument(**overrides: Any) -> InstrumentMetadata:
    payload: dict[str, Any] = {
        "instrument_id": "rh_inst_aapl_xnas",
        "symbol": "AAPL",
        "exchange": "XNAS",
        "currency": "USD",
        "asset_type": "EQUITY",
        "tradable": True,
        "price_increment": "0.01",
        "quantity_increment": "1",
    }
    payload.update(overrides)
    return InstrumentMetadata.model_validate(payload)


def make_phase_limits(
    *,
    age: int = 120,
    spread: str = "100",
    deviation: str = "500",
    vol: str = "1000",
) -> PhaseMarketQualityLimits:
    return PhaseMarketQualityLimits(
        max_quote_age_seconds=age,
        max_spread_bps=Decimal(spread),
        max_limit_deviation_bps=Decimal(deviation),
        max_short_term_volatility_bps=Decimal(vol),
    )


def make_risk_config(
    *,
    allowlist: tuple[AllowlistEntry, ...] | None = None,
    max_order_notional: str = "10000",
) -> RiskRuleConfig:
    phase = make_phase_limits()
    return RiskRuleConfig(
        rule_set_version="risk-rules-1.0.0",
        eligibility=EligibilityLimits(
            allowlist=allowlist
            or (
                AllowlistEntry(
                    instrument_id="rh_inst_aapl_xnas",
                    symbol="AAPL",
                    exchange="XNAS",
                    currency="USD",
                    asset_type=AssetType.EQUITY,
                ),
            )
        ),
        market_quality=MarketQualityLimits(
            proposal=phase,
            pretrade=phase,
            max_clock_skew_seconds=30,
        ),
        exposure=ExposureLimits(
            max_order_notional=Decimal(max_order_notional),
            max_symbol_weight=Decimal("0.50"),
            max_sector_weight=Decimal("0.80"),
            max_daily_turnover=Decimal("50000"),
            min_cash_reserve_weight=Decimal("0"),
            max_daily_loss=Decimal("10000"),
        ),
        order_conflicts=OrderConflictLimits(duplicate_window_seconds=300),
    )


def make_sizing_config() -> SizingConfig:
    return SizingConfig(
        quantity_increment=Decimal("1"),
        price_increment=Decimal("0.01"),
        min_notional=Decimal("1.00"),
        max_notional=Decimal("5000.00"),
        cash_reserve=Decimal("100.00"),
        candidate_ttl_seconds=120,
    )


def make_exposure_inputs(
    *,
    instrument_id: str = "rh_inst_aapl_xnas",
) -> ExposureInputs:
    return ExposureInputs(
        sectors=(SectorAssignment(instrument_id=instrument_id, sector="TECH"),),
        daily_turnover_to_date=Decimal("0"),
        daily_realized_pnl=Decimal("0"),
        daily_unrealized_pnl=Decimal("0"),
    )


def make_paper_flow_config(
    *,
    inject_approval: bool = False,
    expire_approval: bool = False,
    market_liquidity: str = "100",
    risk_config: RiskRuleConfig | None = None,
    write_port: Any = None,
    raise_unknown_on_submit: bool = False,
    as_of: datetime | None = None,
) -> PaperFlowConfig:
    portfolio = make_cash_portfolio()
    instrument = make_instrument()
    return PaperFlowConfig(
        context=make_strategy_context(),
        quote=make_quote(),
        portfolio=portfolio,
        risk_config=risk_config or make_risk_config(),
        sizing_config=make_sizing_config(),
        instrument=instrument,
        as_of=as_of or AS_OF,
        inject_approval=inject_approval,
        expire_approval=expire_approval,
        market_liquidity=Decimal(market_liquidity),
        opening_cash=FIXED_OPENING_CASH,
        exposure_inputs=make_exposure_inputs(),
        write_port=write_port,
        raise_unknown_on_submit=raise_unknown_on_submit,
    )


__all__ = [
    "AS_OF",
    "AS_OF_ISO",
    "make_cash_portfolio",
    "make_exposure_inputs",
    "make_instrument",
    "make_paper_flow_config",
    "make_quote",
    "make_risk_config",
    "make_sizing_config",
    "make_strategy_context",
]
