from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .fashion import FashionProfile, enrich_title
from .models import Comp
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
class ListingEvaluation:
    profile: FashionProfile
    desirability: DesirabilityResult
    comps: CompResult
    economics: EconomicsResult
    auth_risk: AuthRiskResult


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
    generic = [signal.value for signal in profile.negative_signals if signal.value.startswith("generic:") or "basic" in signal.value or "logo tee" in signal.value]
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
    if condition and any(term in condition.lower() for term in ("poor", "junk", "ジャンク")):
        score -= 15
        warnings.append("poor-condition wording")
    if profile.brand and not (profile.era or profile.collection or distinct_signals):
        warnings.append("brand match without piece-specific desirability evidence")
    return DesirabilityResult(max(0, min(100, score)), reasons, warnings)


def _comp_similarity(profile: FashionProfile, comp: Comp) -> float:
    comp_profile = enrich_title(comp.title)
    if not profile.brand or not comp_profile.brand or profile.brand.value != comp_profile.brand.value:
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
    return max(0.0, min(1.0, score * max(0.0, min(1.0, comp.similarity))))


def evaluate_comps(profile: FashionProfile, comps: Iterable[Comp]) -> CompResult:
    allowed_sources = {"grailed_sold", "yahoo_ended", "ebay_sold", "local_sold"}
    matched: list[Comp] = []
    for comp in comps:
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
            )
        )
    if not matched:
        return CompResult(None, None, None, "none", [], "no sufficiently similar sold comps")
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
        points += 80
        reasons.append("counterfeit-associated seller wording")
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
    if raw.get("seller_feedback_percent", 100) < 95:
        points += 25
        reasons.append("seller feedback below configured baseline")
    if points >= 80:
        level = "do_not_touch"
    elif points >= 50:
        level = "high"
    elif points >= 25:
        level = "medium"
    else:
        level = "low"
    return AuthRiskResult(level, reasons or ["no explicit risk signal in available fields"])


def evaluate_listing(listing: Any, comps: Iterable[Comp] = (), settings: dict[str, Any] | None = None) -> ListingEvaluation:
    profile = enrich_title(listing.title_original, getattr(listing, "brand", None))
    desirability = score_desirability(profile, listing.title_original, getattr(listing, "condition", None))
    comp_result = evaluate_comps(profile, comps)
    economics = evaluate_economics(getattr(listing, "buyout_price", None) or listing.current_price, comp_result, settings)
    auth = assess_auth_risk(profile, listing.title_original, listing.current_price, getattr(listing, "raw", None))
    return ListingEvaluation(profile, desirability, comp_result, economics, auth)
