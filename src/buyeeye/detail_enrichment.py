from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlmodel import Field, SQLModel

from .browser_session import (
    SESSION_DIR,
    STATE_PATH,
    ensure_browser_dir,
    restore_session_cookies_async,
)
from .db import DB_PATH, get_session, init_db
from .live_sources import buyee_item_id, clean_marketplace_title, image_url_from_attributes
from .normalize import normalize_text


def detail_payload_script() -> str:
    return r"""
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
        association_version: 1,
        page_url: location.href,
        canonical_url: document.querySelector('link[rel="canonical"]')?.href || '',
        og_url: meta('og:url'),
        heading: text('.itemInformation__itemName, .itemInformation__title, h1'),
        document_title: document.title,
        og_title: meta('og:title'),
        description: text('.itemInformation__description, .item-description, [data-item-description]'),
        og_description: meta('og:description') || meta('description'),
        breadcrumbs: [...document.querySelectorAll('nav a, .breadcrumb a')].map(x => x.innerText.trim()).filter(Boolean),
        labels,
        primary_images: meta('og:image') ? [{
          provenance: 'og_image', attributes: {src: meta('og:image')}
        }] : [],
        visible_text: document.body.innerText.slice(0, 30000)
      };
    }
    """


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
            try:
                stored = json.loads(row.observation_json)
            except (TypeError, ValueError):
                return None
            if not isinstance(stored, dict):
                return None
            if (row.source, row.source_listing_id) != (source, source_listing_id) or (
                stored.get("source"), stored.get("source_listing_id")
            ) != (source, source_listing_id):
                return None
            # Legacy whole-document image captures have no observed page identity
            # or gallery provenance. Refresh only selected Stage B candidates;
            # never relabel a legacy/wrong-page payload with the requested ID.
            try:
                raw = stored.get("raw") or {}
                if not isinstance(raw, dict):
                    return None
                validate_detail_identity(source, source_listing_id, raw)
                refreshed = parse_detail_payload(source, source_listing_id, raw)
            except (TypeError, ValueError):
                return None
            refreshed.observed_at = stored["observed_at"]
            return refreshed

    def put(self, observation: DetailObservation) -> None:
        validate_detail_identity(observation.source, observation.source_listing_id, observation.raw)
        if observation.image_urls != _bound_detail_images(observation.raw):
            raise DetailIdentityError("detail cache image provenance mismatch")
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


def _primary_visible_brand(payload: dict[str, Any]) -> str | None:
    """Read Buyee's product metadata, never a brand in seller/recommendation copy.

    Production's Brand row is not a table/dl. Bind the fallback to the observed
    item page and its metadata sequence before Item Explanation. A missing
    value, multiple Brand rows, or an unfamiliar layout remains unknown.
    """
    if payload.get("association_version") != 1 or not payload.get("page_url"):
        return None
    text = str(payload.get("visible_text") or "")
    text = re.split(r"(?im)^\s*(?:Item Explanation|Recently viewed items|Recommended items)\s*$", text)[0]
    lines = [normalize_text(line) for line in text.splitlines() if normalize_text(line)]
    positions = [i for i, line in enumerate(lines) if line == "Brand"]
    if len(positions) != 1:
        return None
    i = positions[0]
    if i + 2 >= len(lines) or lines[i + 2] != "Item Condition":
        return None
    value = lines[i + 1]
    if value in {"-", "ー", "Unknown", "Not specified"} or len(value) > 100:
        return None
    return value


class DetailIdentityError(ValueError):
    """The requested listing and the observed page do not agree."""


def validate_detail_identity(source: str, listing_id: str, payload: dict[str, Any]) -> None:
    if source != "buyee" or not payload.get("page_url"):
        raise DetailIdentityError("detail page identity unavailable")
    for field_name in ("page_url", "canonical_url", "og_url"):
        value = payload.get(field_name)
        if value and buyee_item_id(str(value)) != listing_id:
            raise DetailIdentityError("detail page identity mismatch")
    auction_id = _visible_value(str(payload.get("visible_text") or ""), "Auction ID")
    if auction_id and auction_id.strip() != listing_id:
        raise DetailIdentityError("detail auction identity mismatch")


def _bound_detail_images(payload: dict[str, Any]) -> list[str]:
    # Only images explicitly associated with this product are eligible. The old
    # document.images array includes browsing-history/recommendation products.
    records = payload.get("primary_images") or []
    urls = []
    for record in records:
        if not isinstance(record, dict) or record.get("provenance") not in {
            "primary_gallery", "og_image",
        }:
            continue
        url = image_url_from_attributes(record.get("attributes") or {})
        if url and record.get("provenance") == "og_image":
            parsed = urlsplit(url)
            if parsed.hostname not in {
                "images.auctions.yahoo.co.jp", "auctions.c.yimg.jp", "pctr.c.yimg.jp",
                "cdnyauction.buyee.jp", "cdnyauction-pctr.buyee.jp",
            } or not (
                parsed.hostname == "images.auctions.yahoo.co.jp" and parsed.path.startswith("/image/")
                or "images.auctions.yahoo.co.jp/image/" in parsed.path
            ):
                # A generic site's social-sharing image is not a product photo.
                continue
        if url:
            urls.append(url)
    return list(dict.fromkeys(urls))


def parse_detail_payload(
    source: str, source_listing_id: str, payload: dict[str, Any]
) -> DetailObservation:
    if payload.get("page_url") or payload.get("association_version"):
        validate_detail_identity(source, source_listing_id, payload)
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
            clean_marketplace_title(str(value))
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
    images = _bound_detail_images(payload) if payload.get("page_url") else []
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
        brand_observed=_label_value(labels, r"^brand$", r"ブランド")
        or _primary_visible_brand(payload),
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
    if (listing.source, listing.source_listing_id) != (
        observation.source, observation.source_listing_id,
    ):
        raise DetailIdentityError("detail observation identity mismatch")
    if observation.raw.get("page_url") or observation.raw.get("association_version"):
        validate_detail_identity(listing.source, listing.source_listing_id, observation.raw)
    if observation.image_urls:
        validate_detail_identity(listing.source, listing.source_listing_id, observation.raw)
        if observation.image_urls != _bound_detail_images(observation.raw):
            raise DetailIdentityError("detail image provenance unavailable")
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
    # On cache hits a currently observed, item-bound card thumbnail is fresher.
    # Live gallery observations may replace it; unscoped legacy arrays may not.
    card_image_fresh = (
        retrieval == "cache_hit"
        and (listing.raw.get("image_integrity") or {}).get("status") == "card_bound"
        and listing.image_url
    )
    if observation.image_urls and not card_image_fresh:
        listing.image_url = observation.image_urls[0]
        listing.raw["image_integrity"] = {
            "status": "detail_bound", "source": listing.source,
            "source_listing_id": listing.source_listing_id,
            "observed_at": observation.observed_at, "clear_persisted": False,
        }
    if observation.seller_feedback_percent is not None:
        listing.raw["seller_feedback_percent"] = observation.seller_feedback_percent
    if observation.seller_feedback_count is not None:
        listing.raw["seller_feedback_count"] = observation.seller_feedback_count
    listing.raw["detail_photo_count"] = len(observation.image_urls)
    # This counts verified extracted images, not the marketplace's full gallery.
    # A social/product image proves inspectability, not a one-photo-only offer.
    primary_only = bool(observation.image_urls) and all(
        record.get("provenance") == "og_image"
        for record in observation.raw.get("primary_images", [])
        if isinstance(record, dict)
    )
    listing.raw["detail_photo_coverage"] = (
        "primary_only" if primary_only
        else "gallery_observed" if observation.image_urls
        else "unavailable"
    )


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
            try:
                cached = self._cached_observation(listing)
                if cached is not None:
                    apply_detail(listing, cached, retrieval="cache_hit")
                    enriched += 1
                    continue
            except (OSError, TypeError, ValueError):
                # Malformed or cross-item cache data is not a successful detail
                # observation. Try this candidate through the same bounded path.
                pass
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
                        if listing.source != "buyee" or buyee_item_id(listing.url) != listing.source_listing_id:
                            raise DetailIdentityError("requested detail URL identity mismatch")
                        await page.goto(listing.url, wait_until="domcontentloaded", timeout=60000)
                        payload = None
                        for attempt in range(3):
                            await page.wait_for_timeout(2000)
                            try:
                                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                                payload = await page.evaluate(detail_payload_script())
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
                            "stage_b": {
                                "status": "failed", "error_type": type(exc).__name__,
                                "identity_conflict": isinstance(exc, DetailIdentityError),
                            },
                        }
                        failed += 1
                await page.close()
            finally:
                await context.close()
        return enriched, failed
