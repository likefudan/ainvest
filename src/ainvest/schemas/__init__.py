"""Versioned Pydantic domain contracts shared across packages.

Schemas are the shared dependency foundation. They must not import other
``ainvest`` boundary packages, and must not import SQLAlchemy ORM APIs.
Domain models stay separate from persistence/ORM models.
"""

from ainvest.schemas.approval import (
    ApprovalChallenge,
    ApprovalEvent,
    ApprovalMethod,
    ApprovalScope,
)
from ainvest.schemas.broker import (
    BrokerFill,
    BrokerOrder,
    CancelCommand,
    CancelResult,
    ReconciliationResult,
)
from ainvest.schemas.common import (
    SCHEMA_VERSION_V1,
    AssetType,
    InstrumentIdentity,
    MachineCode,
    OrderSide,
    Provenance,
    QualityFlag,
    decimal_json_schema,
)
from ainvest.schemas.export import (
    EXPORTED_MODELS,
    check_json_schemas,
    export_json_schemas,
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
from ainvest.schemas.orders import (
    CandidateOrder,
    OrderProposal,
    OrderType,
    TimeInForce,
    order_proposal_example,
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
from ainvest.schemas.risk import RiskDecision, RiskOutcome, RiskViolation
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
    "EXPORTED_MODELS",
    "SCHEMA_VERSION_V1",
    "AccountScope",
    "ApprovalChallenge",
    "ApprovalEvent",
    "ApprovalMethod",
    "ApprovalScope",
    "AssetType",
    "BrokerFill",
    "BrokerOrder",
    "CancelCommand",
    "CancelResult",
    "CandidateOrder",
    "EvidenceCitation",
    "EvidenceKind",
    "ExposureSnapshot",
    "FactValueKind",
    "FundamentalFact",
    "FundamentalSnapshot",
    "InstrumentIdentity",
    "MachineCode",
    "MarketEvent",
    "MarketQuote",
    "OhlcvBar",
    "OpenOrderSide",
    "OpenOrderSnapshot",
    "OrderProposal",
    "OrderSide",
    "OrderType",
    "PortfolioSnapshot",
    "PositionSnapshot",
    "Provenance",
    "QualityFlag",
    "ReconciliationResult",
    "ResearchMarketSection",
    "ResearchPacket",
    "ResearchPortfolioSection",
    "RiskDecision",
    "RiskOutcome",
    "RiskViolation",
    "SignalIntent",
    "StrategyContext",
    "StrategyState",
    "TechnicalIndicators",
    "ThesisSection",
    "TimeInForce",
    "TradeSignal",
    "check_json_schemas",
    "decimal_json_schema",
    "export_json_schemas",
    "order_proposal_example",
    "parse_research_packet",
    "parse_strategy_context",
    "parse_trade_signal",
    "parse_trade_signal_for_context",
    "research_packet_example",
    "trade_signal_example",
]
