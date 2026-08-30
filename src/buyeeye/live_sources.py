from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from .browser_session import SESSION_DIR, STATE_PATH, load_session_cookies, restore_session_cookies, restore_session_cookies_async
from .db import save_listings
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
            result = self._fetch_via_browser(query)
            return await result if hasattr(result, "__await__") else result

        params = {"q": query} if query else None
        url = "https://buyee.jp/jdirectitems/auction"
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, cookies=cookies) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            result = self._fetch_via_browser(query)
            return await result if hasattr(result, "__await__") else result

        return self._parse_listing_html(response.text, query)

    async def _fetch_via_browser(self, query: str | None = None) -> list[LiveListing]:
        from playwright.async_api import async_playwright

        url = "https://buyee.jp/jdirectitems/auction"
        if query:
            url = f"{url}?q={quote_plus(query)}"

        async with async_playwright() as p:
            browser = await p.chromium.launch_persistent_context(
                str(SESSION_DIR),
                headless=False,
                viewport={"width": 1440, "height": 1100},
            )
            try:
                await restore_session_cookies_async(browser, STATE_PATH)
                page = await browser.new_page()
                await page.goto("https://buyee.jp/", wait_until="domcontentloaded", timeout=45000)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(4000)
                data = await page.evaluate(
                    """
                    () => Array.from(document.querySelectorAll('a[href]'))
                        .map(a => ({
                            href: a.href,
                            text: (a.textContent || '').replace(/\\s+/g, ' ').trim()
                        }))
                        .filter(x => x.href.includes('/item/jdirectitems/auction/'))
                        .slice(0, 20)
                    """
                )
                return self._items_from_browser_payload(data, query)
            finally:
                await browser.close()

    @staticmethod
    def _item_id_from_href(href: str) -> str:
        match = re.search(r"/auction/([A-Za-z0-9]+)(?:\?|$)", href)
        if match:
            return match.group(1)
        match = re.search(r"/item/([A-Za-z0-9]+)(?:\?|$)", href)
        if match:
            return match.group(1)
        compact = re.sub(r"[^A-Za-z0-9]", "", href)
        return compact[-32:] if len(compact) > 32 else compact

    @staticmethod
    def _items_from_browser_payload(rows: list[dict[str, str]], query: str | None = None) -> list[LiveListing]:
        listings: list[LiveListing] = []
        seen: set[str] = set()
        for row in rows:
            href = row.get("href", "")
            text = normalize_text(row.get("text", "") or "")
            if not href or not text:
                continue
            item_id = BuyeeAuthenticatedSource._item_id_from_href(href)
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            price = BuyeeAuthenticatedSource._extract_price(text)
            raw = {
                "query": query or "buyee-auction",
                "time_remaining": None,
            }
            time_match = re.search(r"Time Remaining\s+([^\n]+)", text, flags=re.I)
            if time_match:
                raw["time_remaining"] = time_match.group(1)
            listings.append(
                LiveListing(
                    source="buyee",
                    source_listing_id=item_id,
                    url=href,
                    title_original=text,
                    title_normalized=normalize_text(text),
                    brand=normalize_brand(BuyeeAuthenticatedSource._extract_brand(text)),
                    category="unknown",
                    current_price=price,
                    buyout_price=None,
                    starting_price=price,
                    seller_name=None,
                    condition=None,
                    raw=raw,
                )
            )
        return listings

    @staticmethod
    def _parse_listing_html(html: str, query: str | None = None) -> list[LiveListing]:
        soup = BeautifulSoup(html, "lxml")
        listings: list[LiveListing] = []
        seen: set[str] = set()

        candidates = soup.select("a[href*='/item/jdirectitems/auction/'], a[href*='/item/search/']")
        for item in candidates[:40]:
            href = item.get("href") or ""
            text = normalize_text(item.get_text(" ", strip=True))
            if not href or not text:
                continue
            item_id = BuyeeAuthenticatedSource._item_id_from_href(href)
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            price = BuyeeAuthenticatedSource._extract_price(text)
            raw = {"query": query or "buyee-auction", "time_remaining": None}
            time_match = re.search(r"Time Remaining\s+([^\n]+)", text, flags=re.I)
            if time_match:
                raw["time_remaining"] = time_match.group(1)
            listings.append(
                LiveListing(
                    source="buyee",
                    source_listing_id=item_id,
                    url=href if href.startswith("http") else f"https://buyee.jp{href}",
                    title_original=text,
                    title_normalized=normalize_text(text),
                    brand=normalize_brand(BuyeeAuthenticatedSource._extract_brand(text)),
                    category="unknown",
                    current_price=price,
                    buyout_price=None,
                    starting_price=price,
                    seller_name=None,
                    condition=None,
                    raw=raw,
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
        match = re.search(r"(?:¥|JPY|YEN)\s*([0-9][0-9,]*)|([0-9][0-9,]*)\s*(?:YEN|JPY|¥)", text, flags=re.I)
        if not match:
            return None
        value = match.group(1) or match.group(2)
        if value is None:
            return None
        return int(value.replace(",", ""))


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
                items = await source.fetch(query)
                combined.extend(items)
            except (httpx.HTTPError, ValueError):
                continue
        persisted = save_listings(combined)
        return combined if persisted is None else combined
