from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .browser_session import has_valid_session
from .comp_cache import CompRepository, CompService
from .comp_sources import ObservedUSSoldSource, YahooEndedSource
from .db import DB_PATH, get_alert_state, save_alert_state, save_listings
from .detail_enrichment import BuyeeDetailService, DetailRepository
from .discord_alerts import alert_thesis, send_listing_alert
from .discovery import QuerySpec
from .evaluation import ListingEvaluation, evaluate_listing
from .fashion import enrich_title
from .live_sources import BuyeeAuthenticatedSource, LiveListing, LiveMarketLoop
from .runtime_state import mark_listing_seen, record_stage_a_outcomes
from .settings import load_settings
from .stage_a import (
    assess_stage_a,
    brand_is_confirmed,
    category_is_valid,
    detected_family,
    select_stage_a_candidates,
)


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
    alert_route_counts: dict[str, int] | None = None
    thesis_counts: dict[str, int] | None = None
    opportunity_counts: dict[str, int] | None = None
    comp_candidates: int = 0
    comps_accepted: int = 0
    stage_a_rejected: int = 0
    stage_a_brand_confirmed: int = 0
    stage_a_category_valid: int = 0
    stage_a_eligible: int = 0
    stage_a_capped: int = 0
    stage_a_path_counts: dict[str, int] | None = None
    stage_a_reject_counts: dict[str, int] | None = None
    stage_b_candidates: int = 0
    stage_b_enriched: int = 0
    stage_b_failed: int = 0
    stage_b_rejected: int = 0
    comp_queried: int = 0
    final_candidates: int = 0
    unique_listings: int = 0
    duplicates: int = 0
    search_pages: int = 0
    comp_cache_hits: int = 0
    comp_cache_misses: int = 0
    japan_comp_candidates: int = 0
    japan_comps_accepted: int = 0
    detected_family_funnel: dict[str, dict[str, int]] | None = None


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
    archive_watch_min_heat: int = 70,
) -> tuple[str, str] | None:
    heat = evaluation.desirability.score
    economics = evaluation.economics
    risk = evaluation.auth_risk.level
    profile = evaluation.profile
    if risk == "do_not_touch":
        return None
    if risk == "high":
        if heat >= 50 or economics.opportunity_score >= 70:
            return "review", "; ".join(evaluation.auth_risk.reasons[:2])
        return None
    if (
        evaluation.auction_maturity.auction_type == "open_auction"
        and evaluation.auction_maturity.level == "low"
    ):
        if heat >= archive_watch_min_heat and (profile.collection or profile.era):
            return "grails", "early archive discovery; current bid is provisional"
        return None
    meaningful_auth_concern = any(
        phrase in " ".join(evaluation.auth_risk.reasons).lower()
        for phrase in (
            "frequently counterfeited",
            "implausibly low",
            "seller history unavailable",
            "detail-photo coverage unavailable",
        )
    )
    if (
        listing.buyout_price is not None
        and economics.opportunity_score >= 70
        and economics.comp_confidence in {"medium", "high"}
    ):
        return "buyouts", "strong sold-comp economics at a fixed buyout"
    if listing.buyout_price is not None and evaluation.directional.thesis == "asymmetric_value":
        return "buyouts", "actionable fixed price with unusually low absolute downside"
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
    if heat >= 82:
        return "grails", "high piece-specific closet heat"
    if (
        economics.opportunity_score >= 70
        and economics.comp_confidence in {"medium", "high"}
        and heat >= 65
    ):
        if meaningful_auth_concern:
            return "review", "strong economics with material authentication uncertainty"
        return "grails", "piece-aware sold comps show a compelling archive opportunity"
    if evaluation.directional.thesis == "asymmetric_value":
        return "grails", "fashion-relevant piece with unusually low absolute downside"
    return None


def _opportunity_kind(route: str, thesis: str, evaluation: ListingEvaluation) -> str:
    """Describe why a routed listing is actionable, independently of its price format."""
    if thesis == "archive_watch":
        return "archive_watch"
    if route == "buyouts":
        return "actionable_fixed_price"
    if route == "ending":
        return "ending_soon"
    if route == "review":
        return "legit_review"
    if route == "silver":
        return "silver_jewelry"
    if (
        evaluation.auction_maturity.auction_type == "open_auction"
        and evaluation.auction_maturity.price_actionable
        and thesis in {"verified_arbitrage", "asymmetric_value"}
    ):
        return "mature_auction"
    return "archive_grail"


def _signature(listing: LiveListing) -> str:
    values = {
        "title": listing.title_original,
        "price": listing.current_price,
        "buyout": listing.buyout_price,
        "starting": listing.starting_price,
        "time": listing.time_remaining,
        "bid_count": listing.bid_count,
        "watch_count": listing.watch_count,
        "image": listing.image_url,
        "opportunity": listing.opportunity_score,
        "auth_risk": listing.auth_risk,
        "comp_confidence": listing.comp_confidence,
        "comp_sources": ((listing.raw or {}).get("intelligence") or {}).get("comp_sources", []),
        "auction_maturity": ((listing.raw or {}).get("intelligence") or {}).get("auction_maturity"),
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


async def scan_once(
    query: str | QuerySpec,
    db_path: str | Path = DB_PATH,
    send_alerts: bool = True,
    global_dedupe: bool = False,
) -> ScanResult:
    settings = load_settings()
    spec = (
        query
        if isinstance(query, QuerySpec)
        else QuerySpec(
            query=query,
            provenance="manual.scan_once",
            family="manual",
            priority=0,
            strategy_type="semantic_manual",
        )
    )
    query_text = spec.query
    source = BuyeeAuthenticatedSource(
        max_pages=int(settings.get("scanner", {}).get("search_pages_per_query", 2)),
        search_filters=spec.search_filters,
        discovery_context=spec.discovery_observation(),
    )
    loop = LiveMarketLoop(sources=[source])
    listings = await loop.run_once(query_text, persist=False)
    ending_threshold = int(settings.get("alert", {}).get("ending_threshold_minutes", 60))
    result = ScanResult(
        scanned=len(listings),
        unique_listings=len(listings),
        search_pages=source.last_page_count,
        route_counts={},
        alert_route_counts={},
        thesis_counts={},
        opportunity_counts={},
        stage_a_path_counts={},
        stage_a_reject_counts={},
        detected_family_funnel={},
    )
    if global_dedupe:
        unique: list[LiveListing] = []
        for listing in listings:
            seen = mark_listing_seen(
                listing.source,
                listing.source_listing_id,
                _signature(listing),
                query_text,
                db_path,
                freshness_hours=int(settings.get("scanner", {}).get("listing_dedupe_hours", 6)),
                strategy_id=spec.strategy_id,
                strategy_type=spec.strategy_type,
                family=spec.family,
            )
            if seen.is_duplicate:
                result.duplicates += 1
            else:
                unique.append(listing)
        listings = unique
        result.unique_listings = len(unique)
    comp_service = CompService(
        [
            ObservedUSSoldSource(
                jpy_to_usd=float(settings.get("pricing", {}).get("fx_jpy_to_usd", 0.00675))
            )
        ],
        CompRepository(
            db_path,
            ttl_hours=int(settings.get("comps", {}).get("cache_ttl_hours", 72)),
        ),
    )
    japan_comp_service = CompService(
        [YahooEndedSource()],
        CompRepository(
            db_path,
            ttl_hours=int(settings.get("comps", {}).get("cache_ttl_hours", 72)),
        ),
    )
    before = {
        item.source_listing_id: get_alert_state(item.source, item.source_listing_id, db_path)
        for item in listings
    }
    stage_a_assessments = []
    valid_listings: list[LiveListing] = []

    def family_funnel(family: str | None) -> dict[str, int]:
        key = family or "unresolved"
        assert result.detected_family_funnel is not None
        return result.detected_family_funnel.setdefault(
            key,
            {
                "seen": 0,
                "brand_confirmed": 0,
                "category_valid": 0,
                "stage_a_candidates": 0,
                "stage_b_enriched": 0,
                "stage_b_failed": 0,
                "stage_b_rejected": 0,
                "finals": 0,
                "alerts": 0,
            },
        )

    for listing in listings:
        evaluation = evaluate_listing(listing, settings=settings)
        family = detected_family(evaluation)
        funnel = family_funnel(family)
        funnel["seen"] += 1
        confirmed = brand_is_confirmed(
            evaluation,
            float(settings.get("stage_a", {}).get("minimum_brand_confidence", 0.95)),
        )
        category_valid = category_is_valid(listing, evaluation, spec)
        if confirmed:
            result.stage_a_brand_confirmed += 1
            funnel["brand_confirmed"] += 1
        if category_valid:
            result.stage_a_category_valid += 1
            funnel["category_valid"] += 1
        if not _is_relevant(listing):
            result.rejected_irrelevant += 1
            reason = (
                "missing_title"
                if not listing.title_original
                else "irrelevant_signal"
                if any(
                    signal.value.startswith("irrelevant:")
                    for signal in evaluation.profile.negative_signals
                )
                else "brand_unresolved"
            )
            assert result.stage_a_reject_counts is not None
            result.stage_a_reject_counts[reason] = result.stage_a_reject_counts.get(reason, 0) + 1
            continue
        if not _price_is_valid(listing):
            result.rejected_invalid += 1
            assert result.stage_a_reject_counts is not None
            result.stage_a_reject_counts["invalid_price"] = (
                result.stage_a_reject_counts.get("invalid_price", 0) + 1
            )
            continue
        valid_listings.append(listing)
        assessment = assess_stage_a(listing, evaluation, spec, settings)
        stage_a_assessments.append(assessment)

    selected_assessments, capped_assessments = select_stage_a_candidates(
        stage_a_assessments,
        spec,
        settings,
    )
    selected_ids = {assessment.listing_id for assessment in selected_assessments}
    capped_ids = {assessment.listing_id for assessment in capped_assessments}
    assessment_by_id = {assessment.listing_id: assessment for assessment in stage_a_assessments}
    stage_b_listings = [
        listing for listing in valid_listings if listing.source_listing_id in selected_ids
    ]
    result.stage_a_eligible = sum(assessment.eligible for assessment in stage_a_assessments)
    result.stage_a_capped = len(capped_assessments)
    result.stage_b_candidates = len(stage_b_listings)
    result.stage_a_rejected = max(0, result.unique_listings - len(stage_b_listings))
    for listing in valid_listings:
        assessment = assessment_by_id[listing.source_listing_id]
        selected = listing.source_listing_id in selected_ids
        capped = listing.source_listing_id in capped_ids
        listing.raw = {
            **(listing.raw or {}),
            "stage_a": assessment.observation(selected=selected, capped=capped),
        }
        if selected:
            path = assessment.primary_path or "unknown"
            assert result.stage_a_path_counts is not None
            result.stage_a_path_counts[path] = result.stage_a_path_counts.get(path, 0) + 1
            family_funnel(assessment.detected_family)["stage_a_candidates"] += 1
        else:
            reason = "candidate_budget" if capped else assessment.reject_reason or "ineligible"
            assert result.stage_a_reject_counts is not None
            result.stage_a_reject_counts[reason] = result.stage_a_reject_counts.get(reason, 0) + 1
    if global_dedupe:
        record_stage_a_outcomes(
            {
                (listing.source, listing.source_listing_id): (
                    "selected"
                    if listing.source_listing_id in selected_ids
                    else "capped"
                    if listing.source_listing_id in capped_ids
                    else "ineligible"
                )
                for listing in valid_listings
            },
            db_path,
            strategy_type=spec.strategy_type,
            family=spec.family,
        )
    if stage_b_listings:
        detail_service = BuyeeDetailService(
            DetailRepository(
                db_path,
                ttl_hours=int(settings.get("detail", {}).get("cache_ttl_hours", 24)),
            )
        )
        try:
            result.stage_b_enriched, result.stage_b_failed = await detail_service.enrich(
                stage_b_listings
            )
        except Exception:  # noqa: BLE001 - Stage B must fail closed without stopping scans.
            result.stage_b_failed = len(stage_b_listings)
        for listing in stage_b_listings:
            if not (listing.raw or {}).get("stage_b"):
                listing.raw = {
                    **(listing.raw or {}),
                    "stage_b": {"status": "failed", "error_type": "DetailUnavailable"},
                }
            assessment = assessment_by_id[listing.source_listing_id]
            status = ((listing.raw or {}).get("stage_b") or {}).get("status")
            if status == "enriched":
                family_funnel(assessment.detected_family)["stage_b_enriched"] += 1
            elif status == "failed":
                family_funnel(assessment.detected_family)["stage_b_failed"] += 1

    stage_b_ids = {listing.source_listing_id for listing in stage_b_listings}
    comp_threshold = int(settings.get("comps", {}).get("minimum_heat_for_lookup", 40))
    for listing in valid_listings:
        evaluation = evaluate_listing(listing, settings=settings)
        if (
            listing.source_listing_id in stage_b_ids
            and evaluation.desirability.score >= comp_threshold
            and evaluation.auth_risk.level != "do_not_touch"
        ):
            result.comp_queried += 1
            try:
                comp_candidates = await comp_service.retrieve(
                    evaluation.profile,
                    minimum_candidates=int(
                        settings.get("comps", {}).get("minimum_candidates_before_broadening", 5)
                    ),
                )
            except (httpx.HTTPError, OSError, ValueError):
                comp_candidates = []
            try:
                japan_candidates = await japan_comp_service.retrieve(
                    evaluation.profile,
                    minimum_candidates=int(
                        settings.get("comps", {}).get("minimum_candidates_before_broadening", 5)
                    ),
                )
            except (httpx.HTTPError, OSError, ValueError):
                japan_candidates = []
            evaluation = evaluate_listing(
                listing,
                comps=comp_candidates,
                settings=settings,
                japan_comps=japan_candidates,
            )
            result.comp_candidates += evaluation.comps.candidate_count
            result.comps_accepted += len(evaluation.comps.matches)
            if evaluation.japan_comps:
                result.japan_comp_candidates += evaluation.japan_comps.candidate_count
                result.japan_comps_accepted += len(evaluation.japan_comps.matches)
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
                "comp_queries": evaluation.comps.queries,
                "comp_sources": evaluation.comps.sources,
                "comp_candidates": evaluation.comps.candidate_count,
                "comps_accepted": len(evaluation.comps.matches),
                "japan_comp_sources": (
                    evaluation.japan_comps.sources if evaluation.japan_comps else []
                ),
                "japan_comps_accepted": (
                    len(evaluation.japan_comps.matches) if evaluation.japan_comps else 0
                ),
                "directional_score": evaluation.directional.score,
                "valuation_state": evaluation.directional.us_valuation_state,
                "auction_maturity": evaluation.auction_maturity.level,
                "price_actionable": evaluation.auction_maturity.price_actionable,
            },
        }
        if listing.source_listing_id not in stage_b_ids:
            continue
        routed = _route_evaluation(
            listing,
            evaluation,
            ending_threshold,
            archive_watch_min_heat=int(settings.get("alert", {}).get("archive_watch_min_heat", 70)),
        )
        if routed is None:
            result.below_threshold += 1
            if ((listing.raw or {}).get("stage_b") or {}).get("status") == "enriched":
                result.stage_b_rejected += 1
                assessment = assessment_by_id[listing.source_listing_id]
                family_funnel(assessment.detected_family)["stage_b_rejected"] += 1
            continue
        route, reason = routed
        result.final_candidates += 1
        assessment = assessment_by_id[listing.source_listing_id]
        family_funnel(assessment.detected_family)["finals"] += 1
        result.route_counts[route] = result.route_counts.get(route, 0) + 1
        thesis = alert_thesis(route, evaluation)
        result.thesis_counts[thesis] = result.thesis_counts.get(thesis, 0) + 1
        opportunity_kind = _opportunity_kind(route, thesis, evaluation)
        result.opportunity_counts[opportunity_kind] = (
            result.opportunity_counts.get(opportunity_kind, 0) + 1
        )
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
            family_funnel(assessment.detected_family)["alerts"] += 1
            result.alert_route_counts[route] = result.alert_route_counts.get(route, 0) + 1
        else:
            result.failed_alerts += 1
    result.new_rows, result.updated_rows = save_listings(listings, db_path)
    result.comp_cache_hits = comp_service.cache_hits + japan_comp_service.cache_hits
    result.comp_cache_misses = comp_service.cache_misses + japan_comp_service.cache_misses
    return result


def health_report(db_path: str | Path = DB_PATH) -> dict[str, Any]:
    return {
        "database": str(db_path),
        "database_ready": Path(db_path).exists(),
        "session_ready": has_valid_session(),
    }
