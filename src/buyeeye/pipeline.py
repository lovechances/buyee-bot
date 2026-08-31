from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .browser_session import has_valid_session
from .db import DB_PATH, get_alert_state, save_alert_state, save_listings
from .discord_alerts import send_listing_alert
from .live_sources import BuyeeAuthenticatedSource, LiveListing, LiveMarketLoop
from .scoring import score_listing
from .settings import load_settings


@dataclass
class ScanResult:
    scanned: int = 0
    new_rows: int = 0
    updated_rows: int = 0
    new_alerts: int = 0
    suppressed_alerts: int = 0
    failed_alerts: int = 0
    rejected_irrelevant: int = 0
    rejected_invalid: int = 0
    below_threshold: int = 0
    route_counts: dict[str, int] | None = None


def _minutes_remaining(value: str | None) -> int | None:
    if not value:
        return None
    matches = re.findall(r"(\d+)\s*(day|hour|minute)s?", value.lower())
    if not matches:
        return None
    return sum(
        int(amount) * {"day": 1440, "hour": 60, "minute": 1}[unit] for amount, unit in matches
    )


def _is_relevant(listing: LiveListing) -> bool:
    text = f"{listing.title_original} {listing.brand}".lower()
    if not listing.title_original:
        return False
    if any(
        term in text for term in ("toy", "figure", "plush", "game", "camera", "phone", "poster")
    ):
        return False
    return any(
        brand in text
        for brand in (
            "number (n)ine",
            "number nine",
            "ナンバーナイン",
            "本人期",
            "needles",
            "リビルド",
            "chrome hearts",
            "クロムハーツ",
            "dior",
            "rick owens",
            "saint laurent",
        )
    )


def _price_is_valid(listing: LiveListing) -> bool:
    prices = [
        price
        for price in (listing.current_price, listing.buyout_price, listing.starting_price)
        if price is not None
    ]
    return bool(prices) and all(0 < price <= 1_000_000 for price in prices)


def _route(
    listing: LiveListing,
    score: int,
    ending_threshold_minutes: int = 60,
) -> tuple[str, str] | None:
    text = f"{listing.title_original} {listing.brand}".lower()
    if listing.buyout_price is not None and score >= 78:
        return "buyouts", "buyout price and sufficient preliminary score"
    if score >= 82:
        return "grails", "high preliminary opportunity score"
    if score >= 70 and any(
        term in text for term in ("chrome hearts", "silver", "925", "jewelry", "ring", "necklace")
    ):
        return "silver", "silver-related brand or term"
    remaining = _minutes_remaining(listing.time_remaining)
    if remaining is not None and remaining <= ending_threshold_minutes and score >= 70:
        return "ending", "auction ends within the configured threshold"
    auth_risk = (listing.raw or {}).get("auth_risk")
    if auth_risk in {"medium", "high"}:
        return "review", f"explicit {auth_risk} authentication-risk signal"
    return None


def _signature(listing: LiveListing) -> str:
    values = {
        "title": listing.title_original,
        "price": listing.current_price,
        "buyout": listing.buyout_price,
        "starting": listing.starting_price,
        "time": listing.time_remaining,
        "image": listing.image_url,
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


async def scan_once(
    query: str, db_path: str | Path = DB_PATH, send_alerts: bool = True
) -> ScanResult:
    source = BuyeeAuthenticatedSource()
    loop = LiveMarketLoop(sources=[source])
    listings = await loop.run_once(query, persist=False)
    settings = load_settings()
    ending_threshold = int(settings.get("alert", {}).get("ending_threshold_minutes", 60))
    result = ScanResult(scanned=len(listings), route_counts={})
    before = {
        item.source_listing_id: get_alert_state(item.source, item.source_listing_id, db_path)
        for item in listings
    }
    result.new_rows, result.updated_rows = save_listings(listings, db_path)
    for listing in listings:
        if not _is_relevant(listing):
            result.rejected_irrelevant += 1
            continue
        if not _price_is_valid(listing):
            result.rejected_invalid += 1
            continue
        score = score_listing(
            {
                "title_original": listing.title_original,
                "brand": listing.brand,
                "current_price": listing.current_price,
            }
        )
        routed = _route(listing, score, ending_threshold)
        if routed is None:
            result.below_threshold += 1
            continue
        route, reason = routed
        result.route_counts[route] = result.route_counts.get(route, 0) + 1
        signature = _signature(listing)
        previous = before[listing.source_listing_id]
        if previous is not None and previous.signature == signature:
            result.suppressed_alerts += 1
            continue
        if not send_alerts:
            continue
        sent, _, _ = send_listing_alert(listing, route, reason, score)
        if sent:
            save_alert_state(listing.source, listing.source_listing_id, signature, route, db_path)
            result.new_alerts += 1
        else:
            result.failed_alerts += 1
    return result


def health_report(db_path: str | Path = DB_PATH) -> dict[str, Any]:
    return {
        "database": str(db_path),
        "database_ready": Path(db_path).exists(),
        "session_ready": has_valid_session(),
    }
