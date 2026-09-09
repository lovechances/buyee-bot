from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .discovery import QuerySpec
from .evaluation import ListingEvaluation, has_structurally_generic_product
from .fashion import is_generic_signal


@dataclass(frozen=True)
class StageAAssessment:
    """Cheap card-only decision about whether a detail fetch is worth its cost."""

    listing_id: str
    eligible_paths: tuple[str, ...]
    primary_path: str | None
    inspection_score: float
    brand_confirmed: bool
    detected_family: str | None
    target_brand_match: bool
    category_valid: bool
    item_type: str | None
    item_type_confident: bool
    actionable_price: bool
    price_in_discovery_band: bool
    discovery_price_fraction: float | None
    actionable_buyout_fraction: float | None
    piece_relationship: str
    hard_veto: bool
    reject_reason: str | None
    reasons: tuple[str, ...]
    value_inspection_candidate: bool = False

    @property
    def eligible(self) -> bool:
        return bool(self.eligible_paths) and not self.hard_veto

    def observation(self, *, selected: bool, capped: bool = False) -> dict[str, Any]:
        return {
            "selected": selected,
            "eligible": self.eligible,
            "eligible_paths": list(self.eligible_paths),
            "primary_path": self.primary_path,
            "inspection_score": round(self.inspection_score, 2),
            "brand_confirmed": self.brand_confirmed,
            "detected_family": self.detected_family,
            "target_brand_match": self.target_brand_match,
            "category_valid": self.category_valid,
            "item_type": self.item_type,
            "item_type_confident": self.item_type_confident,
            "actionable_price": self.actionable_price,
            "value_inspection_candidate": self.value_inspection_candidate,
            "price_in_discovery_band": self.price_in_discovery_band,
            "discovery_price_fraction": self.discovery_price_fraction,
            "actionable_buyout_fraction": self.actionable_buyout_fraction,
            "piece_relationship": self.piece_relationship,
            "hard_veto": self.hard_veto,
            "reject_reason": "candidate_budget" if capped else self.reject_reason,
            "reasons": list(self.reasons),
        }


PATH_ORDER = {
    "silver_jewelry": 7,
    "market_buyout_inspection": 6,
    "ending_auction": 5,
    "semantic_archive": 4,
    "semantic_closet_inspection": 3,
    "identity_recovery": 2,
    "unknown_interesting": 1,
}

FASHION_CATEGORY_GROUPS = {
    "mens_fashion",
    "mens_fashion_goods",
    "mens_bags",
    "mens_shoes",
    "mens_accessories",
}


def detected_family(evaluation: ListingEvaluation) -> str | None:
    brand = evaluation.profile.brand
    if not brand or not brand.provenance.startswith("taxonomy.brands."):
        return None
    parts = brand.provenance.split(".")
    return parts[2] if len(parts) > 2 else None


def brand_is_confirmed(evaluation: ListingEvaluation, minimum_confidence: float = 0.95) -> bool:
    brand = evaluation.profile.brand
    return bool(
        brand
        and brand.confidence >= minimum_confidence
        and brand.provenance.startswith("taxonomy.brands.")
    )


def category_is_valid(listing: Any, evaluation: ListingEvaluation, spec: QuerySpec) -> bool:
    if evaluation.profile.category and evaluation.profile.category.value in {
        "menswear",
        "accessory",
        "jewelry",
    }:
        return True
    if str(getattr(listing, "category", "")).casefold() in {
        "menswear",
        "accessory",
        "jewelry",
    }:
        return True
    filters = spec.search_filters
    return bool(filters and filters.category_group in FASHION_CATEGORY_GROUPS)


def _discovery_price_context(
    listing: Any, spec: QuerySpec
) -> tuple[bool, float | None, int | None]:
    filters = spec.search_filters
    if not filters:
        return False, None, None
    if spec.strategy_type in {"market_buyout", "market_explore"}:
        price = getattr(listing, "buyout_price", None)
        minimum = filters.min_buyout_price_jpy
        maximum = filters.max_buyout_price_jpy
    else:
        price = getattr(listing, "current_price", None)
        minimum = filters.min_current_price_jpy
        maximum = filters.max_current_price_jpy
    if price is None or maximum is None:
        return False, None, price
    in_band = (minimum is None or price >= minimum) and price <= maximum
    return in_band, round(price / maximum, 4) if maximum else None, price


def _actionable_buyout_context(listing: Any, spec: QuerySpec) -> tuple[bool, float | None]:
    """Check a real buyout against the slice bounds without treating them as FMV."""
    filters = spec.search_filters
    buyout = getattr(listing, "buyout_price", None)
    if filters is None or buyout is None:
        return False, None
    if spec.strategy_type in {"market_buyout", "market_explore"}:
        minimum = filters.min_buyout_price_jpy
        maximum = filters.max_buyout_price_jpy
    else:
        # Ending/price-band strategies constrain current price. A buyout remains
        # independently actionable only when it also fits that exploration slice.
        minimum = filters.min_current_price_jpy
        maximum = filters.max_current_price_jpy
    fits = bool(
        maximum is not None and (minimum is None or buyout >= minimum) and buyout <= maximum
    )
    return fits, round(buyout / maximum, 4) if maximum else None


def _has_generic_or_junk_signal(evaluation: ListingEvaluation) -> bool:
    return any(
        signal.value.startswith("irrelevant:")
        or is_generic_signal(signal, evaluation.profile.relationship)
        for signal in evaluation.profile.negative_signals
    )


def assess_stage_a(
    listing: Any,
    evaluation: ListingEvaluation,
    spec: QuerySpec,
    settings: dict[str, Any],
) -> StageAAssessment:
    """Return inspection eligibility without changing heat, valuation, or routing."""
    config = settings.get("stage_a", {})
    archive_threshold = int(
        config.get(
            "archive_heat_threshold",
            settings.get("detail", {}).get("minimum_heat", 40),
        )
    )
    minimum_brand_confidence = float(config.get("minimum_brand_confidence", 0.95))
    market_minimum_heat = int(config.get("market_minimum_heat", 18))
    silver_minimum_heat = int(config.get("silver_minimum_heat", 30))
    unknown_absolute_max = int(config.get("unknown_absolute_buyout_max_jpy", 10_000))
    unknown_max_fraction = float(config.get("unknown_max_band_fraction", 0.40))
    semantic_minimum_heat = int(config.get("semantic_inspection_minimum_heat", 12))
    semantic_buyout_max = int(
        config.get("semantic_inspection_max_buyout_jpy", 30_000)
    )
    semantic_bid_max = int(
        config.get("semantic_inspection_max_current_bid_jpy", 25_000)
    )
    identity_recovery_max = int(
        config.get("identity_recovery_max_actionable_jpy", 40_000)
    )

    profile = evaluation.profile
    heat = evaluation.desirability.score
    family = detected_family(evaluation)
    confirmed = brand_is_confirmed(evaluation, minimum_brand_confidence)
    target_match = bool(confirmed and family and family == spec.family)
    item_confident = bool(profile.item_type and profile.item_type.confidence >= 0.80)
    category_valid = category_is_valid(listing, evaluation, spec)
    actionable = evaluation.auction_maturity.price_actionable
    actionable_kind = evaluation.auction_maturity.actionable_price_kind
    actionable_price = (
        getattr(listing, "buyout_price", None)
        if actionable_kind == "buyout"
        else getattr(listing, "current_price", None)
        if actionable_kind == "current_bid"
        else None
    )
    in_band, band_fraction, contextual_price = _discovery_price_context(listing, spec)
    is_market = spec.query_layer == "market_structure"
    buyout_fits_context, buyout_fraction = _actionable_buyout_context(listing, spec)
    generic_or_junk = _has_generic_or_junk_signal(evaluation)
    relationship = profile.relationship.value if profile.relationship else "uncertain"
    relationship_provenance = profile.relationship.provenance if profile.relationship else ""
    relationship_adjustments = {
        "core": 12.0,
        "collaboration": -4.0,
        "diffusion": -12.0,
        "uncertain": -14.0,
        "homage_reference": -30.0,
        **{
            str(name): float(value)
            for name, value in config.get("relationship_rank_adjustments", {}).items()
        },
    }
    relationship_adjustment = relationship_adjustments.get(relationship, -14.0)
    if relationship_provenance.endswith(".collaboration_low_priority"):
        relationship_adjustment += float(
            config.get("low_priority_collaboration_adjustment", -8)
        )
    homage_or_reference = relationship == "homage_reference"
    severe_auth_veto = evaluation.auth_risk.level == "do_not_touch"
    scope_veto = bool(
        profile.product_scope.hard_non_target
        or not profile.product_scope.acquisition_attributable
    )
    hard_veto = severe_auth_veto or scope_veto
    piece_context = bool(
        item_confident
        or profile.era
        or profile.collection
        or profile.season
        or profile.material
        or profile.collaboration
        or profile.positive_signals
        or evaluation.directional.piece_signals
    )
    market_card_relevance = item_confident or heat >= market_minimum_heat
    semantic_inspection = spec.query_layer in {"broad", "medium"}

    paths: list[str] = []
    reasons: list[str] = []
    if heat >= archive_threshold and not hard_veto and not homage_or_reference:
        paths.append("semantic_archive")
        reasons.append(f"archive heat {heat} >= {archive_threshold}")

    if (
        confirmed
        and target_match
        and profile.item_type
        and profile.item_type.value == "jewelry"
        and profile.material
        and profile.material.value == "silver"
        and heat >= silver_minimum_heat
        and not hard_veto
        and not homage_or_reference
    ):
        paths.append("silver_jewelry")
        reasons.append("confirmed silver-jewelry identity")

    if (
        is_market
        and getattr(listing, "buyout_price", None) is not None
        and confirmed
        and target_match
        and category_valid
        and actionable
        and buyout_fits_context
        and piece_context
        and market_card_relevance
        and not hard_veto
        and not generic_or_junk
        and not homage_or_reference
    ):
        paths.append("market_buyout_inspection")
        reasons.append("target-brand actionable buyout inside its discovery context")

    if (
        is_market
        and spec.strategy_type in {"market_ending", "market_closing"}
        and evaluation.auction_maturity.auction_type == "open_auction"
        and evaluation.auction_maturity.actionable_price_kind == "current_bid"
        and confirmed
        and target_match
        and category_valid
        and actionable
        and in_band
        and piece_context
        and market_card_relevance
        and not hard_veto
        and not generic_or_junk
        and not homage_or_reference
    ):
        paths.append("ending_auction")
        reasons.append("target-brand mature auction inside its discovery band")

    semantic_price_ok = bool(
        actionable_price is not None
        and (
            actionable_kind == "buyout" and actionable_price <= semantic_buyout_max
            or actionable_kind == "current_bid" and actionable_price <= semantic_bid_max
        )
    )
    if (
        semantic_inspection
        and confirmed
        and target_match
        and category_valid
        and item_confident
        and actionable
        and semantic_price_ok
        and heat >= semantic_minimum_heat
        and heat < archive_threshold
        and not profile.auth_sensitive
        and not generic_or_junk
        and not hard_veto
        and not homage_or_reference
    ):
        paths.append("semantic_closet_inspection")
        reasons.append("bounded actionable semantic closet inspection")

    if (
        spec.query_layer == "broad"
        and confirmed
        and target_match
        and actionable
        and actionable_price is not None
        and actionable_price <= identity_recovery_max
        and (not item_confident or not category_valid)
        and not profile.auth_sensitive
        and not generic_or_junk
        and not hard_veto
        and not homage_or_reference
    ):
        paths.append("identity_recovery")
        reasons.append("bounded full-title/category recovery for an incomplete brand card")

    buyout = getattr(listing, "buyout_price", None)
    unusually_low = bool(
        buyout is not None
        and (
            buyout <= unknown_absolute_max
            or (
                buyout_fits_context
                and buyout_fraction is not None
                and buyout_fraction <= unknown_max_fraction
            )
        )
    )
    if (
        confirmed
        and target_match
        and category_valid
        and unusually_low
        and actionable
        and not generic_or_junk
        and not hard_veto
        and not homage_or_reference
    ):
        paths.append("unknown_interesting")
        reasons.append("bounded low-price identity exploration")

    priority = float(heat)
    priority += 18 if confirmed else 0
    priority += 8 if target_match else 0
    priority += 8 if item_confident else 0
    priority += 5 if category_valid else 0
    priority += 10 if getattr(listing, "buyout_price", None) is not None else 0
    priority += (
        7
        if evaluation.auction_maturity.auction_type == "open_auction"
        and evaluation.auction_maturity.actionable_price_kind == "current_bid"
        else 0
    )
    priority += 4 if is_market else 0
    priority += min(8, 2 * len(evaluation.directional.piece_signals))
    priority += relationship_adjustment
    dedupe = (getattr(listing, "raw", None) or {}).get("dedupe", {})
    if dedupe.get("lifetime_first_seen"):
        priority += 16
        reasons.append("lifetime-first listing gets bounded inspection priority")
    elif dedupe.get("revisit_reason") == "candidate_rotation":
        priority -= 4
    if in_band and band_fraction is not None:
        priority += max(0.0, 10.0 * (1.0 - min(1.0, band_fraction)))
    if generic_or_junk:
        priority -= 20
    if evaluation.auth_risk.level == "high":
        priority -= 12

    primary = max(paths, key=lambda path: PATH_ORDER[path]) if paths else None
    if severe_auth_veto:
        reject_reason = "severe_auth_veto"
    elif profile.product_scope.target_role in {"style_reference", "contradicted"}:
        reject_reason = "target_identity_mismatch"
    elif profile.product_scope.offer_kind in {
        "packaging_only",
        "media_ephemera",
        "non_fashion",
    }:
        reject_reason = "offer_scope_mismatch"
    elif profile.product_scope.audience in {"womens", "kids"}:
        reject_reason = "category_mismatch"
    elif profile.product_scope.offer_clarity != "clear":
        reject_reason = "partial_offer_ambiguous"
    elif homage_or_reference:
        reject_reason = "homage_or_reference"
    elif not confirmed and heat < archive_threshold:
        reject_reason = "brand_unconfirmed"
    elif is_market and confirmed and not target_match and heat < archive_threshold:
        reject_reason = "query_brand_mismatch"
    elif not category_valid and heat < archive_threshold:
        reject_reason = "category_unresolved"
    elif generic_or_junk and heat < archive_threshold:
        reject_reason = "generic_or_junk"
    elif (
        spec.strategy_type in {"market_ending", "market_closing"}
        and evaluation.auction_maturity.auction_type == "open_auction"
        and evaluation.auction_maturity.actionable_price_kind != "current_bid"
        and heat < archive_threshold
    ):
        reject_reason = "immature_auction"
    else:
        reject_reason = "insufficient_card_evidence"

    if contextual_price is not None and in_band:
        reasons.append(f"discovery price observed in configured band ({contextual_price} JPY)")
    # This is an allocation preference among already-eligible cards, not a new
    # eligibility path or evidence that a cheap item has resale value.
    value_inspection_candidate = bool(
        paths
        and confirmed
        and target_match
        and category_valid
        and item_confident
        and profile.item_type
        and profile.item_type.value
        in {
            "t_shirt", "shirt", "hoodie", "knitwear", "denim", "trousers",
            "jacket", "coat", "footwear",
        }
        and actionable
        and actionable_price is not None
        and 0 < actionable_price <= int(
            config.get("value_inspection_max_actionable_jpy", 10_000)
        )
        and not profile.auth_sensitive
        and not generic_or_junk
        and not has_structurally_generic_product(profile, listing.title_original or "")
        and not hard_veto
        and not homage_or_reference
    )
    return StageAAssessment(
        listing_id=str(listing.source_listing_id),
        eligible_paths=tuple(dict.fromkeys(paths)),
        primary_path=primary,
        inspection_score=priority,
        brand_confirmed=confirmed,
        detected_family=family,
        target_brand_match=target_match,
        category_valid=category_valid,
        item_type=profile.item_type.value if profile.item_type else None,
        item_type_confident=item_confident,
        actionable_price=actionable,
        price_in_discovery_band=in_band,
        discovery_price_fraction=band_fraction,
        actionable_buyout_fraction=buyout_fraction,
        piece_relationship=relationship,
        hard_veto=hard_veto,
        reject_reason=None if paths and not hard_veto else reject_reason,
        reasons=tuple(reasons),
        value_inspection_candidate=value_inspection_candidate,
    )


def select_stage_a_candidates(
    assessments: list[StageAAssessment],
    spec: QuerySpec,
    settings: dict[str, Any],
) -> tuple[list[StageAAssessment], list[StageAAssessment]]:
    """Rank eligible cards and apply deterministic, configurable detail-fetch budgets."""
    config = settings.get("stage_a", {})
    eligible = [assessment for assessment in assessments if assessment.eligible]
    overall_cap = max(1, int(config.get("max_candidates_per_query", 6)))
    cap = overall_cap
    if spec.query_layer == "market_structure":
        market_cap = max(1, int(config.get("max_market_candidates_per_query", 3)))
        rate = max(0.0, min(1.0, float(config.get("max_market_candidate_rate", 0.03))))
        rate_cap = max(1, math.floor(len(assessments) * rate)) if assessments else 0
        cap = min(cap, market_cap, rate_cap)
    configured_path_caps = {
        str(path): max(0, int(value)) for path, value in config.get("path_caps", {}).items()
    }
    path_caps = {
        "unknown_interesting": 1,
        "silver_jewelry": 2,
        "semantic_closet_inspection": 1,
        "identity_recovery": 1,
        **configured_path_caps,
    }
    ranked = sorted(
        eligible,
        key=lambda assessment: (
            -assessment.inspection_score,
            -PATH_ORDER.get(assessment.primary_path or "", 0),
            assessment.listing_id,
        ),
    )
    selected: list[StageAAssessment] = []
    capped: list[StageAAssessment] = []
    path_counts: dict[str, int] = {}
    for assessment in ranked:
        path = assessment.primary_path or "unknown_interesting"
        path_cap = path_caps.get(path)
        if len(selected) >= cap or (path_cap is not None and path_counts.get(path, 0) >= path_cap):
            capped.append(assessment)
            continue
        selected.append(assessment)
        path_counts[path] = path_counts.get(path, 0) + 1

    reserve = max(0, min(1, int(config.get("value_inspection_reserved_slots", 1))))
    if reserve and selected and not any(item.value_inspection_candidate for item in selected):
        for candidate in ranked:
            if not candidate.value_inspection_candidate or candidate in selected:
                continue
            path = candidate.primary_path or "unknown_interesting"
            path_cap = path_caps.get(path)
            if path_cap == 0:
                continue
            # Preserve a saturated path budget by replacing within that path.
            # Otherwise the lowest-ranked selected card can yield one slot.
            compatible = [
                item
                for item in reversed(selected)
                if path_cap is None
                or path_counts.get(path, 0) < path_cap
                or (item.primary_path or "unknown_interesting") == path
            ]
            if not compatible:
                continue
            displaced = compatible[0]
            selected = [item for item in selected if item != displaced] + [candidate]
            selected_ids = {item.listing_id for item in selected}
            selected = [item for item in ranked if item.listing_id in selected_ids]
            capped = [item for item in ranked if item.listing_id not in selected_ids]
            break
    return selected, capped
