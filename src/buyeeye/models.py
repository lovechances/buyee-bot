from __future__ import annotations

from dataclasses import dataclass, field
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
