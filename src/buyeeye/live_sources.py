from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import httpx
import yaml
from bs4 import BeautifulSoup

from .browser_session import (
    SESSION_DIR,
    STATE_PATH,
    ensure_browser_dir,
    load_session_cookies,
    restore_session_cookies_async,
)
from .db import DB_PATH, save_listings
from .discovery import MarketplaceSearchFilters
from .normalize import normalize_brand, normalize_text


class AuthExpiredError(RuntimeError):
    pass


class ParserAnomalyError(RuntimeError):
    pass


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
    time_remaining: str | None = None
    buyout_indicator: str | None = None
    bid_count: int | None = None
    watch_count: int | None = None
    raw: dict[str, Any] | None = None
    desirability_score: int | None = None
    opportunity_score: int | None = None
    auth_risk: str | None = None
    comp_confidence: str | None = None


class ListingSource(ABC):
    @abstractmethod
    async def fetch(self, query: str | None = None) -> list[LiveListing]:
        raise NotImplementedError


def load_live_queries(config_path: str | Path = "config/searches.yaml") -> list[str]:
    path = Path(config_path)
    if not path.exists():
        return []
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    families = payload.get("search_families", [])
    queries: list[str] = []
    for family in families:
        aliases = family.get("aliases", [])
        for alias in aliases:
            queries.append(str(alias))
        terms = [
            *family.get("medium_terms", []),
            *family.get("surgical_terms", []),
            *family.get("terms", []),
        ]
        for term in terms:
            for alias in aliases[:1]:
                queries.append(f"{alias} {term}")
    return sorted({q.strip() for q in queries if q and q.strip()})


class BuyeeAuthenticatedSource(ListingSource):
    def __init__(
        self,
        max_pages: int = 1,
        search_filters: MarketplaceSearchFilters | None = None,
        discovery_context: dict[str, Any] | None = None,
    ):
        self.max_pages = max(1, min(max_pages, 5))
        self.last_page_count = 0
        self.search_filters = search_filters
        self.discovery_context = discovery_context or {}

    @staticmethod
    def _search_url(
        query: str | None,
        search_filters: MarketplaceSearchFilters | None = None,
    ) -> str:
        if not query:
            return "https://buyee.jp/jdirectitems/auction"
        url = f"https://buyee.jp/item/search/query/{quote(query, safe='')}"
        if search_filters and search_filters.category_id:
            url += f"/category/{search_filters.category_id}"
        if search_filters:
            encoded = search_filters.encoded_query()
            if encoded:
                url += f"?{encoded}"
        return url

    def _annotate_discovery(self, listings: list[LiveListing]) -> list[LiveListing]:
        if not self.discovery_context:
            return listings
        for listing in listings:
            listing.raw = {
                **(listing.raw or {}),
                "discovery": self.discovery_context,
            }
        return listings

    @staticmethod
    def _empty_page_state(body_text: str) -> str:
        if "Logout" not in body_text and re.search(r"(?i)\b(?:log in|login|sign in)\b", body_text):
            return "auth_expired"
        if re.search(r"(?i)no (?:items|results)|0 (?:items|results)|該当する商品", body_text):
            return "no_results"
        return "parser_anomaly"

    async def fetch(self, query: str | None = None) -> list[LiveListing]:
        if self.max_pages > 1:
            return await self._fetch_via_browser(query)
        cookies = load_session_cookies()
        if not cookies:
            result = self._fetch_via_browser(query)
            return await result if hasattr(result, "__await__") else result

        url = self._search_url(query, self.search_filters)
        try:
            async with httpx.AsyncClient(
                timeout=20.0, follow_redirects=True, cookies=cookies
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            result = self._fetch_via_browser(query)
            return await result if hasattr(result, "__await__") else result

        listings = self._parse_listing_html(response.text, query)
        if listings:
            self.last_page_count = 1
            return self._annotate_discovery(listings)
        result = self._fetch_via_browser(query)
        return await result if hasattr(result, "__await__") else result

    async def _fetch_via_browser(self, query: str | None = None) -> list[LiveListing]:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright

        url = self._search_url(query, self.search_filters)

        ensure_browser_dir()
        async with async_playwright() as p:
            browser = await p.chromium.launch_persistent_context(
                str(SESSION_DIR),
                headless=False,
                args=["--window-position=-10000,-10000"],
                viewport={"width": 1440, "height": 1100},
            )
            try:
                await restore_session_cookies_async(browser, STATE_PATH)
                page = await browser.new_page()
                await page.goto("https://buyee.jp/", wait_until="domcontentloaded", timeout=45000)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(8000)
                listing_script = """
                    () => Array.from(document.querySelectorAll('a[href]'))
                        .map(a => {
                            const card = a.closest('li');
                            const titleNode = card?.querySelector(
                                '.top-items__title, .top-items__name, [data-item-title], h2, h3'
                            );
                            return {
                            href: a.href,
                            text: (card?.innerText || a.textContent || '').replace(/\\s+/g, ' ').trim(),
                            title: (a.textContent || '').replace(/\\s+/g, ' ').trim() || titleNode?.textContent?.trim() || a.getAttribute('title') || a.getAttribute('aria-label') || '',
                            image_url: card?.querySelector('img')?.src || a.querySelector('img')?.src || '',
                            image_alt: card?.querySelector('img')?.alt || a.querySelector('img')?.alt || '',
                            data_title: a.getAttribute('data-title') || ''
                            };
                        })
                        .filter(x => x.href.includes('/item/jdirectitems/auction/'))
                        .slice(0, 500)
                    """
                all_rows: list[dict[str, str]] = []
                visited_pages: set[str] = set()
                for _ in range(self.max_pages):
                    if page.url in visited_pages:
                        break
                    visited_pages.add(page.url)
                    try:
                        data = await page.evaluate(listing_script)
                    except PlaywrightError as exc:
                        if "Execution context was destroyed" not in str(exc):
                            raise
                        await page.wait_for_load_state("domcontentloaded", timeout=45000)
                        await page.wait_for_timeout(3000)
                        data = await page.evaluate(listing_script)
                    all_rows.extend(data)
                    if not data:
                        await page.wait_for_timeout(4000)
                        data = await page.evaluate(listing_script)
                        all_rows.extend(data)
                    self.last_page_count += 1
                    next_url = await page.evaluate(
                        r"""
                        () => {
                          const links = [...document.querySelectorAll('a[href]')];
                          const explicit = links.find(a =>
                            a.rel === 'next' || /^(next|次へ|›|>)$/i.test(a.innerText.trim())
                          );
                          return explicit?.href || null;
                        }
                        """
                    )
                    if not next_url or next_url in visited_pages:
                        break
                    await page.goto(next_url, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(3000)
                if not all_rows:
                    body_text = normalize_text((await page.locator("body").inner_text())[:10000])
                    state = self._empty_page_state(body_text)
                    if state == "auth_expired":
                        raise AuthExpiredError("Buyee authenticated session is no longer active")
                    if state == "no_results":
                        return []
                    logging.getLogger("buyeeye.runtime").warning(
                        "parser_empty_page query=%r recovery=same_context_reload", query
                    )
                    await page.reload(wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(5000)
                    all_rows.extend(await page.evaluate(listing_script))
                    if all_rows:
                        logging.getLogger("buyeeye.runtime").info(
                            "parser_recovered query=%r recovery=same_context_reload", query
                        )
                    else:
                        body_text = normalize_text(
                            (await page.locator("body").inner_text())[:10000]
                        )
                        state = self._empty_page_state(body_text)
                        if state == "auth_expired":
                            raise AuthExpiredError(
                                "Buyee authenticated session is no longer active"
                            )
                        if state == "parser_anomaly":
                            raise ParserAnomalyError(
                                "search page contained no recognizable listing cards"
                            )
                return self._annotate_discovery(self._items_from_browser_payload(all_rows, query))
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
    def _is_primary_search_result(href: str, query: str | None) -> bool:
        """Exclude explicit Buyee recommendation/history modules from query results."""
        if not query:
            return True
        conversion_types = parse_qs(urlsplit(href).query).get("conversionType", [])
        return not conversion_types or all(
            value == "jdirectitems_auction_DirectSearch" for value in conversion_types
        )

    @staticmethod
    def _extract_card_title(row: dict[str, Any]) -> str:
        for key in (
            "title",
            "item_title",
            "listing_title",
            "name",
            "item_name",
            "title_original",
            "itemName",
            "data_title",
            "image_alt",
        ):
            value = row.get(key)
            if value:
                cleaned = normalize_text(str(value))
                if cleaned and not re.search(
                    r"(?i)Time Remaining|YEN|JPY|US\$|^With Authentication$", cleaned
                ):
                    return cleaned
        return ""

    @staticmethod
    def _extract_time_remaining(text: str) -> str | None:
        match = re.search(
            r"(?i)Time Remaining\s+((?:\d+\s*(?:day|hour|minute)(?:\(s\)|s)?\s*)+)",
            text,
        )
        if match:
            value = match.group(1).strip()
            if value:
                return value
        return None

    @staticmethod
    def _extract_buyout_price(text: str) -> int | None:
        match = re.search(
            r"(?i)(?:Buy\s*Now|Buyout(?:\s*Price)?|Buy\s*Out(?:\s*Price)?)"
            r"\s*(?:[:\-]?)\s*(?:¥|JPY|YEN)?\s*([0-9][0-9,]*)",
            text,
        )
        if match:
            price = int(match.group(1).replace(",", ""))
            return price if 0 < price <= 1_000_000 else None
        return None

    @staticmethod
    def _extract_bid_count(text: str) -> int | None:
        match = re.search(
            r"(?i)(?:([0-9]+)\s*(?:bids?|bid count)|"
            r"(?:number of bids|bid count)\s*[:\-]?\s*([0-9]+))",
            text,
        )
        if match:
            return int(match.group(1) or match.group(2))
        return None

    @staticmethod
    def _extract_watch_count(text: str) -> int | None:
        match = re.search(r"(?i)([0-9]+)\s*(?:watch(?:es)?|watch count)", text)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def _items_from_browser_payload(
        rows: list[dict[str, str]], query: str | None = None
    ) -> list[LiveListing]:
        listings: list[LiveListing] = []
        seen: set[str] = set()
        for row in rows:
            href = row.get("href", "")
            text = str(row.get("text") or "")
            if (
                not href
                or not text
                or not BuyeeAuthenticatedSource._is_primary_search_result(href, query)
            ):
                continue
            item_id = BuyeeAuthenticatedSource._item_id_from_href(href)
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            title = BuyeeAuthenticatedSource._extract_card_title(row)
            title_original = title
            if title_original and title_original.lower() in {"time remaining", ""}:
                title_original = ""
            time_remaining = BuyeeAuthenticatedSource._extract_time_remaining(text)
            price = BuyeeAuthenticatedSource._extract_price(text)
            buyout_price = BuyeeAuthenticatedSource._extract_buyout_price(text)
            raw = {
                "query": query or "buyee-auction",
                "time_remaining": time_remaining,
                "bid_count": BuyeeAuthenticatedSource._extract_bid_count(text),
                "watch_count": BuyeeAuthenticatedSource._extract_watch_count(text),
                "image_url": row.get("image_url") or row.get("img_src") or None,
                "buyout_indicator": "buyout" if buyout_price is not None else None,
            }
            listings.append(
                LiveListing(
                    source="buyee",
                    source_listing_id=item_id,
                    url=href,
                    title_original=title_original,
                    title_normalized=normalize_text(title_original),
                    brand=normalize_brand(
                        BuyeeAuthenticatedSource._extract_brand(title_original or text)
                    ),
                    category=BuyeeAuthenticatedSource._infer_category(title_original),
                    current_price=price,
                    buyout_price=buyout_price,
                    starting_price=price,
                    seller_name=None,
                    condition=None,
                    image_url=row.get("image_url") or row.get("img_src") or None,
                    time_remaining=time_remaining,
                    buyout_indicator=raw["buyout_indicator"],
                    bid_count=raw["bid_count"],
                    watch_count=raw["watch_count"],
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
            text = item.get_text(" ", strip=True)
            if (
                not href
                or not text
                or not BuyeeAuthenticatedSource._is_primary_search_result(href, query)
            ):
                continue
            item_id = BuyeeAuthenticatedSource._item_id_from_href(href)
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            time_remaining = BuyeeAuthenticatedSource._extract_time_remaining(text)
            price = BuyeeAuthenticatedSource._extract_price(text)
            title = BuyeeAuthenticatedSource._extract_card_title(
                {"text": text, "title": item.get("title")}
            )
            buyout_price = BuyeeAuthenticatedSource._extract_buyout_price(text)
            raw = {
                "query": query or "buyee-auction",
                "time_remaining": time_remaining,
                "bid_count": BuyeeAuthenticatedSource._extract_bid_count(text),
                "watch_count": BuyeeAuthenticatedSource._extract_watch_count(text),
                "buyout_indicator": "buyout" if buyout_price is not None else None,
            }
            listings.append(
                LiveListing(
                    source="buyee",
                    source_listing_id=item_id,
                    url=href if href.startswith("http") else f"https://buyee.jp{href}",
                    title_original=title,
                    title_normalized=normalize_text(title),
                    brand=normalize_brand(BuyeeAuthenticatedSource._extract_brand(title or text)),
                    category=BuyeeAuthenticatedSource._infer_category(title),
                    current_price=price,
                    buyout_price=buyout_price,
                    starting_price=price,
                    seller_name=None,
                    condition=None,
                    image_url=item.select_one("img") and item.select_one("img").get("src"),
                    time_remaining=time_remaining,
                    buyout_indicator=raw["buyout_indicator"],
                    bid_count=raw["bid_count"],
                    watch_count=raw["watch_count"],
                    raw=raw,
                )
            )
        return listings

    @staticmethod
    def _extract_brand(title: str) -> str:
        if re.search(r"(?i)number\s*\(?n\)?\s*ine|ナンバーナイン", title):
            return "Number (N)ine"
        for marker in [
            "Number (N)ine",
            "Chrome Hearts",
            "Needles",
            "Rick Owens",
            "Dior Homme",
            "Saint Laurent",
        ]:
            if marker.lower() in title.lower():
                return marker
        return "unknown"

    @staticmethod
    def _infer_category(title: str) -> str:
        lowered = title.lower()
        if any(
            term in lowered
            for term in (
                "ring",
                "necklace",
                "bracelet",
                "belt",
                "wallet",
                "keyholder",
                "リング",
                "ネックレス",
                "ブレスレット",
                "ベルト",
                "財布",
                "キーホルダ",
            )
        ):
            return "accessory"
        if any(
            term in lowered
            for term in (
                "shirt",
                "tee",
                "jacket",
                "coat",
                "knit",
                "cardigan",
                "hoodie",
                "pants",
                "denim",
                "jeans",
                "boot",
                "シャツ",
                "ジャケット",
                "コート",
                "ニット",
                "カーディガン",
                "パーカー",
                "パンツ",
                "デニム",
                "ジーンズ",
                "ブーツ",
                "セットアップ",
            )
        ):
            return "menswear"
        return "unknown"

    @staticmethod
    def _extract_price(text: str) -> int | None:
        match = re.search(
            r"(?:¥|JPY|YEN)\s*([0-9][0-9,]*)|([0-9][0-9,]*)\s*(?:YEN|JPY|¥)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        value = match.group(1) or match.group(2)
        if value is None:
            return None
        price = int(value.replace(",", ""))
        if price <= 0 or price > 1_000_000:
            return None
        return price


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
        for marker in [
            "Number (N)ine",
            "Chrome Hearts",
            "Needles",
            "Rick Owens",
            "Dior Homme",
            "Saint Laurent",
        ]:
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
        for marker in [
            "Number (N)ine",
            "Chrome Hearts",
            "Needles",
            "Rick Owens",
            "Dior Homme",
            "Saint Laurent",
        ]:
            if marker.lower() in title.lower():
                return marker
        return "unknown"


class LiveMarketLoop:
    def __init__(self, sources: list[ListingSource] | None = None) -> None:
        self.sources = sources or [BuyeeAuthenticatedSource(), YahooAuctionSource()]

    async def run_once(
        self, query: str, persist: bool = True, db_path: str | Path = DB_PATH
    ) -> list[LiveListing]:
        combined: list[LiveListing] = []
        for source in self.sources:
            try:
                items = await source.fetch(query)
                combined.extend(items)
            except (httpx.HTTPError, ValueError):
                continue
        if persist:
            save_listings(combined, db_path)
        return combined
