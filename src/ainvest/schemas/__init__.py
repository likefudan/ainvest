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
)
from ainvest.schemas.market import (
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
    "InstrumentIdentity",
    "MarketQuote",
    "OhlcvBar",
    "Provenance",
    "QualityFlag",
    "ResearchMarketSection",
    "ResearchPacket",
    "ResearchPortfolioSection",
    "TechnicalIndicators",
    "ThesisSection",
    "parse_research_packet",
    "research_packet_example",
]
