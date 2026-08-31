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
from .evaluation import ListingEvaluation, evaluate_listing
from .fashion import enrich_title
from .live_sources import BuyeeAuthenticatedSource, LiveListing, LiveMarketLoop
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
    if not listing.title_original:
        return False
    profile = enrich_title(listing.title_original, listing.brand)
    if any(signal.value.startswith("irrelevant:") for signal in profile.negative_signals):
        return False
    return profile.brand is not None


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


def _route_evaluation(
    listing: LiveListing,
    evaluation: ListingEvaluation,
    ending_threshold_minutes: int = 60,
) -> tuple[str, str] | None:
    heat = evaluation.desirability.score
    economics = evaluation.economics
    risk = evaluation.auth_risk.level
    profile = evaluation.profile
    if risk == "do_not_touch":
        return None
    if risk == "high":
        return "review", "; ".join(evaluation.auth_risk.reasons[:2])
    if (
        listing.buyout_price is not None
        and economics.opportunity_score >= 70
        and economics.comp_confidence in {"medium", "high"}
    ):
        return "buyouts", "strong sold-comp economics at a fixed buyout"
    if heat >= 82:
        return "grails", "high piece-specific closet heat"
    if (
        heat >= 70
        and profile.category
        and profile.category.value == "jewelry"
        and profile.material
        and profile.material.value == "silver"
    ):
        return "silver", "recognizable silver-jewelry candidate with strong piece signals"
    remaining = _minutes_remaining(listing.time_remaining)
    if remaining is not None and remaining <= ending_threshold_minutes and heat >= 70:
        return "ending", "desirable piece ending within the configured threshold"
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
    for listing in listings:
        if not _is_relevant(listing):
            result.rejected_irrelevant += 1
            continue
        if not _price_is_valid(listing):
            result.rejected_invalid += 1
            continue
        evaluation = evaluate_listing(listing, settings=settings)
        if evaluation.profile.brand:
            listing.brand = evaluation.profile.brand.value
        if evaluation.profile.category:
            listing.category = evaluation.profile.category.value
        listing.desirability_score = evaluation.desirability.score
        listing.opportunity_score = evaluation.economics.opportunity_score
        listing.auth_risk = evaluation.auth_risk.level
        listing.comp_confidence = evaluation.economics.comp_confidence
        listing.raw = {
            **(listing.raw or {}),
            "intelligence": {
                "desirability_reasons": evaluation.desirability.reasons,
                "warnings": evaluation.desirability.warnings + evaluation.economics.warnings,
                "auth_reasons": evaluation.auth_risk.reasons,
            },
        }
        routed = _route_evaluation(listing, evaluation, ending_threshold)
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
        sent, _, _ = send_listing_alert(
            listing, route, reason, evaluation.desirability.score, evaluation
        )
        if sent:
            save_alert_state(listing.source, listing.source_listing_id, signature, route, db_path)
            result.new_alerts += 1
        else:
            result.failed_alerts += 1
    result.new_rows, result.updated_rows = save_listings(listings, db_path)
    return result


def health_report(db_path: str | Path = DB_PATH) -> dict[str, Any]:
    return {
        "database": str(db_path),
        "database_ready": Path(db_path).exists(),
        "session_ready": has_valid_session(),
    }
