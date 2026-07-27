"""Shared builders for unit risk engine and rule tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from ainvest.risk.models import (
    AllowlistEntry,
    EligibilityLimits,
    EvaluationPhase,
    ExposureInputs,
    ExposureLimits,
    InstrumentMetadata,
    MarketQualityLimits,
    PhaseMarketQualityLimits,
    RiskContext,
    RiskRuleConfig,
)
from ainvest.schemas.common import AssetType
from ainvest.schemas.examples import candidate_order_example, market_quote_example
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.orders import CandidateOrder
from ainvest.schemas.portfolio import PortfolioSnapshot

DEFAULT_AS_OF = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)


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


def make_market_quality(
    *,
    proposal: PhaseMarketQualityLimits | None = None,
    pretrade: PhaseMarketQualityLimits | None = None,
    max_clock_skew_seconds: int = 30,
) -> MarketQualityLimits:
    phase = proposal or make_phase_limits()
    return MarketQualityLimits(
        proposal=phase,
        pretrade=pretrade or phase,
        max_clock_skew_seconds=max_clock_skew_seconds,
    )


def make_exposure_limits(
    *,
    max_notional: str = "10000",
    max_symbol: str = "0.50",
    max_sector: str = "0.80",
    max_turnover: str = "50000",
    min_cash: str = "0.0",
    max_loss: str = "10000",
) -> ExposureLimits:
    return ExposureLimits(
        max_order_notional=Decimal(max_notional),
        max_symbol_weight=Decimal(max_symbol),
        max_sector_weight=Decimal(max_sector),
        max_daily_turnover=Decimal(max_turnover),
        min_cash_reserve_weight=Decimal(min_cash),
        max_daily_loss=Decimal(max_loss),
    )


def make_allowlist_entry(
    *,
    instrument_id: str = "rh_inst_aapl_xnas",
    symbol: str = "AAPL",
    exchange: str = "XNAS",
    currency: str = "USD",
    asset_type: AssetType = AssetType.EQUITY,
) -> AllowlistEntry:
    return AllowlistEntry(
        instrument_id=instrument_id,
        symbol=symbol,
        exchange=exchange,
        currency=currency,
        asset_type=asset_type,
    )


def make_risk_config(
    *,
    market_quality: MarketQualityLimits | None = None,
    exposure: ExposureLimits | None = None,
    allowlist: tuple[AllowlistEntry, ...] | None = None,
    rule_set_version: str = "risk-rules-1.0.0",
) -> RiskRuleConfig:
    return RiskRuleConfig(
        rule_set_version=rule_set_version,
        eligibility=EligibilityLimits(allowlist=allowlist or (make_allowlist_entry(),)),
        market_quality=market_quality or make_market_quality(),
        exposure=exposure or make_exposure_limits(),
    )


def make_instrument(**overrides: object) -> InstrumentMetadata:
    payload: dict[str, object] = {
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


def make_candidate(**overrides: object) -> CandidateOrder:
    payload: dict[str, object] = {**candidate_order_example(), "account_scope": "paper"}
    payload.update(overrides)
    return CandidateOrder.model_validate(payload)


def make_quote(**overrides: object) -> MarketQuote:
    payload: dict[str, object] = dict(market_quote_example())
    payload.update(overrides)
    return MarketQuote.model_validate(payload)


def make_fresh_quote(
    *,
    observed: str,
    received: str,
    bid: str = "214.48",
    ask: str = "214.52",
    last_price: str = "214.50",
    **overrides: object,
) -> MarketQuote:
    payload: dict[str, object] = {
        **market_quote_example(),
        "last_price": last_price,
        "bid": bid,
        "ask": ask,
        "provenance": {
            "source": "test",
            "observed_at": observed,
            "received_at": received,
            "timezone": "UTC",
            "is_delayed": False,
            "quality_flags": [],
        },
    }
    payload.update(overrides)
    return MarketQuote.model_validate(payload)


def make_context(
    *,
    risk_decision_id: str = "risk_01HZYTEST0000001",
    phase: EvaluationPhase = EvaluationPhase.PROPOSAL,
    as_of: datetime | None = None,
    candidate: CandidateOrder | None = None,
    quote: MarketQuote | None = None,
    instrument: InstrumentMetadata | None = None,
    config: RiskRuleConfig | None = None,
    portfolio: PortfolioSnapshot | None = None,
    short_term_volatility_bps: Decimal | None = Decimal("10"),
    exposure_inputs: ExposureInputs | None = None,
    **extra: object,
) -> RiskContext:
    payload: dict[str, object] = {
        "risk_decision_id": risk_decision_id,
        "phase": phase,
        "as_of": as_of or DEFAULT_AS_OF,
        "candidate": candidate or make_candidate(),
        "quote": quote or make_quote(),
        "instrument": instrument or make_instrument(),
        "config": config or make_risk_config(),
        "portfolio": portfolio,
        "short_term_volatility_bps": short_term_volatility_bps,
        "exposure_inputs": exposure_inputs,
    }
    payload.update(extra)
    return RiskContext.model_validate(payload)
