from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ListingCandidate:
    source: str
    source_listing_id: str
    url: str
    title_original: str
    title_normalized: str = ""
    brand: str = ""
    category: str = ""
    current_price: int | None = None
    buyout_price: int | None = None
    starting_price: int | None = None
    seller_name: str | None = None
    condition: str | None = None
    material: str | None = None
    collection: str | None = None
    designer_era: str | None = None
    tags: list[str] = field(default_factory=list)
    negative_terms: list[str] = field(default_factory=list)
    year: str | None = None
    season: str | None = None
    recommendations: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def price_or_start(self) -> int:
        return self.current_price or self.buyout_price or self.starting_price or 0


@dataclass
class Comp:
    source: str
    title: str
    price_jpy: int | None
    similarity: float
    recency_days: int
    category: str | None = None
    is_sold: bool = True
    provenance: str = "observed_market"
    source_listing_id: str | None = None
    url: str | None = None
    query: str | None = None
    sold_at: datetime | None = None
    original_price: float | None = None
    original_currency: str | None = None
    shipping_original: float | None = None
    condition: str | None = None
    size: str | None = None
    # A page can be sold while its displayed amount is not the accepted offer.
    # Producers must explicitly establish realized amounts; source names alone
    # are not transaction verification.
    price_evidence: str = "unknown"
    evidence_observed_at: datetime | None = None
    evidence_note: str | None = None
    stock_identity: str | None = None
    # Optional human/producer evidence about this exact product, independent
    # from observing a sale. Never inferred merely from source or shared brand.
    match_evidence: str | None = None


@dataclass
class IntelligenceProposal:
    id: int
    term: str
    brand: str
    reason: str
    support_count: int
    negative_count: int
    confidence: float
    status: str = "proposed"
