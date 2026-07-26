"""Versioned Pydantic domain contracts shared across packages.

Schemas are the shared dependency foundation. They must not import other
``ainvest`` boundary packages, and must not import SQLAlchemy ORM APIs.
Domain models stay separate from persistence/ORM models.
"""

from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    AssetType,
    InstrumentIdentity,
    Provenance,
    QualityFlag,
    decimal_json_schema,
)
from ainvest.schemas.market import (
    FactValueKind,
    FundamentalFact,
    FundamentalSnapshot,
    MarketEvent,
    MarketQuote,
    OhlcvBar,
    ResearchMarketSection,
    ResearchPortfolioSection,
    TechnicalIndicators,
)
from ainvest.schemas.portfolio import (
    AccountScope,
    ExposureSnapshot,
    OpenOrderSide,
    OpenOrderSnapshot,
    PortfolioSnapshot,
    PositionSnapshot,
)
from ainvest.schemas.research import (
    EvidenceCitation,
    EvidenceKind,
    ResearchPacket,
    ThesisSection,
    parse_research_packet,
    research_packet_example,
)
from ainvest.schemas.strategy import (
    SignalIntent,
    StrategyContext,
    StrategyState,
    TradeSignal,
    parse_strategy_context,
    parse_trade_signal,
    parse_trade_signal_for_context,
    trade_signal_example,
)

__all__ = [
    "SCHEMA_VERSION_V1",
    "AccountScope",
    "AssetType",
    "EvidenceCitation",
    "EvidenceKind",
    "ExposureSnapshot",
    "FactValueKind",
    "FundamentalFact",
    "FundamentalSnapshot",
    "InstrumentIdentity",
    "MarketEvent",
    "MarketQuote",
    "OhlcvBar",
    "OpenOrderSide",
    "OpenOrderSnapshot",
    "PortfolioSnapshot",
    "PositionSnapshot",
    "Provenance",
    "QualityFlag",
    "ResearchMarketSection",
    "ResearchPacket",
    "ResearchPortfolioSection",
    "SignalIntent",
    "StrategyContext",
    "StrategyState",
    "TechnicalIndicators",
    "ThesisSection",
    "TradeSignal",
    "decimal_json_schema",
    "parse_research_packet",
    "parse_strategy_context",
    "parse_trade_signal",
    "parse_trade_signal_for_context",
    "research_packet_example",
    "trade_signal_example",
]
