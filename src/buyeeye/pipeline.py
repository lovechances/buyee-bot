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


@dataclass
class ScanResult:
    scanned: int = 0
    new_rows: int = 0
    updated_rows: int = 0
    new_alerts: int = 0
    suppressed_alerts: int = 0
    failed_alerts: int = 0


def _route(listing: LiveListing, score: int) -> tuple[str, str]:
    text = f"{listing.title_original} {listing.brand}".lower()
    if listing.buyout_price is not None:
        return "buyouts", "buyout price is available"
    if score >= 82:
        return "grails", "high preliminary opportunity score"
    if "chrome hearts" in text or "silver" in text or "925" in text:
        return "silver", "silver-related brand or term"
    if listing.time_remaining and re.search(
        r"\b(?:hour|minute|day)\b", listing.time_remaining, re.IGNORECASE
    ):
        return "ending", "active auction timing is available"
    return "review", "new live listing requires preliminary review"


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


async def scan_once(query: str, db_path: str | Path = DB_PATH, send_alerts: bool = True) -> ScanResult:
    source = BuyeeAuthenticatedSource()
    loop = LiveMarketLoop(sources=[source])
    listings = await loop.run_once(query, persist=False)
    result = ScanResult(scanned=len(listings))
    before = {item.source_listing_id: get_alert_state(item.source, item.source_listing_id, db_path) for item in listings}
    result.new_rows, result.updated_rows = save_listings(listings, db_path)
    for listing in listings:
        score = score_listing({
            "title_original": listing.title_original,
            "brand": listing.brand,
            "current_price": listing.current_price,
        })
        route, reason = _route(listing, score)
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
