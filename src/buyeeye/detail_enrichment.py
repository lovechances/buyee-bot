from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Field, SQLModel

from .browser_session import (
    SESSION_DIR,
    STATE_PATH,
    ensure_browser_dir,
    restore_session_cookies_async,
)
from .db import DB_PATH, get_session, init_db
from .normalize import normalize_text


@dataclass
class DetailObservation:
    source: str
    source_listing_id: str
    observed_at: str
    title_original: str | None = None
    description_original: str | None = None
    category_hierarchy: list[str] = field(default_factory=list)
    brand_observed: str | None = None
    item_type_observed: str | None = None
    condition_original: str | None = None
    size_original: str | None = None
    measurements_original: str | None = None
    material_original: str | None = None
    seller_name: str | None = None
    seller_feedback_percent: float | None = None
    seller_feedback_count: int | None = None
    current_price: int | None = None
    buyout_price: int | None = None
    starting_price: int | None = None
    bid_count: int | None = None
    watch_count: int | None = None
    auction_open_time: str | None = None
    auction_end_time: str | None = None
    image_urls: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class DetailCache(SQLModel, table=True):
    key: str = Field(primary_key=True)
    source: str
    source_listing_id: str
    retrieved_at: datetime
    expires_at: datetime
    content_hash: str
    observation_json: str


class DetailRepository:
    def __init__(self, db_path: str | Path = DB_PATH, ttl_hours: int = 24):
        self.db_path = Path(db_path)
        self.ttl = timedelta(hours=ttl_hours)

    @staticmethod
    def key(source: str, source_listing_id: str) -> str:
        return hashlib.sha256(f"{source}\0{source_listing_id}".encode()).hexdigest()

    def get_fresh(self, source: str, source_listing_id: str) -> DetailObservation | None:
        init_db(self.db_path)
        with get_session(self.db_path) as session:
            row = session.get(DetailCache, self.key(source, source_listing_id))
            if row is None:
                return None
            expires = row.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires <= datetime.now(UTC):
                return None
            stored = json.loads(row.observation_json)
            if stored.get("raw"):
                refreshed = parse_detail_payload(source, source_listing_id, stored["raw"])
                refreshed.observed_at = stored["observed_at"]
                return refreshed
            return DetailObservation(**stored)

    def put(self, observation: DetailObservation) -> None:
        init_db(self.db_path)
        now = datetime.now(UTC)
        payload = json.dumps(asdict(observation), ensure_ascii=False, sort_keys=True)
        key = self.key(observation.source, observation.source_listing_id)
        with get_session(self.db_path) as session:
            row = session.get(DetailCache, key)
            values = {
                "retrieved_at": now,
                "expires_at": now + self.ttl,
                "content_hash": hashlib.sha256(payload.encode()).hexdigest(),
                "observation_json": payload,
            }
            if row is None:
                session.add(
                    DetailCache(
                        key=key,
                        source=observation.source,
                        source_listing_id=observation.source_listing_id,
                        **values,
                    )
                )
            else:
                for field_name, value in values.items():
                    setattr(row, field_name, value)
            session.commit()


def _label_value(labels: dict[str, str], *patterns: str) -> str | None:
    for label, value in labels.items():
        if any(re.search(pattern, label, re.IGNORECASE) for pattern in patterns) and value:
            return normalize_text(value)
    return None


def _jpy(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(?:¥|JPY|YEN|円)?\s*([0-9][0-9,]*)", value, re.IGNORECASE)
    if not match:
        return None
    price = int(match.group(1).replace(",", ""))
    return price if 0 < price <= 1_000_000 else None


def _visible_value(visible_text: str, label: str) -> str | None:
    lines = [normalize_text(line) for line in visible_text.splitlines() if normalize_text(line)]
    for index, line in enumerate(lines[:-1]):
        if line.casefold() == label.casefold():
            return lines[index + 1]
    return None


def _visible_int(visible_text: str, label: str) -> int | None:
    value = _visible_value(visible_text, label)
    if not value:
        return None
    match = re.search(r"[0-9][0-9,]*", value)
    return int(match.group().replace(",", "")) if match else None


def parse_detail_payload(
    source: str, source_listing_id: str, payload: dict[str, Any]
) -> DetailObservation:
    labels = {
        normalize_text(str(row[0])): normalize_text(str(row[1]))
        for row in payload.get("labels", [])
        if isinstance(row, list) and len(row) >= 2 and row[0] and row[1]
    }
    visible_text = str(payload.get("visible_text") or "")
    category_hierarchy: list[str] = []
    for line in visible_text.splitlines():
        normalized = normalize_text(line)
        if normalized.startswith("Auction ＞"):
            category_hierarchy = [normalize_text(part) for part in normalized.split("＞")]
            break
    if not category_hierarchy:
        category_hierarchy = [
            normalize_text(str(part)) for part in payload.get("breadcrumbs", []) if part
        ]
    title_candidates = [payload.get("heading"), payload.get("og_title"), payload.get("document_title")]
    title = next(
        (
            normalize_text(str(value)).removesuffix(" | Buyee")
            for value in title_candidates
            if value and not re.search(r"(?i)^Buyee|With Authentication", str(value))
        ),
        None,
    )
    description = payload.get("description") or payload.get("og_description")
    feedback_text = _label_value(labels, r"feedback", r"評価")
    feedback_text = feedback_text or _visible_value(visible_text, "Percentage of good ratings")
    feedback_percent = None
    feedback_count = None
    if feedback_text:
        percent = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", feedback_text)
        count = re.search(
            r"(?:\(|^)([0-9][0-9,]*)\s*(?:ratings?|件|\))",
            feedback_text,
            re.IGNORECASE,
        )
        feedback_percent = float(percent.group(1)) if percent else None
        feedback_count = int(count.group(1).replace(",", "")) if count else None
    good_count = _visible_int(visible_text, "Good")
    bad_count = _visible_int(visible_text, "Bad")
    if feedback_count is None and good_count is not None and bad_count is not None:
        feedback_count = good_count + bad_count
    images = list(
        dict.fromkeys(
            str(url)
            for url in payload.get("images", [])
            if "images.auctions.yahoo.co.jp/image/" in str(url)
        )
    )
    hierarchy_without_size = [
        part for part in category_hierarchy if not re.match(r"(?i)^size\b", part)
    ]
    return DetailObservation(
        source=source,
        source_listing_id=source_listing_id,
        observed_at=datetime.now(UTC).isoformat(),
        title_original=title,
        description_original=normalize_text(str(description)) if description else None,
        category_hierarchy=category_hierarchy,
        brand_observed=_label_value(labels, r"^brand$", r"ブランド"),
        item_type_observed=_label_value(labels, r"item type", r"商品カテゴリ", r"カテゴリ")
        or (hierarchy_without_size[-1] if hierarchy_without_size else None),
        condition_original=_label_value(labels, r"condition", r"状態")
        or _visible_value(visible_text, "Item Condition"),
        size_original=_label_value(labels, r"^size$", r"サイズ")
        or next(
            (part for part in reversed(category_hierarchy) if re.match(r"(?i)^size\b", part)),
            None,
        ),
        measurements_original=_label_value(labels, r"measurement", r"実寸", r"寸法"),
        material_original=_label_value(labels, r"material", r"素材"),
        seller_name=_label_value(labels, r"seller", r"出品者")
        or _visible_value(visible_text, "Seller"),
        seller_feedback_percent=feedback_percent,
        seller_feedback_count=feedback_count,
        current_price=_jpy(_label_value(labels, r"current price", r"現在価格")),
        buyout_price=_jpy(_label_value(labels, r"buyout", r"buy it now", r"即決")),
        starting_price=_jpy(_visible_value(visible_text, "Starting Price")),
        bid_count=_visible_int(visible_text, "Number of Bids"),
        watch_count=_visible_int(visible_text, "Watch"),
        auction_open_time=_visible_value(visible_text, "Opening Time (JST)"),
        auction_end_time=_label_value(labels, r"end time", r"終了日時")
        or _visible_value(visible_text, "Closing Time (JST)"),
        image_urls=images,
        raw=payload,
    )


def apply_detail(
    listing: Any,
    observation: DetailObservation,
    *,
    retrieval: str = "live_fetch",
) -> None:
    if retrieval not in {"cache_hit", "live_fetch"}:
        raise ValueError("Detail retrieval must be cache_hit or live_fetch")
    observed = asdict(observation)
    listing.raw = {
        **(listing.raw or {}),
        "stage_b": {
            "status": "enriched",
            "retrieval": retrieval,
            "cache_hit": retrieval == "cache_hit",
            "live_fetch": retrieval == "live_fetch",
            "observed": observed,
        },
    }
    if observation.title_original:
        listing.title_original = observation.title_original
        listing.title_normalized = normalize_text(observation.title_original)
    if observation.brand_observed:
        listing.brand = observation.brand_observed
    if observation.condition_original:
        listing.condition = observation.condition_original
    if observation.seller_name:
        listing.seller_name = observation.seller_name
    # A cached identity observation can be up to the configured Stage B TTL old.
    # Never let it replace fresher auction state from the current search card.
    if retrieval == "live_fetch":
        if observation.current_price:
            listing.current_price = observation.current_price
        if observation.buyout_price:
            listing.buyout_price = observation.buyout_price
            listing.buyout_indicator = "buyout"
        if observation.starting_price:
            listing.starting_price = observation.starting_price
        if observation.bid_count is not None:
            listing.bid_count = observation.bid_count
        if observation.watch_count is not None:
            listing.watch_count = observation.watch_count
    if observation.image_urls:
        listing.image_url = observation.image_urls[0]
    if observation.seller_feedback_percent is not None:
        listing.raw["seller_feedback_percent"] = observation.seller_feedback_percent
    if observation.seller_feedback_count is not None:
        listing.raw["seller_feedback_count"] = observation.seller_feedback_count
    listing.raw["detail_photo_count"] = len(observation.image_urls)


class BuyeeDetailService:
    def __init__(
        self,
        repository: DetailRepository,
        *,
        force_open_auction_refresh_minutes: int = 60,
    ):
        self.repository = repository
        self.force_open_auction_refresh_minutes = max(
            0, int(force_open_auction_refresh_minutes)
        )

    def _force_live_refresh(self, listing: Any) -> bool:
        buyout_price = getattr(listing, "buyout_price", None)
        current_price = getattr(listing, "current_price", None)
        has_live_bid = bool(
            buyout_price is None
            or (current_price is not None and current_price < buyout_price)
        )
        if not has_live_bid:
            return False
        text = normalize_text(str(getattr(listing, "time_remaining", None) or ""))
        matches = re.findall(
            r"(\d+)\s*(day|hour|minute)s?(?:\(s\))?", text.casefold()
        )
        if not matches:
            return False
        remaining = sum(
            int(amount) * {"day": 1440, "hour": 60, "minute": 1}[unit]
            for amount, unit in matches
        )
        return remaining <= self.force_open_auction_refresh_minutes

    def _cached_observation(self, listing: Any) -> DetailObservation | None:
        if self._force_live_refresh(listing):
            return None
        return self.repository.get_fresh(listing.source, listing.source_listing_id)

    async def enrich(self, listings: list[Any]) -> tuple[int, int]:
        pending: list[Any] = []
        enriched = 0
        for listing in listings:
            cached = self._cached_observation(listing)
            if cached is not None:
                apply_detail(listing, cached, retrieval="cache_hit")
                enriched += 1
            else:
                pending.append(listing)
        if not pending:
            return enriched, 0

        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright

        ensure_browser_dir()
        failed = 0
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                str(SESSION_DIR),
                headless=False,
                args=["--window-position=-10000,-10000"],
                viewport={"width": 1440, "height": 1100},
            )
            try:
                await restore_session_cookies_async(context, STATE_PATH)
                page = await context.new_page()
                for listing in pending:
                    try:
                        await page.goto(listing.url, wait_until="domcontentloaded", timeout=60000)
                        payload = None
                        for attempt in range(3):
                            await page.wait_for_timeout(2000)
                            try:
                                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                                payload = await page.evaluate(
                                    r"""
                            () => {
                              const text = s => document.querySelector(s)?.textContent?.replace(/\s+/g, ' ').trim() || '';
                              const meta = p => document.querySelector(`meta[property="${p}"],meta[name="${p}"]`)?.content || '';
                              const labels = [...document.querySelectorAll('tr,dl')].flatMap(node => {
                                if (node.tagName === 'TR') {
                                  const cells = [...node.querySelectorAll('th,td')].map(x => x.innerText.trim());
                                  return cells.length >= 2 ? [[cells[0], cells.slice(1).join(' ')]] : [];
                                }
                                return [...node.querySelectorAll('dt')].map(dt => [dt.innerText.trim(), dt.nextElementSibling?.innerText?.trim() || '']);
                              });
                              return {
                                heading: text('.itemInformation__itemName, .itemInformation__title, h1'),
                                document_title: document.title,
                                og_title: meta('og:title'),
                                description: text('.itemInformation__description, .item-description, [data-item-description]'),
                                og_description: meta('og:description') || meta('description'),
                                breadcrumbs: [...document.querySelectorAll('nav a, .breadcrumb a')].map(x => x.innerText.trim()).filter(Boolean),
                                labels,
                                images: [...document.images].map(x => x.currentSrc || x.src).filter(x => /yimg|buyee|auctions/i.test(x)),
                                visible_text: document.body.innerText.slice(0, 30000)
                              };
                            }
                            """
                                )
                                break
                            except PlaywrightError as exc:
                                if "Execution context was destroyed" not in str(exc) or attempt == 2:
                                    raise
                        if payload is None:
                            raise ValueError("detail payload unavailable")
                        observation = parse_detail_payload(
                            listing.source, listing.source_listing_id, payload
                        )
                        if not observation.title_original:
                            raise ValueError("detail title unavailable")
                        self.repository.put(observation)
                        apply_detail(listing, observation, retrieval="live_fetch")
                        enriched += 1
                    except (OSError, PlaywrightError, TimeoutError, ValueError) as exc:
                        listing.raw = {
                            **(listing.raw or {}),
                            "stage_b": {"status": "failed", "error_type": type(exc).__name__},
                        }
                        failed += 1
                await page.close()
            finally:
                await context.close()
        return enriched, failed
