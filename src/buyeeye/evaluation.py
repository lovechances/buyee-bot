from __future__ import annotations

import re
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .fashion import FashionProfile, enrich_title, is_generic_signal
from .models import Comp
from .normalize import normalize_text
from .valuation import estimate_landed_cost_jpy


@dataclass
class DesirabilityResult:
    score: int
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class CompResult:
    low_jpy: int | None
    median_jpy: int | None
    high_jpy: int | None
    confidence: str
    matches: list[Comp] = field(default_factory=list)
    reason: str = ""
    candidate_count: int = 0
    rejected_count: int = 0
    queries: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


@dataclass
class EconomicsResult:
    landed_cost_jpy: int | None
    resale_median_jpy: int | None
    estimated_net_resale_jpy: int | None
    gross_margin_jpy: int | None
    net_margin_jpy: int | None
    multiple: float | None
    opportunity_score: int
    comp_confidence: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class AuthRiskResult:
    level: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class IdentityConfidenceResult:
    score: int
    confidence: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class ExactValuationConfidenceResult:
    confidence: str
    reason: str


@dataclass
class DirectionalOpportunityResult:
    score: int
    confidence: str
    thesis: str | None
    landed_usd: float | None
    us_valuation_state: str
    discovery_price_fraction: float | None
    piece_signals: tuple[str, ...]
    reasons: list[str] = field(default_factory=list)


@dataclass
class AuctionMaturityResult:
    auction_type: str
    level: str
    remaining_minutes: int | None
    price_actionable: bool
    current_bid_provisional: bool
    reason: str


@dataclass
class ListingEvaluation:
    profile: FashionProfile
    desirability: DesirabilityResult
    comps: CompResult
    economics: EconomicsResult
    auth_risk: AuthRiskResult
    auction_maturity: AuctionMaturityResult
    identity: IdentityConfidenceResult
    valuation: ExactValuationConfidenceResult
    directional: DirectionalOpportunityResult
    japan_comps: CompResult | None = None


def assess_auction_maturity(
    listing: Any, settings: dict[str, Any] | None = None
) -> AuctionMaturityResult:
    if getattr(listing, "buyout_price", None) is not None:
        return AuctionMaturityResult(
            "fixed_price", "high", None, True, False, "actionable buyout price"
        )
    text = str(getattr(listing, "time_remaining", None) or "")
    matches = re.findall(r"(\d+)\s*(day|hour|minute)s?(?:\(s\))?", text.casefold())
    remaining = (
        sum(
            int(amount) * {"day": 1440, "hour": 60, "minute": 1}[unit]
            for amount, unit in matches
        )
        if matches
        else None
    )
    max_hours = int(
        (settings or {}).get("alert", {}).get("auction_alert_max_remaining_hours", 12)
    )
    if remaining is None or remaining > max_hours * 60:
        level = "low"
    elif remaining <= 6 * 60:
        level = "high"
    else:
        level = "medium"
    bids = getattr(listing, "bid_count", None) or 0
    watches = getattr(listing, "watch_count", None) or 0
    activity = f"{bids} bid(s), {watches} watcher(s)"
    reason = (
        f"auction outside {max_hours}h actionability window; {activity}"
        if level == "low"
        else f"auction within actionability window; {activity}"
    )
    return AuctionMaturityResult(
        "open_auction", level, remaining, level in {"medium", "high"}, True, reason
    )


_DIRECTIONAL_DESIGN_TERMS = (
    "all-over",
    "all over",
    "aids",
    "applique",
    "appliqué",
    "asymmetric",
    "asymmetrical",
    "back logo",
    "back print",
    "bondage",
    "deconstructed",
    "destroy",
    "distressed",
    "embroidered",
    "embroidery",
    "gradient",
    "graphic",
    "jacquard",
    "one button",
    "1b",
    "paneled",
    "panelled",
    "patchwork",
    "painted",
    "patterned",
    "processed",
    "reconstruction",
    "tailored",
    "runway",
    "single stitch",
    "skull",
    "アップリケ",
    "デストロイ",
    "エイズ",
    "ジャカード",
    "ジャガード",
    "グラデーション",
    "パネル",
    "パッチワーク",
    "ペンキ",
    "ボンテージ",
    "スカル",
    "再構築",
    "刺繍",
    "加工",
    "ドクロ",
    "切替",
    "変形",
    "総柄",
    "シングルステッチ",
)

# These terms are useful search/taxonomy context, but they do not establish
# that the specific garment is substantively interesting. In particular, a
# seller writing "archive" or naming a famous band cannot turn a plain basic
# into an asymmetric-value alert.
_DIRECTIONAL_CONTEXT_ONLY_SIGNALS = {
    "archive",
    "sport",
    "nirvana",
    "smashing pumpkins",
    "marilyn manson",
    "sonic youth",
    "nine inch nails",
}

_VINTAGE_BAND_PRODUCTION_TERMS = (
    "70s",
    "80s",
    "90s",
    "single stitch",
    "シングルステッチ",
    "copyright",
    "コピーライト",
    "made in usa",
    "screen stars",
    "brockum",
    "giant tag",
    "当時物",
)

_CONDITION_EVIDENCE_TERMS = (
    "new with tags",
    "nwt",
    "deadstock",
    "unworn",
    "unused",
    "新品未使用",
    "未使用品",
    "新品",
    "タグ付き",
    "タグ付",
    "デッドストック",
)

_GENERIC_ARCHIVE_SHORTHAND = re.compile(
    r"(?i)(?:\d{4}|\d{2}(?:ss|aw|fw)|(?:ss|aw|fw)\d{2})"
)

_EXPLICIT_NON_MENSWEAR_TERMS = (
    "women's",
    "womens",
    "women fashion",
    "women's fashion",
    "ladies",
    "レディース",
    "女性用",
)

_POOR_CONDITION_TERMS = (
    "bad condition",
    "poor condition",
    "heavily damaged",
    "junk",
    "ジャンク",
    "全体的に状態が悪い",
    "状態が悪い",
)


def _phrase_present(text: str, phrase: str) -> bool:
    lowered = text.casefold()
    needle = phrase.casefold()
    if any(character.isascii() and character.isalnum() for character in needle):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
                lowered,
                flags=re.IGNORECASE,
            )
        )
    return needle in lowered


def _piece_signals(profile: FashionProfile, evidence_text: str) -> tuple[str, ...]:
    signals = {
        term for term in _DIRECTIONAL_DESIGN_TERMS if _phrase_present(evidence_text, term)
    }
    signals.update(
        signal.value
        for signal in profile.positive_signals
        if signal.value.casefold() not in _DIRECTIONAL_CONTEXT_ONLY_SIGNALS
    )
    return tuple(sorted(signals))


def _has_vintage_band_production_evidence(evidence_text: str) -> bool:
    # One seller-supplied construction phrase (especially "single stitch")
    # is too weak to distinguish an actual period tee from a modern cut-off.
    # Require two independent age/production observations before treating a
    # band subject as vintage sourcing evidence.
    return (
        sum(
            _phrase_present(evidence_text, term)
            for term in _VINTAGE_BAND_PRODUCTION_TERMS
        )
        >= 2
    )


def has_specific_archive_context(profile: FashionProfile) -> bool:
    """Return archive evidence stronger than a bare year or season shorthand."""
    return any(
        evidence
        and not _GENERIC_ARCHIVE_SHORTHAND.fullmatch(
            normalize_text(evidence.matched_term).replace(" ", "")
        )
        for evidence in (profile.era, profile.collection)
    )


def _has_favorable_condition_evidence(condition: str | None, evidence_text: str) -> bool:
    condition_text = normalize_text(condition or "").casefold()
    if condition_text in {"new", "unused", "unworn", "new with tags", "nwt"}:
        return True
    combined = " ".join(value for value in (condition_text, evidence_text) if value)
    return any(_phrase_present(combined, term) for term in _CONDITION_EVIDENCE_TERMS)


def _has_poor_condition_evidence(condition: str | None) -> bool:
    normalized = normalize_text(condition or "").casefold()
    return any(_phrase_present(normalized, term) for term in _POOR_CONDITION_TERMS)


def has_explicit_non_menswear_evidence(listing: Any) -> bool:
    """Use richer Stage B category evidence to reject explicit women's inventory."""
    observed = ((getattr(listing, "raw", None) or {}).get("stage_b") or {}).get(
        "observed", {}
    )
    hierarchy = observed.get("category_hierarchy", [])
    text = normalize_text(
        " ".join(
            [
                str(getattr(listing, "title_original", "") or ""),
                *(str(value) for value in hierarchy if value),
            ]
        )
    ).casefold()
    return any(_phrase_present(text, term) for term in _EXPLICIT_NON_MENSWEAR_TERMS)


def has_specific_design_evidence(piece_signals: Iterable[str]) -> bool:
    """Distinguish construction/motif evidence from collection-name repetition."""
    design_terms = {normalize_text(term).casefold() for term in _DIRECTIONAL_DESIGN_TERMS}
    return any(normalize_text(signal).casefold() in design_terms for signal in piece_signals)


def assess_identity_confidence(
    profile: FashionProfile,
    stage_b_record: dict[str, Any] | None = None,
    identity_conflict: str | None = None,
) -> IdentityConfidenceResult:
    """Measure what BuyEye knows about identity, independently of price or comps."""
    stage_b_record = stage_b_record or {}
    score = 0
    reasons: list[str] = []
    if profile.brand and profile.brand.provenance.startswith("taxonomy.brands."):
        score += 45
        reasons.append("brand matched a configured taxonomy alias")
    elif profile.brand:
        score += 18
        reasons.append("brand available only as marketplace metadata")
    if profile.item_type:
        score += 25 if profile.item_type.confidence >= 0.8 else 15
        reasons.append(f"item type identified as {profile.item_type.value}")
    if profile.category:
        score += 10
        reasons.append(f"fashion category identified as {profile.category.value}")
    if stage_b_record.get("status") == "enriched":
        score += 10
        reasons.append("detail page enriched successfully")
    if profile.relationship and profile.relationship.value != "uncertain":
        score += 5
        reasons.append(f"piece relationship classified as {profile.relationship.value}")
    if profile.collection or profile.era or profile.season or profile.material:
        score += 5
        reasons.append("piece-level season, era, collection, or material evidence")
    if stage_b_record.get("status") == "failed":
        # A rich card can otherwise reach HIGH without the detail page that was
        # supposed to confirm it. Keep card evidence usable for archive-watch
        # routing, but do not let a failed enrichment create directional value.
        score = min(score, 74)
        reasons.append("detail enrichment failed; identity confidence capped")
    if identity_conflict:
        score = min(score, 24)
        reasons.append(identity_conflict)
    score = min(100, score)
    confidence = "high" if score >= 75 else "medium" if score >= 50 else "low"
    return IdentityConfidenceResult(score, confidence, reasons)


_EXPLICIT_NON_TARGET_DETAIL_BRANDS = {
    "gu",
    "uniqlo",
    "ジーユー",
    "ユニクロ",
}
_NON_TARGET_DETAIL_BRAND_KEYS = {
    "ジーユー": "gu",
    "ユニクロ": "uniqlo",
}


def _detail_brand_conflict(
    stage_b_record: dict[str, Any],
    identity_evidence_text: str,
) -> str | None:
    """Detect only strong detail-brand contradictions; unknown labels stay advisory."""
    if stage_b_record.get("status") != "enriched":
        return None
    observed = str((stage_b_record.get("observed") or {}).get("brand_observed") or "").strip()
    if not observed:
        return None
    identity_profile = enrich_title(identity_evidence_text)
    if not identity_profile.brand:
        return None
    observed_profile = enrich_title(observed)
    if observed_profile.brand:
        if observed_profile.brand.value != identity_profile.brand.value:
            return (
                f"detail brand {observed!r} contradicts "
                f"{identity_profile.brand.value!r} identity"
            )
        return None

    normalized_observed = " ".join(observed.casefold().split())
    if normalized_observed not in _EXPLICIT_NON_TARGET_DETAIL_BRANDS:
        return None
    observed_participant = _NON_TARGET_DETAIL_BRAND_KEYS.get(
        normalized_observed, normalized_observed
    )
    relationship = identity_profile.relationship
    if (
        relationship
        and relationship.value == "collaboration"
        and observed_participant in relationship.participants
    ):
        return None
    return (
        f"detail brand {observed!r} contradicts "
        f"{identity_profile.brand.value!r} identity"
    )


def assess_exact_valuation_confidence(comps: CompResult) -> ExactValuationConfidenceResult:
    """Expose exact resale confidence without borrowing from directional evidence."""
    confidence = "unavailable" if comps.confidence == "none" else comps.confidence
    return ExactValuationConfidenceResult(confidence, comps.reason)


def _discovery_price_fraction(listing: Any) -> float | None:
    """Return exploration-band position; this is context, never an FMV estimate."""
    discovery = (getattr(listing, "raw", None) or {}).get("discovery", {})
    if discovery.get("query_layer") != "market_structure":
        return None
    filters = discovery.get("search_filters", {})
    if not isinstance(filters, dict):
        return None
    buyout = getattr(listing, "buyout_price", None)
    if buyout is not None:
        maximum = filters.get("max_buyout_price_jpy") or filters.get(
            "max_current_price_jpy"
        )
        price = buyout
    else:
        maximum = filters.get("max_current_price_jpy")
        price = getattr(listing, "current_price", None)
    try:
        price_value = int(price)
        maximum_value = int(maximum)
    except (TypeError, ValueError):
        return None
    if price_value <= 0 or maximum_value <= 0:
        return None
    return round(price_value / maximum_value, 4)


def assess_directional_opportunity(
    profile: FashionProfile,
    desirability: DesirabilityResult,
    economics: EconomicsResult,
    auth: AuthRiskResult,
    listing: Any,
    identity: IdentityConfidenceResult,
    valuation: ExactValuationConfidenceResult,
    evidence_text: str,
    settings: dict[str, Any] | None = None,
) -> DirectionalOpportunityResult:
    """Assess bargain direction without manufacturing an exact resale estimate."""
    config = (settings or {}).get("directional_opportunity", {})
    fx = float((settings or {}).get("pricing", {}).get("fx_jpy_to_usd", 0.00675))
    landed_usd = round(economics.landed_cost_jpy * fx, 2) if economics.landed_cost_jpy else None
    max_landed = float(config.get("max_landed_usd", 65))
    exceptional_landed = float(config.get("exceptional_landed_usd", 30))
    minimum_score = int(config.get("minimum_score", 72))
    archive_context_fraction = float(config.get("archive_context_max_fraction", 0.85))
    ordinary_context_fraction = float(config.get("ordinary_context_max_fraction", 0.45))
    exceptional_actionable_price = int(
        config.get("exceptional_actionable_price_jpy", 10_000)
    )
    exceptional_actionable_minimum_score = int(
        config.get("exceptional_actionable_minimum_score", 62)
    )
    favorable_condition_bonus = int(config.get("favorable_condition_bonus", 6))
    relationship = profile.relationship.value if profile.relationship else "uncertain"
    relationship_provenance = profile.relationship.provenance if profile.relationship else ""
    design_signals = _piece_signals(profile, evidence_text)
    # A discovery-band position is useful only when the observed acquisition
    # price is actionable. An early current bid is provisional marketplace
    # state, not directional bargain evidence.
    discovery_fraction = (
        _discovery_price_fraction(listing)
        if economics.landed_cost_jpy is not None
        else None
    )
    generic = any(
        is_generic_signal(signal, profile.relationship)
        for signal in profile.negative_signals
    )
    irrelevant = any(
        signal.value.startswith("irrelevant:") for signal in profile.negative_signals
    )
    poor_condition = _has_poor_condition_evidence(getattr(listing, "condition", None))

    score = {"high": 30, "medium": 18, "low": 8}[identity.confidence]
    reasons = [f"{identity.confidence}-confidence fashion identity"]
    score += {
        "core": 12,
        "collaboration": 6,
        "diffusion": -8,
        "homage_reference": -30,
        "uncertain": -8,
    }.get(relationship, -8)
    reasons.append(f"{relationship} relationship")
    if profile.collection or profile.era:
        score += 18
        reasons.append("collection or designer-era evidence")
    if profile.season:
        score += 5
        reasons.append("explicit season evidence")
    if profile.material and profile.material.value in {"silver", "leather", "mohair", "cashmere"}:
        score += 8
        reasons.append(f"notable {profile.material.value} material")
    if design_signals:
        score += min(18, 6 * len(design_signals))
        reasons.append("piece-specific design evidence: " + ", ".join(design_signals[:3]))
    if desirability.score >= 55:
        score += 10
    elif desirability.score >= 40:
        score += 6
    elif desirability.score >= 25:
        score += 3

    if landed_usd is not None and landed_usd <= exceptional_landed:
        score += 25
        reasons.append(f"exceptionally low absolute downside (~${landed_usd:.0f} landed)")
    elif landed_usd is not None and landed_usd <= max_landed:
        score += 14
        reasons.append(f"low absolute downside (~${landed_usd:.0f} landed)")
    elif landed_usd is not None and landed_usd <= 120:
        score += 7
        reasons.append(f"bounded absolute acquisition cost (~${landed_usd:.0f} landed)")

    if discovery_fraction is not None:
        if discovery_fraction <= 0.35:
            score += 24
        elif discovery_fraction <= 0.60:
            score += 17
        elif discovery_fraction <= archive_context_fraction:
            score += 9
        else:
            score += 3
        reasons.append(
            f"actionable price is {discovery_fraction:.0%} of its discovery-band ceiling"
        )
    if valuation.confidence in {"medium", "high"}:
        score += 10
        reasons.append("credible U.S. sold evidence")
    if generic:
        score -= 25
        reasons.append("generic/basic detail evidence")
    if irrelevant:
        score = 0
        reasons.append("non-fashion object evidence")
    if poor_condition:
        score -= 12
        reasons.append("explicit poor-condition evidence")
    if relationship_provenance.endswith(".collaboration_low_priority"):
        score -= 18
        reasons.append("mass-market or secondary-brand collaboration context")
    if auth.level == "medium":
        score -= 6
    elif auth.level == "high":
        score -= 18
    elif auth.level == "do_not_touch":
        score = 0

    archive_context = has_specific_archive_context(profile)
    notable_material = bool(
        profile.material
        and profile.material.value in {"silver", "leather", "mohair", "cashmere"}
    )
    strong_collaboration = bool(
        profile.relationship
        and profile.relationship.value == "collaboration"
        and not relationship_provenance.endswith(".collaboration_low_priority")
    )
    substantive = bool(
        archive_context or design_signals or notable_material or strong_collaboration
    )
    quality_floor = bool(
        substantive
        and (
            not generic
            or archive_context
            or notable_material
            or len(design_signals) >= 2
        )
    )
    is_vintage_band_tee = bool(
        profile.brand and profile.brand.value == "Vintage Band Tee"
    )
    vintage_band_quality = bool(
        is_vintage_band_tee
        and _has_vintage_band_production_evidence(evidence_text)
        and design_signals
        and not generic
    )
    if is_vintage_band_tee:
        substantive = vintage_band_quality
        quality_floor = vintage_band_quality
    favorable_condition = bool(
        substantive
        and quality_floor
        and not generic
        and auth.level != "do_not_touch"
        and not poor_condition
        and _has_favorable_condition_evidence(
            getattr(listing, "condition", None), evidence_text
        )
    )
    if favorable_condition:
        score += favorable_condition_bonus
        reasons.append("favorable new/unworn condition supports an already substantive piece")
    score = max(0, min(100, score))
    contextual_price_ok = bool(
        discovery_fraction is not None
        and discovery_fraction
        <= (archive_context_fraction if archive_context else ordinary_context_fraction)
    )
    absolute_downside_ok = bool(
        landed_usd is not None
        and landed_usd <= max_landed
        and substantive
    )
    actionable_price = getattr(listing, "buyout_price", None)
    if actionable_price is None and economics.landed_cost_jpy is not None:
        actionable_price = getattr(listing, "current_price", None)
    exceptional_acquisition_ok = bool(
        economics.landed_cost_jpy is not None
        and actionable_price is not None
        and 0 < int(actionable_price) <= exceptional_actionable_price
        and substantive
        and quality_floor
        and (
            has_specific_design_evidence(design_signals)
            or notable_material
            or strong_collaboration
        )
        and not poor_condition
    )
    if exceptional_acquisition_ok:
        reasons.append(
            "actionable acquisition price is inside the bounded low-downside threshold"
        )
    score_is_sufficient = bool(
        score >= minimum_score
        or (
            exceptional_acquisition_ok
            and score >= exceptional_actionable_minimum_score
        )
    )
    relationship_ok = relationship in {"core", "collaboration", "diffusion"}
    if is_vintage_band_tee:
        relationship_ok = vintage_band_quality
    if relationship == "diffusion":
        relationship_ok = bool(
            substantive
            and score >= int(config.get("diffusion_minimum_score", 85))
        )
    elif relationship == "collaboration" and relationship_provenance.endswith(
        ".collaboration_low_priority"
    ):
        relationship_ok = bool(
            substantive
            and score >= int(config.get("low_priority_collaboration_minimum_score", 88))
        )
    qualifies = bool(
        economics.landed_cost_jpy is not None
        and identity.confidence == "high"
        and profile.brand
        and profile.item_type
        and profile.category
        and substantive
        and quality_floor
        and relationship_ok
        and auth.level != "do_not_touch"
        and not irrelevant
        and not has_explicit_non_menswear_evidence(listing)
        and score_is_sufficient
        and (contextual_price_ok or absolute_downside_ok or exceptional_acquisition_ok)
    )
    thesis = "asymmetric_value" if qualifies else None
    if economics.landed_cost_jpy is None:
        confidence = "unavailable"
    elif substantive and quality_floor and relationship_ok and not generic:
        confidence = "high" if score >= 82 else "medium" if score >= 60 else "low"
    else:
        confidence = "low"
    valuation_state = (
        "verified_us_sold"
        if valuation.confidence in {"medium", "high"}
        else "us_resale_unverified"
    )
    if not quality_floor:
        reasons.append("insufficient substantive piece-quality evidence")
    return DirectionalOpportunityResult(
        score,
        confidence,
        thesis,
        landed_usd,
        valuation_state,
        discovery_fraction,
        design_signals,
        reasons,
    )


def score_desirability(profile: FashionProfile, title: str, condition: str | None = None) -> DesirabilityResult:
    score = 5 if profile.brand else 0
    reasons: list[str] = []
    warnings: list[str] = []
    if profile.designer:
        score += 8
        reasons.append(f"designer context: {profile.designer.value}")
    if profile.era:
        score += 18
        reasons.append(f"recognized era: {profile.era.value}")
    if profile.collection:
        score += 24
        reasons.append(f"recognized collection: {profile.collection.value}")
    if profile.season:
        score += 5
        reasons.append(f"explicit season: {profile.season.value}")
    if profile.item_type:
        score += 7
        reasons.append(f"identified item type: {profile.item_type.value}")
    if profile.material and profile.material.value in {"silver", "leather", "mohair", "cashmere"}:
        score += 10
        reasons.append(f"notable material: {profile.material.value}")
    if profile.collaboration:
        score += 8
        reasons.append("explicit collaboration")
    distinct_signals = {signal.value for signal in profile.positive_signals}
    score += min(30, len(distinct_signals) * 10)
    reasons.extend(f"strong signal: {signal}" for signal in sorted(distinct_signals))
    generic = [
        signal.value
        for signal in profile.negative_signals
        if is_generic_signal(signal, profile.relationship)
    ]
    counterfeit = [signal.value for signal in profile.negative_signals if signal.value.startswith("counterfeit:")]
    irrelevant = [signal.value for signal in profile.negative_signals if signal.value.startswith("irrelevant:")]
    if generic:
        score -= min(35, 15 * len(generic))
        warnings.append("generic/basic product language")
    if counterfeit:
        score -= 35
        warnings.append("counterfeit-associated wording")
    if irrelevant:
        score = 0
        warnings.append("non-fashion or irrelevant item language")
    if _has_poor_condition_evidence(condition):
        score -= 15
        warnings.append("poor-condition wording")
    if profile.brand and not (profile.era or profile.collection or distinct_signals):
        warnings.append("brand match without piece-specific desirability evidence")
    return DesirabilityResult(max(0, min(100, score)), reasons, warnings)


def _comp_similarity(profile: FashionProfile, comp: Comp) -> float:
    comp_profile = enrich_title(comp.title)
    if not profile.brand or not comp_profile.brand or profile.brand.value != comp_profile.brand.value:
        return 0.0
    target_relationship = profile.relationship.value if profile.relationship else "uncertain"
    comp_relationship = (
        comp_profile.relationship.value if comp_profile.relationship else "uncertain"
    )
    # A shared parent-house name does not make mainline, diffusion, and
    # collaboration transactions interchangeable. This guard prevents, for
    # example, McQ from inheriting Alexander McQueen mainline sold values.
    if "homage_reference" in {target_relationship, comp_relationship}:
        return 0.0
    if "uncertain" in {target_relationship, comp_relationship}:
        return 0.0
    if (
        {target_relationship, comp_relationship} & {"diffusion", "collaboration"}
        and target_relationship != comp_relationship
    ):
        return 0.0
    if target_relationship == "collaboration":
        target_participants = profile.relationship.participants
        comp_participants = comp_profile.relationship.participants
        # A house can collaborate with partners spanning radically different
        # product and market tiers. Only the same explicitly evidenced pair is
        # comparable; absent/ambiguous partner evidence is not filled in.
        if (
            not target_participants
            or not comp_participants
            or target_participants != comp_participants
        ):
            return 0.0
    score = 0.35
    if profile.category and comp_profile.category and profile.category.value == comp_profile.category.value:
        score += 0.2
    elif profile.category and comp_profile.category:
        return 0.0
    if profile.item_type and comp_profile.item_type and profile.item_type.value == comp_profile.item_type.value:
        score += 0.2
    elif profile.item_type and comp_profile.item_type:
        score -= 0.15
    if profile.collection and comp_profile.collection and profile.collection.value == comp_profile.collection.value:
        score += 0.15
    if profile.material and comp_profile.material and profile.material.value == comp_profile.material.value:
        score += 0.1
    target_signals = {signal.value for signal in profile.positive_signals}
    comp_signals = {signal.value for signal in comp_profile.positive_signals}
    if target_signals & comp_signals:
        score += 0.15
    return max(0.0, min(1.0, score * max(0.0, min(1.0, comp.similarity))))


def evaluate_comps(profile: FashionProfile, comps: Iterable[Comp]) -> CompResult:
    candidates = list(comps)
    allowed_sources = {"grailed_sold", "yahoo_ended", "ebay_sold", "local_sold"}
    matched: list[Comp] = []
    for comp in candidates:
        if not comp.is_sold or comp.source not in allowed_sources or not comp.price_jpy:
            continue
        similarity = _comp_similarity(profile, comp)
        if similarity < 0.45:
            continue
        matched.append(
            Comp(
                source=comp.source,
                title=comp.title,
                price_jpy=comp.price_jpy,
                similarity=similarity,
                recency_days=comp.recency_days,
                category=comp.category,
                is_sold=True,
                provenance=comp.provenance,
                source_listing_id=comp.source_listing_id,
                url=comp.url,
                query=comp.query,
                sold_at=comp.sold_at,
                original_price=comp.original_price,
                original_currency=comp.original_currency,
                shipping_original=comp.shipping_original,
                condition=comp.condition,
                size=comp.size,
            )
        )
    if not matched:
        return CompResult(
            None,
            None,
            None,
            "none",
            [],
            "no sufficiently similar sold comps",
            len(candidates),
            len(candidates),
            sorted({comp.query for comp in candidates if comp.query}),
            sorted({comp.source for comp in candidates}),
        )
    prices = sorted(int(comp.price_jpy) for comp in matched if comp.price_jpy)
    if len(prices) >= 5:
        trim = max(1, len(prices) // 10)
        prices = prices[trim:-trim] or prices
    confidence = "high" if len(prices) >= 5 else "medium" if len(prices) >= 3 else "low"
    if statistics.mean(comp.similarity for comp in matched) < 0.65:
        confidence = "low"
    return CompResult(
        low_jpy=prices[max(0, len(prices) // 4 - 1)],
        median_jpy=int(statistics.median(prices)),
        high_jpy=prices[min(len(prices) - 1, (3 * len(prices)) // 4)],
        confidence=confidence,
        matches=sorted(matched, key=lambda comp: comp.similarity, reverse=True),
        reason=f"{len(matched)} piece-aware sold comp(s)",
        candidate_count=len(candidates),
        rejected_count=len(candidates) - len(matched),
        queries=sorted({comp.query for comp in candidates if comp.query}),
        sources=sorted({comp.source for comp in candidates}),
    )


def evaluate_economics(price_jpy: int | None, comps: CompResult, settings: dict[str, Any] | None = None) -> EconomicsResult:
    if not price_jpy or price_jpy <= 0:
        return EconomicsResult(None, comps.median_jpy, None, None, None, None, 0, comps.confidence, ["invalid or missing acquisition price"])
    landed = int(estimate_landed_cost_jpy(price_jpy, settings)["landed_jpy"])
    if comps.median_jpy is None:
        return EconomicsResult(landed, None, None, None, None, None, 0, "none", ["no reliable sold-market valuation"])
    resale_fee = float((settings or {}).get("pricing", {}).get("resale_fee_percent", 0.13))
    net_resale = int(comps.median_jpy * (1 - resale_fee))
    gross_margin = comps.median_jpy - landed
    net_margin = net_resale - landed
    multiple = round(comps.median_jpy / landed, 2) if landed else None
    score = 0
    if comps.confidence in {"medium", "high"}:
        score += 20
    if net_margin >= 30_000:
        score += 35
    elif net_margin >= 15_000:
        score += 25
    elif net_margin > 0:
        score += 10
    if multiple and multiple >= 2.0:
        score += 35
    elif multiple and multiple >= 1.5:
        score += 20
    warnings = [] if net_margin > 0 else ["estimated net margin is not positive"]
    return EconomicsResult(landed, comps.median_jpy, net_resale, gross_margin, net_margin, multiple, min(100, score), comps.confidence, warnings)


def assess_auth_risk(profile: FashionProfile, title: str, price_jpy: int | None, raw: dict[str, Any] | None = None) -> AuthRiskResult:
    raw = raw or {}
    reasons: list[str] = []
    points = 0
    counterfeit = [signal for signal in profile.negative_signals if signal.value.startswith("counterfeit:")]
    if counterfeit:
        points += 100
        reasons.append("counterfeit-associated seller wording")
    if raw.get("provenance_inconsistency"):
        points += 100
        reasons.append("materially contradictory identity evidence")
    if profile.auth_sensitive:
        points += 15
        reasons.append("frequently counterfeited brand/category")
        if price_jpy is not None and price_jpy < 5_000:
            points += 30
            reasons.append("implausibly low price for auth-sensitive item")
        if not raw.get("seller_feedback_count"):
            points += 10
            reasons.append("seller history unavailable")
        if not raw.get("detail_photo_count"):
            points += 10
            reasons.append("detail-photo coverage unavailable")
    feedback_percent = raw.get("seller_feedback_percent")
    feedback_count = raw.get("seller_feedback_count") or 0
    if feedback_percent is not None and feedback_percent < 95:
        if feedback_percent < 80 or feedback_count < 10:
            points += 25
        elif feedback_percent < 90:
            points += 15
        else:
            points += 8
        reasons.append(
            f"seller feedback {feedback_percent:g}% across {feedback_count:,} ratings"
        )
    if counterfeit or raw.get("provenance_inconsistency"):
        level = "do_not_touch"
    elif points >= 45:
        level = "high"
    elif points >= 15:
        level = "medium"
    else:
        level = "low"
    return AuthRiskResult(level, reasons or ["no explicit risk signal in available fields"])


def evaluate_listing(
    listing: Any,
    comps: Iterable[Comp] = (),
    settings: dict[str, Any] | None = None,
    japan_comps: Iterable[Comp] = (),
) -> ListingEvaluation:
    supplied_comps = list(comps)
    supplied_japan_comps = list(japan_comps)
    # Keep source-market evidence structurally separate even if an internal
    # caller accidentally supplies Yahoo-ended observations through `comps`.
    # They remain useful Japan evidence, but can never become U.S. FMV or
    # resale economics.
    us_comp_candidates = [comp for comp in supplied_comps if comp.source != "yahoo_ended"]
    japan_comp_candidates = [
        *supplied_japan_comps,
        *(comp for comp in supplied_comps if comp.source == "yahoo_ended"),
    ]
    stage_b_record = (getattr(listing, "raw", None) or {}).get("stage_b", {})
    stage_b = stage_b_record.get("observed", {})
    identity_evidence_text = " ".join(
        str(value)
        for value in (
            listing.title_original,
            stage_b.get("description_original"),
            stage_b.get("item_type_observed"),
            stage_b.get("material_original"),
            stage_b.get("size_original"),
            " ".join(stage_b.get("category_hierarchy", [])),
        )
        if value
    )
    evidence_text = " ".join(
        value
        for value in (
            identity_evidence_text,
            str(stage_b.get("brand_observed") or "").strip(),
        )
        if value
    )
    identity_conflict = _detail_brand_conflict(stage_b_record, identity_evidence_text)
    profile = enrich_title(evidence_text, getattr(listing, "brand", None))
    desirability = score_desirability(profile, evidence_text, getattr(listing, "condition", None))
    comp_result = evaluate_comps(profile, us_comp_candidates)
    if stage_b_record.get("status") == "failed" and comp_result.confidence in {"medium", "high"}:
        comp_result.confidence = "low"
        comp_result.reason = f"{comp_result.reason}; Stage B identity unavailable"
    maturity = assess_auction_maturity(listing, settings)
    economics = evaluate_economics(
        getattr(listing, "buyout_price", None) or listing.current_price,
        comp_result,
        settings,
    )
    if not maturity.price_actionable:
        economics = EconomicsResult(
            None,
            comp_result.median_jpy,
            None,
            None,
            None,
            None,
            0,
            comp_result.confidence,
            ["current bid is provisional; auction is outside the maturity window"],
        )
    auth_price_jpy = getattr(listing, "buyout_price", None)
    if auth_price_jpy is None and maturity.price_actionable:
        auth_price_jpy = getattr(listing, "current_price", None)
    auth_raw = dict(getattr(listing, "raw", None) or {})
    if identity_conflict:
        auth_raw["provenance_inconsistency"] = True
    auth = assess_auth_risk(
        profile,
        listing.title_original,
        auth_price_jpy,
        auth_raw,
    )
    identity = assess_identity_confidence(profile, stage_b_record, identity_conflict)
    valuation = assess_exact_valuation_confidence(comp_result)
    directional = assess_directional_opportunity(
        profile,
        desirability,
        economics,
        auth,
        listing,
        identity,
        valuation,
        evidence_text,
        settings,
    )
    japan_result = evaluate_comps(profile, japan_comp_candidates)
    return ListingEvaluation(
        profile=profile,
        desirability=desirability,
        comps=comp_result,
        economics=economics,
        auth_risk=auth,
        auction_maturity=maturity,
        identity=identity,
        valuation=valuation,
        directional=directional,
        japan_comps=japan_result,
    )
