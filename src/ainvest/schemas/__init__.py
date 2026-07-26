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
from ainvest.schemas.research import (
    EvidenceCitation,
    EvidenceKind,
    ResearchPacket,
    ThesisSection,
    parse_research_packet,
    research_packet_example,
)

__all__ = [
    "SCHEMA_VERSION_V1",
    "AssetType",
    "EvidenceCitation",
    "EvidenceKind",
    "FactValueKind",
    "FundamentalFact",
    "FundamentalSnapshot",
    "InstrumentIdentity",
    "MarketEvent",
    "MarketQuote",
    "OhlcvBar",
    "Provenance",
    "QualityFlag",
    "ResearchMarketSection",
    "ResearchPacket",
    "ResearchPortfolioSection",
    "TechnicalIndicators",
    "ThesisSection",
    "decimal_json_schema",
    "parse_research_packet",
    "research_packet_example",
]
