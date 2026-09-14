from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml
from bs4 import BeautifulSoup

from .fashion import FashionProfile
from .models import Comp


class SoldCompSource(ABC):
    name: str

    @abstractmethod
    async def fetch(self, query: str) -> list[Comp]:
        raise NotImplementedError


def build_comp_queries(profile: FashionProfile) -> list[str]:
    if not profile.brand:
        return []
    brand = profile.brand.value
    identity = profile.collection or profile.era
    parts = [brand]
    if identity:
        parts.append(identity.value)
    if profile.item_type:
        parts.append(profile.item_type.value.replace("_", " "))
    if profile.material:
        parts.append(profile.material.value)
    if profile.collaboration:
        parts.append("collaboration")
    parts = list(dict.fromkeys(parts))
    queries = [" ".join(parts)]
    if profile.item_type:
        queries.append(" ".join([brand, profile.item_type.value.replace("_", " ")]))
    queries.append(brand)
    return list(dict.fromkeys(query for query in queries if query.strip()))


class YahooEndedSource(SoldCompSource):
    name = "yahoo_ended"
    base_url = "https://auctions.yahoo.co.jp/closedsearch/closedsearch"

    async def fetch(self, query: str) -> list[Comp]:
        url = f"{self.base_url}/{quote(query, safe='')}/0"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; BuyEye/0.1; sold-market research)"}
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
        return self.parse(response.text, query)

    @staticmethod
    def parse(html: str, query: str, now: datetime | None = None) -> list[Comp]:
        soup = BeautifulSoup(html, "lxml")
        observed_at = now or datetime.now(UTC)
        records: list[Comp] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href*='auctions.yahoo.co.jp/jp/auction/']"):
            url = anchor.get("href", "").split("?")[0]
            source_id = url.rstrip("/").rsplit("/", 1)[-1]
            if not source_id or source_id in seen:
                continue
            card = anchor.find_parent("li")
            if card is None:
                continue
            card_text = card.get_text(" ", strip=True)
            price_match = re.search(r"落札\s*([0-9,]+)\s*円", card_text)
            if not price_match:
                continue
            title_candidates = [
                item.get_text(" ", strip=True)
                for item in card.select("a[href]")
                if item.get_text(" ", strip=True)
                and "auctions.yahoo.co.jp/jp/auction/" in item.get("href", "")
            ]
            if not title_candidates:
                continue
            title = max(title_candidates, key=len)
            date_match = re.search(r"(\d{1,2})/(\d{1,2})\s+\d{1,2}:\d{2}\s*終了", card_text)
            sold_at = None
            if date_match:
                month, day = map(int, date_match.groups())
                year = observed_at.year - (1 if month > observed_at.month else 0)
                sold_at = datetime(year, month, day, tzinfo=UTC)
            recency = (observed_at - sold_at).days if sold_at else 180
            seen.add(source_id)
            price_jpy = int(price_match.group(1).replace(",", ""))
            records.append(
                Comp(
                    source="yahoo_ended",
                    title=title,
                    price_jpy=price_jpy,
                    similarity=1.0,
                    recency_days=max(0, recency),
                    is_sold=True,
                    provenance=f"yahoo_closed_search:{query}",
                    source_listing_id=source_id,
                    url=url,
                    query=query,
                    sold_at=sold_at,
                    original_price=float(price_jpy),
                    original_currency="JPY",
                    price_evidence="realized",
                    evidence_observed_at=observed_at,
                    evidence_note="Yahoo closed-search card explicitly labels the winning amount 落札",
                )
            )
        return records


class ObservedUSSoldSource(SoldCompSource):
    """Observed U.S. sold pages, with amount verification kept separate from status."""

    name = "grailed_sold"

    def __init__(
        self,
        path: str | Path = "config/us_sold_comps.yaml",
        jpy_to_usd: float = 0.00675,
    ):
        self.path = Path(path)
        self.jpy_to_usd = jpy_to_usd

    async def fetch(self, query: str) -> list[Comp]:
        if not self.path.exists() or self.jpy_to_usd <= 0:
            return []
        payload = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        query_text = query.casefold()
        records: list[Comp] = []
        for row in payload.get("sold_comps", []):
            terms = [str(term).casefold() for term in row.get("query_terms", [])]
            if terms and not all(term in query_text for term in terms):
                continue
            if row.get("market") != "US" or row.get("status") != "sold":
                continue
            price_usd = float(row["sold_price_usd"])
            records.append(
                Comp(
                    source="grailed_sold",
                    title=str(row["title"]),
                    price_jpy=round(price_usd / self.jpy_to_usd),
                    similarity=1.0,
                    recency_days=int(row.get("recency_days", 180)),
                    is_sold=True,
                    provenance=f"observed_public_grailed:{row['listing_id']}",
                    source_listing_id=str(row["listing_id"]),
                    url=str(row["url"]),
                    query=query,
                    original_price=price_usd,
                    original_currency="USD",
                    condition=row.get("condition"),
                    size=row.get("size"),
                    price_evidence=str(row.get("price_evidence", "unknown")),
                    evidence_observed_at=(
                        datetime.fromisoformat(str(row["evidence_observed_at"]))
                        if row.get("evidence_observed_at") else None
                    ),
                    evidence_note=row.get("evidence_note"),
                    stock_identity=row.get("stock_identity"),
                    match_evidence=row.get("match_evidence"),
                )
            )
        return records


def query_key(source: str, query: str) -> str:
    return hashlib.sha256(f"{source}\0{query.strip().lower()}".encode()).hexdigest()
