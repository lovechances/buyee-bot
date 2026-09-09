from __future__ import annotations

import json
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


_PAGINATION_PAGE_PATTERN = re.compile(
    r'''["']?page["']?\s*:\s*["']?(\d+)["']?''',
    flags=re.IGNORECASE,
)

_PRIMARY_SEARCH_CARD_SELECTOR = ".g-main > ul.auctionSearchResult > li.itemCard"


def _bound_page_number(data_bind: str | None) -> int | None:
    """Read Buyee's Knockout pagination target without guessing a URL parameter."""
    match = _PAGINATION_PAGE_PATTERN.search(str(data_bind or ""))
    return int(match.group(1)) if match else None


def _next_page_number_from_pagination_html(
    pagination_html: str, current_page: int
) -> int | None:
    """Return only the verified next sequential page exposed by Buyee's paginator."""
    soup = BeautifulSoup(pagination_html or "", "lxml")
    expected = current_page + 1
    for anchor in soup.select(".page_navi a[data-bind]"):
        if _bound_page_number(anchor.get("data-bind")) == expected:
            return expected
    return None


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
        # Search-page parser recovery is intentionally distinct from daemon-level
        # source retries/browser restarts.  The pipeline snapshots these counters
        # into each ScanEvent after a successful acquisition.
        self.parser_reload_attempts = 0
        self.parser_reload_successes = 0
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
                primary_card_selector = json.dumps(_PRIMARY_SEARCH_CARD_SELECTOR)
                listing_script = (
                    """
                    () => {
                      const cards = Array.from(document.querySelectorAll(
                        __PRIMARY_SEARCH_CARD_SELECTOR__
                      ));
                      return cards.map(card => {
                        const links = Array.from(card.querySelectorAll(
                          'a[href*="/item/jdirectitems/auction/"]'
                        ));
                        const titleLink = card.querySelector(
                          '.itemCard__itemName a[href*="/item/jdirectitems/auction/"]'
                        );
                        const link = titleLink || links[0];
                        const titleNode = card.querySelector(
                          '.itemCard__itemName, .top-items__title, .top-items__name, '
                          + '[data-item-title], h2, h3'
                        );
                        const image = card.querySelector('img');
                        return {
                          href: link?.href || '',
                          text: (card.innerText || '').replace(/\\s+/g, ' ').trim(),
                          item_title: (titleNode?.textContent || '').replace(/\\s+/g, ' ').trim(),
                          title: (titleLink?.textContent || link?.getAttribute('title')
                            || link?.getAttribute('aria-label') || '')
                            .replace(/\\s+/g, ' ').trim(),
                          image_url: image?.currentSrc || image?.src || '',
                          image_alt: image?.alt || '',
                          data_title: titleLink?.getAttribute('data-title')
                            || link?.getAttribute('data-title') || ''
                        };
                      }).filter(x => x.href).slice(0, 500);
                    }
                    """
                ).replace("__PRIMARY_SEARCH_CARD_SELECTOR__", primary_card_selector)
                card_signature_script = (
                    """
                    () => [...new Set(Array.from(document.querySelectorAll(
                      __PRIMARY_SEARCH_CARD_SELECTOR__
                    )).flatMap(card => Array.from(card.querySelectorAll(
                      'a[href*="/item/jdirectitems/auction/"]'
                    )).map(a => a.href)))].slice(0, 30).join('|')
                    """
                ).replace("__PRIMARY_SEARCH_CARD_SELECTOR__", primary_card_selector)
                all_rows: list[dict[str, Any]] = []
                current_page = 1
                seen_page_signatures: set[str] = set()
                for page_index in range(self.max_pages):
                    try:
                        data = await page.evaluate(listing_script)
                    except PlaywrightError as exc:
                        if "Execution context was destroyed" not in str(exc):
                            raise
                        await page.wait_for_load_state("domcontentloaded", timeout=45000)
                        await page.wait_for_timeout(3000)
                        data = await page.evaluate(listing_script)
                    if not data:
                        await page.wait_for_timeout(4000)
                        data = await page.evaluate(listing_script)
                    page_signature = "|".join(
                        self._item_id_from_href(str(row.get("href") or "")) for row in data
                    )
                    if page_signature and page_signature in seen_page_signatures:
                        break
                    if page_signature:
                        seen_page_signatures.add(page_signature)
                    for rank, row in enumerate(data, start=1):
                        row["search_page"] = current_page
                        row["search_rank_on_page"] = rank
                    all_rows.extend(data)
                    self.last_page_count += 1
                    # The budget counts pages fetched, not pages parsed.  Do
                    # not navigate to an extra page after collecting the final
                    # allowed page: that would add source load while silently
                    # discarding every card from the navigation.
                    if page_index + 1 >= self.max_pages:
                        break
                    paginator = page.locator("nav.search_page_navi").first
                    if await paginator.count() == 0:
                        break
                    paginator_html = await paginator.evaluate("node => node.outerHTML")
                    next_page = _next_page_number_from_pagination_html(
                        paginator_html, current_page
                    )
                    if next_page is None:
                        break
                    links = page.locator("nav.search_page_navi .page_navi a[data-bind]")
                    next_link = None
                    for index in range(await links.count()):
                        candidate = links.nth(index)
                        if (
                            _bound_page_number(await candidate.get_attribute("data-bind"))
                            == next_page
                        ):
                            next_link = candidate
                            break
                    if next_link is None:
                        break
                    before_signature = await page.evaluate(card_signature_script)
                    try:
                        await next_link.click(timeout=15000)
                        page_advance_script = (
                            """
                            ({ expected, before }) => {
                              const current = document.querySelector(
                                'nav.search_page_navi .page_navi .current'
                              )?.textContent?.trim();
                              const signature = [...new Set(Array.from(document.querySelectorAll(
                                __PRIMARY_SEARCH_CARD_SELECTOR__
                              )).flatMap(card => Array.from(card.querySelectorAll(
                                'a[href*="/item/jdirectitems/auction/"]'
                              )).map(a => a.href)))].slice(0, 30).join('|');
                              return current === String(expected)
                                && Boolean(signature)
                                && signature !== before;
                            }
                            """
                        ).replace("__PRIMARY_SEARCH_CARD_SELECTOR__", primary_card_selector)
                        await page.wait_for_function(
                            page_advance_script,
                            arg={"expected": next_page, "before": before_signature},
                            timeout=30000,
                        )
                    except PlaywrightError as exc:
                        logging.getLogger("buyeeye.runtime").warning(
                            "pagination_stopped query=%r page=%s error=%s",
                            query,
                            next_page,
                            type(exc).__name__,
                        )
                        break
                    current_page = next_page
                    await page.wait_for_timeout(1000)
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
                    self.parser_reload_attempts += 1
                    await page.reload(wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(5000)
                    recovered_rows = await page.evaluate(listing_script)
                    for rank, row in enumerate(recovered_rows, start=1):
                        row["search_page"] = current_page
                        row["search_rank_on_page"] = rank
                    all_rows.extend(recovered_rows)
                    if all_rows:
                        self.parser_reload_successes += 1
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
                parsed = self._items_from_browser_payload(all_rows, query)
                if all_rows and not parsed:
                    raise ParserAnomalyError(
                        "search cards were present but contained no usable listing titles"
                    )
                return self._annotate_discovery(parsed)
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
            "item_title",
            "listing_title",
            "name",
            "item_name",
            "title_original",
            "itemName",
            "data_title",
            "image_alt",
            "title",
        ):
            value = row.get(key)
            if value:
                cleaned = normalize_text(str(value))
                if cleaned and not re.search(
                    r"(?i)Time Remaining|YEN|JPY|US\$|^With Authentication$|"
                    r"^(?:With Authentication\s+)?\d+%\s*OFF$",
                    cleaned,
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
        rows: list[dict[str, Any]], query: str | None = None
    ) -> list[LiveListing]:
        listings: list[LiveListing] = []
        seen: set[str] = set()
        accepted_by_page: dict[int, int] = {}
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
            title = BuyeeAuthenticatedSource._extract_card_title(row)
            title_original = title
            if title_original and title_original.lower() in {"time remaining", ""}:
                title_original = ""
            if not title_original:
                continue
            seen.add(item_id)
            time_remaining = BuyeeAuthenticatedSource._extract_time_remaining(text)
            price = BuyeeAuthenticatedSource._extract_price(text)
            buyout_price = BuyeeAuthenticatedSource._extract_buyout_price(text)
            raw_page = row.get("search_page")
            search_page = raw_page if isinstance(raw_page, int) and raw_page > 0 else None
            if search_page is not None:
                accepted_by_page[search_page] = accepted_by_page.get(search_page, 0) + 1
            raw_rank = row.get("search_rank_on_page")
            rank_on_page = (
                raw_rank
                if isinstance(raw_rank, int) and raw_rank > 0
                else accepted_by_page.get(search_page)
                if search_page is not None
                else None
            )
            raw = {
                "query": query or "buyee-auction",
                "time_remaining": time_remaining,
                "bid_count": BuyeeAuthenticatedSource._extract_bid_count(text),
                "watch_count": BuyeeAuthenticatedSource._extract_watch_count(text),
                "image_url": row.get("image_url") or row.get("img_src") or None,
                "buyout_indicator": "buyout" if buyout_price is not None else None,
                "search_page": search_page,
                "search_rank_on_page": rank_on_page,
                "search_rank": len(listings) + 1,
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
        rows: list[dict[str, Any]] = []
        for card in soup.select(_PRIMARY_SEARCH_CARD_SELECTOR):
            links = card.select("a[href*='/item/jdirectitems/auction/']")
            title_link = card.select_one(
                ".itemCard__itemName a[href*='/item/jdirectitems/auction/']"
            )
            link = title_link or (links[0] if links else None)
            if link is None:
                continue
            title_node = card.select_one(
                ".itemCard__itemName, .top-items__title, .top-items__name, "
                "[data-item-title], h2, h3"
            )
            image = card.select_one("img")
            href = str(link.get("href") or "")
            rows.append(
                {
                    "href": href if href.startswith("http") else f"https://buyee.jp{href}",
                    "text": card.get_text(" ", strip=True),
                    "item_title": title_node.get_text(" ", strip=True) if title_node else "",
                    "title": (
                        title_link.get_text(" ", strip=True)
                        if title_link
                        else link.get("title") or link.get_text(" ", strip=True)
                    ),
                    "image_url": image.get("src") if image else "",
                    "image_alt": image.get("alt") if image else "",
                    "data_title": link.get("data-title") or "",
                    "search_page": 1,
                    "search_rank_on_page": len(rows) + 1,
                }
            )
            if len(rows) >= 500:
                break
        return BuyeeAuthenticatedSource._items_from_browser_payload(rows, query)

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
