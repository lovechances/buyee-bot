from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .browser_session import has_valid_session
from .comp_cache import CompRepository, CompService
from .comp_sources import ObservedUSSoldSource, YahooEndedSource
from .db import (
    DB_PATH,
    AlertState,
    backfill_alert_material_state,
    get_alert_state,
    save_alert_state,
    save_listings,
)
from .detail_enrichment import BuyeeDetailService, DetailRepository
from .discord_alerts import alert_thesis, send_listing_alert
from .discovery import QuerySpec, search_page_budget
from .evaluation import (
    ListingEvaluation,
    assess_auction_maturity,
    evaluate_listing,
    has_explicit_non_menswear_evidence,
    has_grail_design_evidence,
    has_specific_archive_context,
)
from .fashion import enrich_title, is_generic_signal
from .live_sources import BuyeeAuthenticatedSource, LiveListing, LiveMarketLoop
from .runtime_state import (
    mark_listing_seen,
    record_decision_classifications,
    record_listing_traces,
    record_stage_a_outcomes,
)
from .settings import load_settings
from .stage_a import (
    assess_stage_a,
    brand_is_confirmed,
    category_is_valid,
    detected_family,
    select_stage_a_candidates,
)

DECISION_LOGIC_VERSION = "ultra-semantic-value-v3"


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
    post_detail_reject_counts: dict[str, int] | None = None
    piece_relationship_counts: dict[str, int] | None = None
    confidence_counts: dict[str, dict[str, int]] | None = None
    post_detail_decisions: list[dict[str, Any]] | None = None
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
    alert_thesis_counts: dict[str, int] | None = None
    recovery_counts: dict[str, int] | None = None
    logic_reconsidered: int = 0
    lifetime_first_seen: int = 0
    historical_revisits: int = 0
    material_state_changes: int = 0


@dataclass(frozen=True)
class PostDetailDecision:
    route: str | None
    reason: str
    rejection_reason: str | None = None


@dataclass(frozen=True)
class AlertMaterialSnapshot:
    route: str
    thesis: str
    price_jpy: int | None
    price_kind: str | None
    maturity_state: str
    identity: str
    identity_confidence: str
    risk_state: str
    evidence_state: str
    verified_median_jpy: int | None


@dataclass(frozen=True)
class AlertEventDecision:
    should_alert: bool
    event: str
    legacy_backfill: bool = False


def _compact_listing_trace(
    listing: LiveListing,
    spec: QuerySpec,
    classification: str | None,
) -> dict[str, Any]:
    """Build a bounded, secret-free explanation of the latest pipeline pass."""
    raw = listing.raw or {}
    stage_a_raw = dict(raw.get("stage_a") or {})
    # Keep the Stage A facts needed to explain a miss, without copying a
    # second full assessment into every SeenListing row.  The trace is a
    # rolling diagnostic aid; canonical identity and dedupe state already
    # live in dedicated columns.
    stage_a_keys = (
        "selected",
        "eligible",
        "eligible_paths",
        "primary_path",
        "inspection_score",
        "brand_confirmed",
        "target_brand_match",
        "category_valid",
        "item_type",
        "item_type_confident",
        "actionable_price",
        "price_in_discovery_band",
        "discovery_price_fraction",
        "actionable_buyout_fraction",
        "piece_relationship",
        "hard_veto",
        "reject_reason",
    )
    stage_a = {
        key: stage_a_raw[key]
        for key in stage_a_keys
        if stage_a_raw.get(key) is not None
        and not (key == "eligible_paths" and not stage_a_raw.get(key))
    }
    if stage_a_raw.get("detected_family") not in {None, spec.family}:
        stage_a["detected_family"] = stage_a_raw["detected_family"]
    if stage_a_raw.get("reasons"):
        stage_a["reasons"] = list(stage_a_raw["reasons"])[:2]
    detail = raw.get("stage_b") or {}
    intelligence = raw.get("intelligence") or {}
    diagnostic = raw.get("diagnostic") or {}
    actionable_price_kind = intelligence.get("actionable_price_kind")
    if actionable_price_kind == "buyout":
        actionable_price = listing.buyout_price
    elif actionable_price_kind == "current_bid":
        actionable_price = listing.current_price
    else:
        actionable_price = None
    dedupe = raw.get("dedupe") or {}
    trace: dict[str, Any] = {
        "query": spec.query,
        "family": spec.family,
        "query_layer": spec.query_layer,
        "strategy_id": spec.strategy_id,
        "strategy_type": spec.strategy_type,
        "search_page": raw.get("search_page"),
        "search_rank": raw.get("search_rank_on_page") or raw.get("search_rank"),
        "current_price_jpy": listing.current_price,
        "buyout_price_jpy": listing.buyout_price,
        "actionable_price_jpy": actionable_price,
        "actionable_price_kind": actionable_price_kind,
        "time_remaining": listing.time_remaining,
        "auction_maturity": intelligence.get("auction_maturity"),
        "revisit_reason": dedupe.get("revisit_reason"),
        "stage_a": stage_a,
        "decision": classification,
    }
    trace = {key: value for key, value in trace.items() if value is not None}
    # Most cards die cheaply at Stage A. Their search context, rank, prices and
    # rejection features are enough to explain a manual-benchmark miss without
    # spending scarce persistent-volume space on empty Stage B shells.
    if not stage_a.get("selected"):
        return trace

    trace.update({
        "detail_status": detail.get("status"),
        "detail_retrieval": detail.get("retrieval"),
        "detail_error": detail.get("error_type"),
        "identity": {
            "brand": listing.brand,
            "item_type": intelligence.get("item_type"),
            "score": intelligence.get("identity_score"),
            "confidence": intelligence.get("identity_confidence"),
            "relationship": intelligence.get("piece_relationship"),
            "product_scope": intelligence.get("product_scope"),
        },
        "acquisition_value": {
            "landed_cost_jpy": intelligence.get("landed_cost_jpy"),
            "directional_score": intelligence.get("directional_score"),
            "directional_confidence": intelligence.get("directional_confidence"),
            "us_valuation_state": intelligence.get("valuation_state"),
            "exact_valuation_confidence": intelligence.get(
                "exact_valuation_confidence"
            ),
            "japan_market_fraction": intelligence.get("japan_market_fraction"),
            "japan_market_median_jpy": intelligence.get(
                "japan_market_median_jpy"
            ),
        },
        "evidence": {
            "us_comp_sources": list(intelligence.get("comp_sources") or [])[:4],
            "us_comps_accepted": intelligence.get("comps_accepted"),
            "japan_comp_sources": list(
                intelligence.get("japan_comp_sources") or []
            )[:4],
            "japan_comps_accepted": intelligence.get("japan_comps_accepted"),
        },
        "route": diagnostic.get("route"),
        "route_reason": diagnostic.get("reason"),
        "rejection_reason": diagnostic.get("rejection_reason"),
        "alert_event": diagnostic.get("alert_event"),
        "alert_outcome": diagnostic.get("alert_outcome"),
    })
    return trace


def _directional_why(evaluation: ListingEvaluation) -> str:
    """Summarize the actual non-FMV evidence behind a directional decision."""
    profile = evaluation.profile
    parts: list[str] = []
    relationship = profile.relationship.value if profile.relationship else "uncertain"
    if relationship == "core":
        parts.append("core line")
    elif relationship == "collaboration":
        parts.append("substantive collaboration")
    elif relationship == "diffusion":
        parts.append("piece-specific diffusion signal")
    if profile.collection:
        parts.append(profile.collection.value)
    elif profile.era:
        parts.append(profile.era.value.replace("_", " ").title())
    if evaluation.directional.piece_signals:
        parts.append("/".join(evaluation.directional.piece_signals[:2]))
    if (
        evaluation.directional.japan_market_fraction is not None
        and evaluation.directional.japan_market_median_jpy is not None
    ):
        parts.append(
            f"{evaluation.directional.japan_market_fraction:.0%} of piece-matched "
            f"Japan ended median (¥{evaluation.directional.japan_market_median_jpy:,})"
        )
    if any("absolute downside" in reason for reason in evaluation.directional.reasons):
        parts.append("low absolute downside")
    return " · ".join(parts[:4]) or "strong directional value evidence"


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
    return bool(
        profile.brand is not None
        and not profile.product_scope.hard_non_target
        and profile.product_scope.acquisition_attributable
    )


def _is_true_grail(
    evaluation: ListingEvaluation,
    *,
    grail_min_heat: int,
    archive_directional_min_heat: int,
    archive_directional_min_score: int,
    allow_medium_identity: bool = False,
) -> bool:
    """Require piece evidence beyond a famous collection/season token."""
    profile = evaluation.profile
    identity_ok = evaluation.identity.confidence == "high" or (
        allow_medium_identity and evaluation.identity.confidence == "medium"
    )
    if any(
        "detail enrichment failed" in reason
        for reason in evaluation.identity.reasons
    ):
        return False
    generic = any(
        is_generic_signal(signal, profile.relationship)
        for signal in profile.negative_signals
    )
    scoped_product = bool(
        profile.product_scope.acquisition_attributable
        and not profile.product_scope.hard_non_target
        and profile.item_type
        and profile.category
        and profile.category.value in {"menswear", "accessory"}
    )
    design_specific = has_grail_design_evidence(
        evaluation.directional.piece_signals
    )
    named_piece = bool(
        has_specific_archive_context(profile)
        and design_specific
        and evaluation.desirability.score >= archive_directional_min_heat
        and evaluation.directional.score >= archive_directional_min_score
    )
    exceptional_piece = bool(
        evaluation.desirability.score >= grail_min_heat
        and design_specific
        and len(evaluation.directional.piece_signals) >= 2
    )
    return bool(
        scoped_product
        and not generic
        and identity_ok
        and (named_piece or exceptional_piece)
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


def _dominant_post_detail_rejection(
    listing: LiveListing,
    evaluation: ListingEvaluation,
) -> str:
    """Return one explainable reason for an enriched candidate not becoming final."""
    profile = evaluation.profile
    relationship = profile.relationship.value if profile.relationship else "uncertain"
    relationship_provenance = profile.relationship.provenance if profile.relationship else ""
    generic = any(
        is_generic_signal(signal, profile.relationship)
        for signal in profile.negative_signals
    )
    irrelevant = any(
        signal.value.startswith("irrelevant:") for signal in profile.negative_signals
    )
    if not listing.title_original or not any(
        price is not None
        for price in (listing.current_price, listing.buyout_price, listing.starting_price)
    ):
        return "malformed_data"
    if profile.product_scope.target_role in {"style_reference", "contradicted"}:
        return "target_identity_mismatch"
    if profile.product_scope.offer_kind == "packaging_only":
        return "offer_scope_mismatch"
    if profile.product_scope.offer_clarity != "clear":
        return "partial_offer_ambiguous"
    if evaluation.auth_risk.level == "do_not_touch":
        return "severe_auth_risk"
    if evaluation.auction_maturity.auction_type == "open_auction" and not (
        evaluation.auction_maturity.price_actionable
    ):
        return "immature_auction"
    if (
        irrelevant
        or has_explicit_non_menswear_evidence(listing, profile)
        or not profile.category
        or profile.category.value not in {
            "menswear",
            "accessory",
            "jewelry",
        }
    ):
        return "category_mismatch"
    if relationship == "homage_reference" or relationship_provenance.endswith(
        ".collaboration_low_priority"
    ):
        return "homage_or_secondary_brand"
    if relationship == "diffusion" and evaluation.directional.thesis is None:
        return "diffusion_low_value"
    if evaluation.identity.confidence == "low":
        return "identity_too_uncertain"
    if generic:
        return "generic_after_detail"
    if evaluation.auth_risk.level == "high":
        return "seller_risk"
    if evaluation.economics.landed_cost_jpy is None:
        return "price_context_uncertain"
    if (
        evaluation.valuation.confidence == "unavailable"
        and evaluation.directional.thesis is None
    ):
        return "missing_us_valuation_but_directional_weak"
    if evaluation.desirability.score < 55:
        return "insufficient_desirability"
    return "route_threshold"


def _post_detail_decision(
    listing: LiveListing,
    evaluation: ListingEvaluation,
    ending_threshold_minutes: int = 60,
    archive_watch_min_heat: int = 70,
    silver_min_heat: int = 50,
    grail_min_heat: int = 82,
    archive_directional_min_heat: int = 55,
    archive_directional_min_score: int = 75,
) -> PostDetailDecision:
    heat = evaluation.desirability.score
    economics = evaluation.economics
    risk = evaluation.auth_risk.level
    profile = evaluation.profile
    low_priority_collaboration = bool(
        profile.relationship
        and profile.relationship.provenance.endswith(".collaboration_low_priority")
    )
    irrelevant = any(
        signal.value.startswith("irrelevant:") for signal in profile.negative_signals
    )
    if profile.product_scope.target_role == "style_reference":
        return PostDetailDecision(
            None,
            "target name is explicitly a style/reference, not the manufacturer",
            "target_identity_mismatch",
        )
    if profile.product_scope.target_role == "contradicted":
        return PostDetailDecision(
            None,
            "; ".join(profile.product_scope.reasons[:2]),
            "target_identity_mismatch",
        )
    if profile.product_scope.offer_kind == "packaging_only":
        return PostDetailDecision(
            None,
            "the offer is packaging/accessories only, not the represented product",
            "offer_scope_mismatch",
        )
    if profile.product_scope.offer_clarity != "clear":
        return PostDetailDecision(
            None,
            "the current price cannot be attributed to one clearly available item",
            "partial_offer_ambiguous",
        )
    if risk == "do_not_touch":
        return PostDetailDecision(None, "severe authentication evidence", "severe_auth_risk")
    if irrelevant:
        return PostDetailDecision(
            None,
            "detail evidence identifies a non-fashion object",
            "category_mismatch",
        )
    if has_explicit_non_menswear_evidence(listing, profile):
        return PostDetailDecision(
            None,
            "detail category explicitly identifies non-menswear inventory",
            "category_mismatch",
        )
    if low_priority_collaboration and evaluation.directional.thesis != "asymmetric_value":
        return PostDetailDecision(
            None,
            "secondary-brand collaboration lacks independent directional evidence",
            "homage_or_secondary_brand",
        )
    true_grail = _is_true_grail(
        evaluation,
        grail_min_heat=grail_min_heat,
        archive_directional_min_heat=archive_directional_min_heat,
        archive_directional_min_score=archive_directional_min_score,
        allow_medium_identity=(
            evaluation.auction_maturity.auction_type == "open_auction"
            and evaluation.auction_maturity.level == "low"
        ),
    )
    credible_economics = bool(
        economics.opportunity_score >= 70
        and economics.comp_confidence in {"medium", "high"}
    )
    directional_value = evaluation.directional.thesis == "asymmetric_value"
    actionable_buyout_thesis = bool(
        evaluation.auction_maturity.actionable_price_kind == "buyout"
        and (directional_value or credible_economics)
    )
    if (
        evaluation.auction_maturity.auction_type == "open_auction"
        and evaluation.auction_maturity.level == "low"
        and not actionable_buyout_thesis
    ):
        if true_grail and heat >= archive_watch_min_heat:
            if risk == "high":
                return PostDetailDecision(
                    "review",
                    "exceptional early archive watch with material identity/auth risk",
                )
            return PostDetailDecision(
                "grails", "early archive discovery; current bid is provisional"
            )
        return PostDetailDecision(None, "auction price is still provisional", "immature_auction")
    if risk == "high":
        if true_grail or credible_economics or directional_value:
            return PostDetailDecision(
                "review",
                "; ".join(evaluation.auth_risk.reasons[:2]),
            )
        return PostDetailDecision(None, "authentication risk outweighs signal", "seller_risk")
    meaningful_auth_concern = any(
        phrase in " ".join(evaluation.auth_risk.reasons).lower()
        for phrase in (
            "implausibly low",
            "seller history unavailable",
            "detail-photo coverage unavailable",
        )
    )
    if meaningful_auth_concern and (directional_value or credible_economics):
        return PostDetailDecision(
            "review", "compelling opportunity with material authentication uncertainty"
        )
    if (
        profile.category
        and profile.category.value == "jewelry"
        and profile.material
        and profile.material.value == "silver"
        and heat >= silver_min_heat
        and (directional_value or credible_economics or true_grail)
    ):
        return PostDetailDecision(
            "silver", "recognizable silver-jewelry candidate with strong piece signals"
        )
    if (
        evaluation.auction_maturity.actionable_price_kind == "buyout"
        and credible_economics
    ):
        return PostDetailDecision("buyouts", "strong sold-comp economics at a fixed buyout")
    if (
        evaluation.auction_maturity.actionable_price_kind == "buyout"
        and directional_value
    ):
        return PostDetailDecision("buyouts", _directional_why(evaluation))
    remaining = _minutes_remaining(listing.time_remaining)
    if (
        evaluation.auction_maturity.auction_type == "open_auction"
        and evaluation.auction_maturity.actionable_price_kind == "current_bid"
        and remaining is not None
        and remaining <= ending_threshold_minutes
        and (directional_value or credible_economics or true_grail)
    ):
        return PostDetailDecision(
            "ending", "qualifying opportunity ending within the configured threshold"
        )
    if true_grail:
        reason = (
            "strong named archive identity and piece-specific evidence"
            if has_specific_archive_context(profile)
            else "exceptional piece-specific archive evidence"
        )
        return PostDetailDecision(
            "grails", reason
        )
    return PostDetailDecision(
        None,
        "candidate did not meet a final route",
        _dominant_post_detail_rejection(listing, evaluation),
    )


def _route_evaluation(
    listing: LiveListing,
    evaluation: ListingEvaluation,
    ending_threshold_minutes: int = 60,
    archive_watch_min_heat: int = 70,
    silver_min_heat: int = 50,
    grail_min_heat: int = 82,
    archive_directional_min_heat: int = 55,
    archive_directional_min_score: int = 75,
) -> tuple[str, str] | None:
    """Compatibility wrapper preserving the one-primary-route public behavior."""
    decision = _post_detail_decision(
        listing,
        evaluation,
        ending_threshold_minutes,
        archive_watch_min_heat,
        silver_min_heat,
        grail_min_heat,
        archive_directional_min_heat,
        archive_directional_min_score,
    )
    if decision.route is None:
        return None
    return decision.route, decision.reason


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


def _observation_maturity_state(
    listing: LiveListing,
    settings: dict[str, Any] | None = None,
) -> str:
    maturity = assess_auction_maturity(listing, settings)
    if maturity.auction_type == "fixed_price":
        return "fixed_buyout"
    remaining = maturity.remaining_minutes
    if remaining is None:
        return "unknown"
    alert = (settings or {}).get("alert", {})
    if remaining <= int(alert.get("ending_threshold_minutes", 60)):
        return "ending"
    if remaining <= int(alert.get("auction_alert_max_remaining_hours", 12)) * 60:
        return "mature"
    return "early_with_buyout" if maturity.actionable_price_kind == "buyout" else "early"


def _signature(
    listing: LiveListing,
    settings: dict[str, Any] | None = None,
) -> str:
    """Hash source state that merits reevaluation, excluding display-only drift."""
    values = {
        "title": " ".join((listing.title_original or "").casefold().split()),
        "price": listing.current_price,
        "buyout": listing.buyout_price,
        "starting": listing.starting_price,
        "bid_count": listing.bid_count,
        "maturity_state": _observation_maturity_state(listing, settings),
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _alert_material_snapshot(
    listing: LiveListing,
    evaluation: ListingEvaluation,
    route: str,
    thesis: str,
    settings: dict[str, Any] | None = None,
) -> AlertMaterialSnapshot:
    profile = evaluation.profile
    relationship = profile.relationship.value if profile.relationship else "uncertain"
    participants = (
        ",".join(profile.relationship.participants)
        if profile.relationship and profile.relationship.participants
        else ""
    )
    identity = "|".join(
        str(value or "-").casefold()
        for value in (
            profile.brand.value if profile.brand else None,
            relationship,
            profile.item_type.value if profile.item_type else None,
            profile.collection.value if profile.collection else None,
            profile.era.value if profile.era else None,
            participants,
        )
    )
    maturity_state = _observation_maturity_state(listing, settings)
    if evaluation.auction_maturity.actionable_price_kind == "buyout":
        price_jpy = listing.buyout_price
        price_kind = "buyout"
    elif evaluation.auction_maturity.actionable_price_kind == "current_bid":
        price_jpy = listing.current_price
        price_kind = "current_bid"
    else:
        price_jpy = None
        price_kind = None
    verified = evaluation.valuation.confidence in {"medium", "high"}
    return AlertMaterialSnapshot(
        route=route,
        thesis=thesis,
        price_jpy=price_jpy,
        price_kind=price_kind,
        maturity_state=maturity_state,
        identity=identity,
        identity_confidence=evaluation.identity.confidence,
        risk_state=evaluation.auth_risk.level,
        evidence_state="verified_us_sold" if verified else "us_resale_unverified",
        verified_median_jpy=(evaluation.comps.median_jpy if verified else None),
    )


def _alert_event_decision(
    previous: AlertState | None,
    current: AlertMaterialSnapshot,
    *,
    now: datetime | None = None,
    cooldown_hours: float = 12,
    price_drop_fraction: float = 0.15,
    price_drop_min_jpy: int = 1_000,
    price_drop_absolute_jpy: int = 5_000,
    price_drop_absolute_min_fraction: float = 0.05,
) -> AlertEventDecision:
    """Decide explicit user-action events; route/display drift is never sufficient."""
    if previous is None:
        return AlertEventDecision(True, "initial_alert")
    if previous.material_state_version is None:
        return AlertEventDecision(False, "legacy_state_backfill", legacy_backfill=True)

    # Ending soon is the one time-critical transition allowed to bypass the
    # defensive cooldown. Normal 6h -> 5h countdown drift remains one state.
    if (
        current.route == "ending"
        and current.maturity_state == "ending"
        and previous.last_material_maturity_state != "ending"
    ):
        return AlertEventDecision(True, "entered_ending_soon")

    event: str | None = None
    if (
        current.price_kind == "buyout"
        and previous.last_material_price_kind != "buyout"
    ):
        event = "buyout_became_available"
    elif (
        current.price_jpy is not None
        and previous.last_material_price_jpy is not None
        and current.price_kind == previous.last_material_price_kind
        and current.price_jpy < previous.last_material_price_jpy
    ):
        drop = previous.last_material_price_jpy - current.price_jpy
        fraction = drop / max(1, previous.last_material_price_jpy)
        if (
            (drop >= price_drop_min_jpy and fraction >= price_drop_fraction)
            or (
                drop >= price_drop_absolute_jpy
                and fraction >= price_drop_absolute_min_fraction
            )
        ):
            event = "material_price_drop"

    risk_order = {"do_not_touch": 3, "high": 2, "medium": 1, "low": 0}
    if event is None and risk_order.get(previous.last_material_risk_state or "", 0) > (
        risk_order.get(current.risk_state, 0)
    ):
        event = "risk_materially_improved"
    if (
        event is None
        and current.identity_confidence == "high"
        and previous.last_material_identity
        and previous.last_material_identity != current.identity
    ):
        event = "identity_materially_changed"
    if (
        event is None
        and previous.last_material_evidence_state != "verified_us_sold"
        and current.evidence_state == "verified_us_sold"
    ):
        event = "verified_us_evidence_obtained"
    if (
        event is None
        and current.evidence_state == "verified_us_sold"
        and current.verified_median_jpy
        and previous.last_verified_median_jpy
    ):
        movement = abs(current.verified_median_jpy - previous.last_verified_median_jpy)
        if movement / max(1, previous.last_verified_median_jpy) >= 0.25:
            event = "verified_us_value_materially_changed"
    if event is None:
        return AlertEventDecision(False, "unchanged_material_state")

    current_time = now or datetime.now(UTC)
    last_alert = previous.last_alert_at
    if last_alert.tzinfo is None:
        last_alert = last_alert.replace(tzinfo=UTC)
    if last_alert > current_time - timedelta(hours=max(0.0, cooldown_hours)):
        return AlertEventDecision(False, f"cooldown:{event}")
    return AlertEventDecision(True, event)


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
    scanner_settings = settings.get("scanner", {})
    source = BuyeeAuthenticatedSource(
        max_pages=search_page_budget(spec, scanner_settings),
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
        post_detail_reject_counts={},
        piece_relationship_counts={},
        confidence_counts={
            "identity": {},
            "valuation": {},
            "directional": {},
        },
        post_detail_decisions=[],
        alert_thesis_counts={},
        recovery_counts={
            "parser_reload_attempts": source.parser_reload_attempts,
            "parser_reload_successes": source.parser_reload_successes,
        },
        detected_family_funnel={},
    )
    if global_dedupe:
        unique: list[LiveListing] = []
        logic_reconsideration_used = False
        for listing in listings:
            seen = mark_listing_seen(
                listing.source,
                listing.source_listing_id,
                _signature(listing, settings),
                query_text,
                db_path,
                freshness_hours=int(settings.get("scanner", {}).get("listing_dedupe_hours", 6)),
                fully_evaluated_freshness_hours=int(
                    settings.get("scanner", {}).get(
                        "fully_evaluated_dedupe_hours", 24
                    )
                ),
                strategy_id=spec.strategy_id,
                strategy_type=spec.strategy_type,
                family=spec.family,
                decision_logic_version=DECISION_LOGIC_VERSION,
                allow_logic_reconsideration=not logic_reconsideration_used,
                logic_reconsideration_max_age_hours=int(
                    settings.get("scanner", {}).get(
                        "decision_logic_reconsideration_max_age_hours", 24
                    )
                ),
                logic_reconsideration_max_per_hour=int(
                    settings.get("scanner", {}).get(
                        "decision_logic_reconsideration_max_per_hour", 2
                    )
                ),
                search_page=(listing.raw or {}).get("search_page"),
                search_rank=(listing.raw or {}).get("search_rank_on_page")
                or (listing.raw or {}).get("search_rank"),
                retry_incomplete_processing=True,
            )
            listing.raw = {
                **(listing.raw or {}),
                "dedupe": {
                    "lifetime_first_seen": seen.lifetime_first_seen,
                    "material_state_changed": seen.material_state_changed,
                    "revisit_reason": seen.revisit_reason,
                },
            }
            if seen.lifetime_first_seen:
                result.lifetime_first_seen += 1
            else:
                result.historical_revisits += 1
            if seen.material_state_changed:
                result.material_state_changes += 1
            if seen.logic_reconsidered:
                logic_reconsideration_used = True
                result.logic_reconsidered += 1
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
    decision_classifications: dict[tuple[str, str], str] = {}

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
            scope = evaluation.profile.product_scope
            if not listing.title_original:
                reason = "missing_title"
            elif scope.target_role in {"style_reference", "contradicted"}:
                reason = "target_identity_mismatch"
            elif scope.offer_kind == "packaging_only":
                reason = "offer_scope_mismatch"
            elif scope.offer_clarity != "clear":
                reason = "partial_offer_ambiguous"
            elif scope.audience in {"womens", "kids"}:
                reason = "category_mismatch"
            elif any(
                signal.value.startswith("irrelevant:")
                for signal in evaluation.profile.negative_signals
            ):
                reason = "irrelevant_signal"
            else:
                reason = "brand_unresolved"
            listing.raw = {
                **(listing.raw or {}),
                "stage_a": {
                    "selected": False,
                    "eligible": False,
                    "brand_confirmed": confirmed,
                    "detected_family": family,
                    "category_valid": category_valid,
                    "item_type_confident": evaluation.profile.item_type is not None,
                    "hard_veto": True,
                    "reject_reason": reason,
                    "reasons": list(scope.reasons),
                },
            }
            assert result.stage_a_reject_counts is not None
            result.stage_a_reject_counts[reason] = result.stage_a_reject_counts.get(reason, 0) + 1
            decision_classifications[(listing.source, listing.source_listing_id)] = (
                f"stage_a:{reason}"
            )
            continue
        if not _price_is_valid(listing):
            result.rejected_invalid += 1
            listing.raw = {
                **(listing.raw or {}),
                "stage_a": {
                    "selected": False,
                    "eligible": False,
                    "brand_confirmed": confirmed,
                    "detected_family": family,
                    "category_valid": category_valid,
                    "item_type_confident": evaluation.profile.item_type is not None,
                    "hard_veto": True,
                    "reject_reason": "invalid_price",
                    "reasons": ["card contains no plausible acquisition price"],
                },
            }
            assert result.stage_a_reject_counts is not None
            result.stage_a_reject_counts["invalid_price"] = (
                result.stage_a_reject_counts.get("invalid_price", 0) + 1
            )
            decision_classifications[(listing.source, listing.source_listing_id)] = (
                "stage_a:invalid_price"
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
            decision_classifications[(listing.source, listing.source_listing_id)] = (
                f"stage_a:{reason}"
            )
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
                for listing in listings
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
            ),
            force_open_auction_refresh_minutes=ending_threshold,
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
                decision_classifications[(listing.source, listing.source_listing_id)] = (
                    "detail_failed"
                )

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
                "directional_confidence": evaluation.directional.confidence,
                "valuation_state": evaluation.directional.us_valuation_state,
                "exact_valuation_confidence": evaluation.valuation.confidence,
                "identity_confidence": evaluation.identity.confidence,
                "identity_score": evaluation.identity.score,
                "item_type": (
                    evaluation.profile.item_type.value
                    if evaluation.profile.item_type
                    else None
                ),
                "piece_relationship": (
                    evaluation.profile.relationship.value
                    if evaluation.profile.relationship
                    else "uncertain"
                ),
                "product_scope": {
                    "target_role": evaluation.profile.product_scope.target_role,
                    "offer_kind": evaluation.profile.product_scope.offer_kind,
                    "audience": evaluation.profile.product_scope.audience,
                    "offer_clarity": evaluation.profile.product_scope.offer_clarity,
                    "remaining_variant": evaluation.profile.product_scope.remaining_variant,
                    "acquisition_attributable": (
                        evaluation.profile.product_scope.acquisition_attributable
                    ),
                    "reasons": list(evaluation.profile.product_scope.reasons),
                },
                "landed_cost_jpy": evaluation.economics.landed_cost_jpy,
                "japan_market_fraction": evaluation.directional.japan_market_fraction,
                "japan_market_median_jpy": (
                    evaluation.directional.japan_market_median_jpy
                ),
                "directional_piece_signals": list(evaluation.directional.piece_signals),
                "auction_maturity": evaluation.auction_maturity.level,
                "price_actionable": evaluation.auction_maturity.price_actionable,
                "actionable_price_kind": (
                    evaluation.auction_maturity.actionable_price_kind
                ),
            },
        }
        if listing.source_listing_id not in stage_b_ids:
            continue
        decision = _post_detail_decision(
            listing,
            evaluation,
            ending_threshold,
            archive_watch_min_heat=int(settings.get("alert", {}).get("archive_watch_min_heat", 70)),
            silver_min_heat=int(settings.get("alert", {}).get("silver_min_score", 50)),
            grail_min_heat=int(settings.get("alert", {}).get("grail_min_score", 82)),
            archive_directional_min_heat=int(
                settings.get("alert", {}).get("archive_directional_min_heat", 55)
            ),
            archive_directional_min_score=int(
                settings.get("alert", {}).get("archive_directional_min_score", 75)
            ),
        )
        stage_b_status = ((listing.raw or {}).get("stage_b") or {}).get("status")
        if stage_b_status == "enriched":
            relationship = (
                evaluation.profile.relationship.value
                if evaluation.profile.relationship
                else "uncertain"
            )
            assert result.piece_relationship_counts is not None
            result.piece_relationship_counts[relationship] = (
                result.piece_relationship_counts.get(relationship, 0) + 1
            )
            assert result.confidence_counts is not None
            for dimension, confidence in (
                ("identity", evaluation.identity.confidence),
                ("valuation", evaluation.valuation.confidence),
                ("directional", evaluation.directional.confidence),
            ):
                bucket = result.confidence_counts.setdefault(dimension, {})
                bucket[confidence] = bucket.get(confidence, 0) + 1
        if decision.route is None:
            listing.raw["diagnostic"] = {
                "route": None,
                "reason": decision.reason,
                "rejection_reason": decision.rejection_reason,
                "alert_outcome": "not_eligible",
            }
            result.below_threshold += 1
            if stage_b_status == "enriched":
                result.stage_b_rejected += 1
                assessment = assessment_by_id[listing.source_listing_id]
                family_funnel(assessment.detected_family)["stage_b_rejected"] += 1
                rejection_reason = decision.rejection_reason or "other"
                assert result.post_detail_reject_counts is not None
                result.post_detail_reject_counts[rejection_reason] = (
                    result.post_detail_reject_counts.get(rejection_reason, 0) + 1
                )
                listing.raw["intelligence"]["post_detail_rejection_reason"] = rejection_reason
                decision_classifications[(listing.source, listing.source_listing_id)] = (
                    f"reject:{rejection_reason}"
                )
                assert result.post_detail_decisions is not None
                result.post_detail_decisions.append(
                    {
                        "listing_id": listing.source_listing_id,
                        "relationship": relationship,
                        "identity": evaluation.identity.confidence,
                        "valuation": evaluation.valuation.confidence,
                        "directional": evaluation.directional.confidence,
                        "directional_score": evaluation.directional.score,
                        "heat": evaluation.desirability.score,
                        "item_type": (
                            evaluation.profile.item_type.value
                            if evaluation.profile.item_type
                            else None
                        ),
                        "japan_market_fraction": (
                            evaluation.directional.japan_market_fraction
                        ),
                        "price_actionable": evaluation.auction_maturity.price_actionable,
                        "risk": evaluation.auth_risk.level,
                        "rejection_reason": rejection_reason,
                    }
                )
            continue
        route, reason = decision.route, decision.reason
        result.final_candidates += 1
        assessment = assessment_by_id[listing.source_listing_id]
        family_funnel(assessment.detected_family)["finals"] += 1
        result.route_counts[route] = result.route_counts.get(route, 0) + 1
        thesis = alert_thesis(route, evaluation)
        result.thesis_counts[thesis] = result.thesis_counts.get(thesis, 0) + 1
        opportunity_kind = _opportunity_kind(route, thesis, evaluation)
        decision_classifications[(listing.source, listing.source_listing_id)] = (
            f"final:{route}:{thesis}"
        )
        listing.raw["diagnostic"] = {
            "route": route,
            "reason": reason,
            "rejection_reason": None,
            "alert_outcome": "pending",
        }
        result.opportunity_counts[opportunity_kind] = (
            result.opportunity_counts.get(opportunity_kind, 0) + 1
        )
        signature = _signature(listing, settings)
        if stage_b_status == "enriched":
            assert result.post_detail_decisions is not None
            result.post_detail_decisions.append(
                {
                    "listing_id": listing.source_listing_id,
                    "relationship": relationship,
                    "identity": evaluation.identity.confidence,
                    "valuation": evaluation.valuation.confidence,
                    "directional": evaluation.directional.confidence,
                    "directional_score": evaluation.directional.score,
                    "heat": evaluation.desirability.score,
                    "item_type": (
                        evaluation.profile.item_type.value
                        if evaluation.profile.item_type
                        else None
                    ),
                    "japan_market_fraction": evaluation.directional.japan_market_fraction,
                    "price_actionable": evaluation.auction_maturity.price_actionable,
                    "risk": evaluation.auth_risk.level,
                    "route": route,
                    "thesis": thesis,
                }
            )
        if not send_alerts:
            listing.raw["diagnostic"]["alert_outcome"] = "disabled"
            continue
        previous = before[listing.source_listing_id]
        material = _alert_material_snapshot(listing, evaluation, route, thesis, settings)
        alert_settings = settings.get("alert", {})
        alert_event = _alert_event_decision(
            previous,
            material,
            cooldown_hours=float(
                alert_settings.get("same_listing_cooldown_hours", 12)
            ),
            price_drop_fraction=float(
                alert_settings.get("material_price_drop_fraction", 0.15)
            ),
            price_drop_min_jpy=int(
                alert_settings.get("material_price_drop_min_jpy", 1_000)
            ),
            price_drop_absolute_jpy=int(
                alert_settings.get("material_price_drop_absolute_jpy", 5_000)
            ),
            price_drop_absolute_min_fraction=float(
                alert_settings.get(
                    "material_price_drop_absolute_min_fraction", 0.05
                )
            ),
        )
        if alert_event.legacy_backfill:
            listing.raw["diagnostic"].update(
                alert_event=alert_event.event,
                alert_outcome="suppressed",
            )
            backfill_alert_material_state(
                listing.source,
                listing.source_listing_id,
                db_path,
                thesis=material.thesis,
                material_price_jpy=material.price_jpy,
                material_price_kind=material.price_kind,
                material_maturity_state=material.maturity_state,
                material_identity=material.identity,
                material_risk_state=material.risk_state,
                material_evidence_state=material.evidence_state,
                verified_median_jpy=material.verified_median_jpy,
            )
            result.suppressed_alerts += 1
            continue
        if not alert_event.should_alert:
            listing.raw["diagnostic"].update(
                alert_event=alert_event.event,
                alert_outcome="suppressed",
            )
            if alert_event.event.startswith("cooldown:"):
                # The material event is still pending because AlertState was
                # intentionally left unchanged.  Do not let the 24-hour
                # fully-evaluated TTL hide it past the alert cooldown.
                decision_classifications[
                    (listing.source, listing.source_listing_id)
                ] = f"pending_alert:{alert_event.event}"
            result.suppressed_alerts += 1
            continue
        sent, _, _ = send_listing_alert(
            listing, route, reason, evaluation.desirability.score, evaluation
        )
        if sent:
            listing.raw["diagnostic"].update(
                alert_event=alert_event.event,
                alert_outcome="sent",
            )
            save_alert_state(
                listing.source,
                listing.source_listing_id,
                signature,
                route,
                db_path,
                thesis=material.thesis,
                material_price_jpy=material.price_jpy,
                material_price_kind=material.price_kind,
                material_maturity_state=material.maturity_state,
                material_identity=material.identity,
                material_risk_state=material.risk_state,
                material_evidence_state=material.evidence_state,
                verified_median_jpy=material.verified_median_jpy,
                alert_event=alert_event.event,
            )
            result.new_alerts += 1
            family_funnel(assessment.detected_family)["alerts"] += 1
            result.alert_route_counts[route] = result.alert_route_counts.get(route, 0) + 1
            assert result.alert_thesis_counts is not None
            result.alert_thesis_counts[thesis] = result.alert_thesis_counts.get(thesis, 0) + 1
        else:
            listing.raw["diagnostic"].update(
                alert_event=alert_event.event,
                alert_outcome="failed",
            )
            # Delivery did not advance AlertState, so this remains retryable
            # on the normal listing revisit window rather than the 24-hour
            # fully-evaluated window.
            decision_classifications[
                (listing.source, listing.source_listing_id)
            ] = f"pending_alert:delivery_failed:{alert_event.event}"
            result.failed_alerts += 1
    result.new_rows, result.updated_rows = save_listings(listings, db_path)
    result.comp_cache_hits = comp_service.cache_hits + japan_comp_service.cache_hits
    result.comp_cache_misses = comp_service.cache_misses + japan_comp_service.cache_misses
    if global_dedupe:
        record_decision_classifications(
            decision_classifications,
            db_path,
            decision_logic_version=DECISION_LOGIC_VERSION,
        )
        traces: dict[tuple[str, str], dict[str, Any]] = {}
        for listing in listings:
            key = (listing.source, listing.source_listing_id)
            classification = decision_classifications.get(key)
            if classification is None:
                stage_a = (listing.raw or {}).get("stage_a") or {}
                if stage_a.get("selected"):
                    classification = "stage_a:selected"
                elif stage_a.get("reject_reason"):
                    classification = f"stage_a:{stage_a['reject_reason']}"
                else:
                    classification = "stage_a:ineligible"
            traces[key] = _compact_listing_trace(listing, spec, classification)
        record_listing_traces(traces, db_path)
    return result


def health_report(db_path: str | Path = DB_PATH) -> dict[str, Any]:
    return {
        "database": str(db_path),
        "database_ready": Path(db_path).exists(),
        "session_ready": has_valid_session(),
    }
