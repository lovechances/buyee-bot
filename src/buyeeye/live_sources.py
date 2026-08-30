from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .browser_session import load_session_cookies
from .normalize import normalize_brand, normalize_text


@dataclass
class LiveListing:
    source: str
    source_listing_id: str
    url: str
    title_original: str
    title_normalized: str
    brand: str
    category: str
    current_price: int | None
    buyout_price: int | None
    starting_price: int | None
    seller_name: str | None
    condition: str | None
    image_url: str | None = None
    raw: dict[str, Any] | None = None


class ListingSource(ABC):
    @abstractmethod
    async def fetch(self, query: str | None = None) -> list[LiveListing]:
        raise NotImplementedError


def load_live_queries(config_path: str | Path = "config/searches.yaml") -> list[str]:
    path = Path(config_path)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    families = payload.get("search_families", [])
    queries: list[str] = []
    for family in families:
        aliases = family.get("aliases", [])
        for alias in aliases:
            queries.append(str(alias))
        for term in family.get("terms", []):
            for alias in aliases[:1]:
                queries.append(f"{alias} {term}")
    return sorted({q.strip() for q in queries if q and q.strip()})


class BuyeeAuthenticatedSource(ListingSource):
    async def fetch(self, query: str | None = None) -> list[LiveListing]:
        cookies = load_session_cookies()
        if not cookies:
            return []
        params = {"q": query} if query else None
        url = "https://buyee.jp/jdirectitems/auction"
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, cookies=cookies) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        listings: list[LiveListing] = []
        for item in soup.select("a[href*='item']")[:10]:
            href = item.get("href") or ""
            title = normalize_text(item.get_text(" ", strip=True))
            if not title:
                continue
            price = self._extract_price(title)
            listings.append(
                LiveListing(
                    source="buyee",
                    source_listing_id=re.sub(r"\D+", "", href)[:32] or title[:32],
                    url=href if href.startswith("http") else f"https://buyee.jp{href}",
                    title_original=title,
                    title_normalized=normalize_text(title),
                    brand=normalize_brand(self._extract_brand(title)),
                    category="unknown",
                    current_price=price,
                    buyout_price=price,
                    starting_price=price,
                    seller_name=None,
                    condition=None,
                    raw={"query": query or "buyee-auction"},
                )
            )
        return listings

    @staticmethod
    def _extract_brand(title: str) -> str:
        for marker in ["Number (N)ine", "Chrome Hearts", "Needles", "Rick Owens", "Dior Homme", "Saint Laurent"]:
            if marker.lower() in title.lower():
                return marker
        return "unknown"

    @staticmethod
    def _extract_price(text: str) -> int | None:
        match = re.search(r"(?:¥|JPY|JPY\s*)\s*([0-9][0-9,]*)", text)
        if not match:
            return None
        return int(match.group(1).replace(",", ""))


class BuyeeYahooSearchSource(ListingSource):
    async def fetch(self, query: str | None = None) -> list[LiveListing]:
        url = "https://buyee.jp/jdirectitems/auction"
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        text = response.text
        soup = BeautifulSoup(text, "lxml")
        listings: list[LiveListing] = []
        for item in soup.select("a[href*='item']")[:10]:
            href = item.get("href") or ""
            title = normalize_text(item.get_text(" ", strip=True))
            if not title:
                continue
            price = self._extract_price(title)
            listings.append(
                LiveListing(
                    source="buyee",
                    source_listing_id=re.sub(r"\D+", "", href)[:32] or title[:32],
                    url=href if href.startswith("http") else f"https://buyee.jp{href}",
                    title_original=title,
                    title_normalized=normalize_text(title),
                    brand=normalize_brand(self._extract_brand(title)),
                    category="unknown",
                    current_price=price,
                    buyout_price=price,
                    starting_price=price,
                    seller_name=None,
                    condition=None,
                    raw={"query": query or "buyee-auction"},
                )
            )
        return listings

    @staticmethod
    def _extract_brand(title: str) -> str:
        for marker in ["Number (N)ine", "Chrome Hearts", "Needles", "Rick Owens", "Dior Homme", "Saint Laurent"]:
            if marker.lower() in title.lower():
                return marker
        return "unknown"

    @staticmethod
    def _extract_price(text: str) -> int | None:
        match = re.search(r"(?:¥|JPY|JPY\s*)\s*([0-9][0-9,]*)", text)
        if not match:
            return None
        return int(match.group(1).replace(",", ""))


class YahooAuctionSource(ListingSource):
    async def fetch(self, query: str | None = None) -> list[LiveListing]:
        if not query:
            return []
        encoded = query.replace(" ", "+")
        url = f"https://auctions.yahoo.co.jp/search?p={encoded}&u=1"
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        listings: list[LiveListing] = []
        for item in soup.select("a[href*='auction']")[:10]:
            href = item.get("href") or ""
            title = normalize_text(item.get_text(" ", strip=True))
            if not title:
                continue
            price = re.search(r"[¥￥][\s]*([0-9,]+)", title)
            listings.append(
                LiveListing(
                    source="yahoo",
                    source_listing_id=re.sub(r"\D+", "", href)[:32] or title[:32],
                    url=href if href.startswith("http") else f"https://auctions.yahoo.co.jp{href}",
                    title_original=title,
                    title_normalized=normalize_text(title),
                    brand=normalize_brand(self._extract_brand(title)),
                    category="unknown",
                    current_price=int(price.group(1).replace(",", "")) if price else None,
                    buyout_price=None,
                    starting_price=int(price.group(1).replace(",", "")) if price else None,
                    seller_name=None,
                    condition=None,
                    raw={"query": query},
                )
            )
        return listings

    @staticmethod
    def _extract_brand(title: str) -> str:
        for marker in ["Number (N)ine", "Chrome Hearts", "Needles", "Rick Owens", "Dior Homme", "Saint Laurent"]:
            if marker.lower() in title.lower():
                return marker
        return "unknown"


class LiveMarketLoop:
    def __init__(self, sources: list[ListingSource] | None = None) -> None:
        self.sources = sources or [BuyeeAuthenticatedSource(), YahooAuctionSource()]

    async def run_once(self, query: str) -> list[LiveListing]:
        combined: list[LiveListing] = []
        for source in self.sources:
            try:
                combined.extend(await source.fetch(query))
            except (httpx.HTTPError, ValueError):
                continue
        return combined
