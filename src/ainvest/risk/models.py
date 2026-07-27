"""Risk engine domain models (P03-T8).

Immutable evaluation inputs and per-rule results. Exposure-limit config lives
in C4b; this module owns the shared context and market-quality / eligibility
configuration required by C4a.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    AssetType,
    CurrencyCode,
    DomainModel,
    ExchangeMic,
    MachineCode,
    Money,
    NonNegativeDecimal,
    OrderSide,
    PnL,
    PositiveDecimal,
    SchemaVersion,
    StableId,
    Symbol,
    UtcDateTime,
    Weight,
)
from ainvest.schemas.market import MarketQuote
from ainvest.schemas.orders import CandidateOrder, OrderHashDigest
from ainvest.schemas.portfolio import PortfolioSnapshot
from ainvest.schemas.risk import RiskOutcome, RiskSeverity


class EvaluationPhase(StrEnum):
    """When risk is evaluated; thresholds may differ by phase (P03-T11)."""

    PROPOSAL = "PROPOSAL"
    PRETRADE = "PRETRADE"


class InstrumentMetadata(DomainModel):
    """Broker-validated instrument facts required by eligibility rules."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    instrument_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    symbol: Symbol
    exchange: ExchangeMic
    currency: CurrencyCode
    asset_type: AssetType
    tradable: bool
    price_increment: PositiveDecimal
    quantity_increment: PositiveDecimal
    is_leveraged_or_inverse: bool = False
    allows_short: bool = False
    allows_margin: bool = False
    is_option: bool = False
    is_crypto: bool = False


class AllowlistEntry(DomainModel):
    """One allowlisted ordinary US equity/ETF identity."""

    instrument_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    symbol: Symbol
    exchange: ExchangeMic
    currency: CurrencyCode = "USD"
    asset_type: AssetType


class EligibilityLimits(DomainModel):
    """Explicit eligibility policy (no implicit allow-all)."""

    allowlist: tuple[AllowlistEntry, ...] = Field(min_length=1)


class PhaseMarketQualityLimits(DomainModel):
    """Market-quality thresholds for one evaluation phase."""

    max_quote_age_seconds: Annotated[int, Field(ge=1, le=86_400)]
    max_spread_bps: NonNegativeDecimal
    max_limit_deviation_bps: NonNegativeDecimal
    max_short_term_volatility_bps: NonNegativeDecimal


class MarketQualityLimits(DomainModel):
    """Separate proposal vs pre-trade market-quality thresholds (P03-T11)."""

    proposal: PhaseMarketQualityLimits
    pretrade: PhaseMarketQualityLimits
    max_clock_skew_seconds: Annotated[int, Field(ge=0, le=3600)]


class ExposureLimits(DomainModel):
    """Explicit exposure limits (P03-T9). Every field is required; no defaults."""

    max_order_notional: PositiveDecimal
    max_symbol_weight: Weight
    max_sector_weight: Weight
    max_daily_turnover: PositiveDecimal
    min_cash_reserve_weight: Weight
    max_daily_loss: PositiveDecimal

    @model_validator(mode="after")
    def _weights_in_unit_interval(self) -> ExposureLimits:
        # Weight type already enforces [0, 1]; keep an explicit cross-field note.
        if self.max_symbol_weight > 1 or self.max_sector_weight > 1:
            raise ValueError("weights must be <= 1")
        return self


class SectorAssignment(DomainModel):
    """Sector label for one instrument (fail closed when missing)."""

    instrument_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    sector: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class ExposureInputs(DomainModel):
    """Daily and sector inputs required by exposure rules (P03-T9)."""

    sectors: tuple[SectorAssignment, ...] = ()
    daily_turnover_to_date: Money
    daily_realized_pnl: PnL
    daily_unrealized_pnl: PnL | None = None


class OrderConflictLimits(DomainModel):
    """Duplicate / open-order conflict policy (P03-T12). No tradable defaults."""

    duplicate_window_seconds: Annotated[int, Field(ge=1, le=86_400)]


class RecentOrderSubmission(DomainModel):
    """Prior submission used for duplicate detection (hash / client id / window)."""

    client_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    order_hash: OrderHashDigest
    instrument_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    symbol: Symbol
    side: OrderSide
    submitted_at: UtcDateTime
    proposal_id: StableId | None = None


class KillSwitchSnapshot(DomainModel):
    """Immutable kill-switch view for one risk evaluation (DEC-008).

    Configured and operational sources are independent; any active source blocks
    new orders. This snapshot never triggers automatic cancellation.
    """

    configured_active: bool = False
    operational_active: bool = False
    reason: Annotated[str, StringConstraints(min_length=1, max_length=512)] | None = None
    updated_at: UtcDateTime | None = None

    @property
    def is_active(self) -> bool:
        return self.configured_active or self.operational_active

    @property
    def active_sources(self) -> tuple[str, ...]:
        sources: list[str] = []
        if self.configured_active:
            sources.append("CONFIGURED")
        if self.operational_active:
            sources.append("OPERATIONAL")
        return tuple(sources)


class RiskRuleConfig(DomainModel):
    """Rule configuration. Missing sections fail closed at construction."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    rule_set_version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    eligibility: EligibilityLimits
    market_quality: MarketQualityLimits
    exposure: ExposureLimits
    order_conflicts: OrderConflictLimits


class RuleResult(DomainModel):
    """Single rule outcome (P03-T8 checklist)."""

    rule_code: MachineCode
    severity: RiskSeverity
    decision: RiskOutcome
    reason: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    evidence: Annotated[str, StringConstraints(min_length=1, max_length=1024)] | None = None

    @model_validator(mode="after")
    def _severity_matches_decision(self) -> RuleResult:
        if self.severity is RiskSeverity.HARD and self.decision is not RiskOutcome.REJECTED:
            raise ValueError("HARD severity requires REJECTED decision")
        if self.severity is RiskSeverity.REVIEW and self.decision is RiskOutcome.APPROVED:
            raise ValueError("REVIEW severity cannot APPROVE")
        if self.decision is RiskOutcome.REJECTED and self.severity is not RiskSeverity.HARD:
            raise ValueError("REJECTED decision requires HARD severity")
        if self.decision is RiskOutcome.NEEDS_REVIEW and self.severity is not RiskSeverity.REVIEW:
            raise ValueError("NEEDS_REVIEW decision requires REVIEW severity")
        if self.decision is RiskOutcome.APPROVED and self.severity not in {
            RiskSeverity.INFO,
        }:
            raise ValueError("APPROVED decision requires INFO severity")
        return self


class RiskContext(DomainModel):
    """Immutable inputs for one risk evaluation."""

    schema_version: SchemaVersion = SCHEMA_VERSION_V1
    risk_decision_id: StableId
    phase: EvaluationPhase
    as_of: UtcDateTime
    candidate: CandidateOrder
    quote: MarketQuote
    instrument: InstrumentMetadata
    config: RiskRuleConfig
    portfolio: PortfolioSnapshot | None = None
    short_term_volatility_bps: NonNegativeDecimal | None = None
    exposure_inputs: ExposureInputs | None = None
    kill_switch: KillSwitchSnapshot | None = None
    recent_submissions: tuple[RecentOrderSubmission, ...] = ()
    client_order_id: Annotated[str, StringConstraints(min_length=3, max_length=128)] | None = None
    proposal_order_hash: OrderHashDigest | None = None


ZERO = Decimal("0")
BPS_DENOM = Decimal("10000")


__all__ = [
    "BPS_DENOM",
    "ZERO",
    "AllowlistEntry",
    "EligibilityLimits",
    "EvaluationPhase",
    "ExposureInputs",
    "ExposureLimits",
    "InstrumentMetadata",
    "KillSwitchSnapshot",
    "MarketQualityLimits",
    "OrderConflictLimits",
    "PhaseMarketQualityLimits",
    "RecentOrderSubmission",
    "RiskContext",
    "RiskRuleConfig",
    "RuleResult",
    "SectorAssignment",
]
