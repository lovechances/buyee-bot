"""Delivery attention, separate from identity, value, risk and primary routing."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import select

from .db import AlertSendLedger, get_session
from .evaluation import (
    ListingEvaluation,
    _design_concepts,
    has_grail_design_evidence,
    has_iconic_piece_context,
    has_named_collection_context,
)


@dataclass(frozen=True)
class AttentionDecision:
    role: str
    delivery: str
    reason: str

    def observation(self) -> dict[str, str]:
        return asdict(self)


def offer_comparison_context(listing: Any, evaluation: ListingEvaluation) -> dict[str, Any] | None:
    """Narrow observed substitute families, not a physical-stock/duplicate claim.

    These two recurring models are demonstrated in the frozen production
    cohort. Other models remain independent until comparison evidence exists.
    Unknown size/condition never proves substitutability.
    """
    profile = evaluation.profile
    if not profile.brand or not profile.item_type or not profile.product_scope.acquisition_attributable:
        return None
    title = str(listing.title_original or "").casefold()
    if profile.brand.value == "Maison Martin Margiela" and profile.item_type.value == "t_shirt" and re.search(r"\baids\b|エイズ", title):
        model = "margiela:aids_tee"
    elif profile.brand.value == "Takahiromiyashita TheSoloist." and "oofos" in title and re.search(r"flip[- ]flop|フリップフロップ", title):
        model = "soloist_oofos:flip_flop"
    else:
        return None
    # Explicit editions/constructions outside this narrow model comparison
    # stay independent. A familiar model name does not erase a useful variant.
    if re.search(r"anniversary|記念|reversible|リバーシブル|limited edition|限定|復刻", title):
        return None
    sleeve = (
        "long" if re.search(r"long[- ]sleeve|長袖", title) else
        "short" if re.search(r"short[- ]sleeve|半袖", title) else None
    )
    size_match = re.search(r"(?i)(size|サイズ|US|UK|EU)\s*[:：]?\s*(xxl|xl|xs|s|m|l|\d{1,2}(?:\.\d+)?)\b", title)
    size = size_match.group(2).upper() if size_match else None
    size_system = size_match.group(1).upper() if size_match and size_match.group(1).upper() in {"US", "UK", "EU"} else "unspecified"
    if not size_match:
        suffix = re.search(r"(?i)(xxl|xl|xs|s|m|l|\d{1,2}(?:\.\d+)?)\s*サイズ", title)
        size = suffix.group(1).upper() if suffix else None
    colors = {
        color for color, pattern in {
            "black": r"\bblack\b|ブラック|黒", "white": r"\bwhite\b|ホワイト|白",
            "grey": r"\bgr[ae]y\b|グレー", "red": r"\bred\b|レッド|赤",
            "blue": r"\bblue\b|ブルー|青", "green": r"\bgreen\b|グリーン|緑",
            "orange": r"\borange\b|オレンジ", "yellow": r"\byellow\b|イエロー|黄",
            "beige": r"\bbeige\b|ベージュ", "silver": r"\bsilver\b|シルバー|銀",
        }.items() if re.search(pattern, title)
    }
    condition = str(getattr(listing, "condition", "") or "").strip().casefold()
    if condition in {"unused", "new", "unworn"}:
        condition = "unworn"
    elif condition in {"no obvious damages/dirt", "no noticeable scratches or stains", "used"}:
        condition = "used"
    else:
        condition = None
    observed = ((listing.raw or {}).get("stage_b") or {}).get("observed") or {}
    metadata_size = str(observed.get("size_original") or "").strip()
    metadata_size = re.sub(r"(?i)^size\s*", "", metadata_size).upper()
    if size and metadata_size and size != metadata_size:
        size = None  # Conflicting sizing does not prove an interchangeable offer.
    kind = evaluation.auction_maturity.actionable_price_kind
    price = listing.buyout_price if kind == "buyout" else listing.current_price if kind == "current_bid" else None
    timing = re.findall(r"(\d+)\s*(day|hour|minute)", str(listing.time_remaining or "").casefold())
    minutes = sum(int(n) * {"day": 1440, "hour": 60, "minute": 1}[unit] for n, unit in timing)
    if not size or not condition or not price or not colors or not minutes:
        return None
    return {"version": 1, "model": model, "size": size, "condition": condition,
            "sleeve": sleeve, "size_system": size_system,
            "subtype": profile.item_subtype.value if profile.item_subtype else None,
            "material": profile.material.value if profile.material else None,
            "price_jpy": price, "price_kind": kind,
            "relationship": profile.relationship.value if profile.relationship else None,
            "era": profile.era.value if profile.era else None,
            "season": profile.season.value if profile.season else None,
            "colors": sorted(colors),
            "comparison_expires_at": (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat(),
            "design": sorted(_design_concepts(evaluation.directional.piece_signals))}


def compare_recent_offers(
    current: dict[str, Any], previous: list[dict[str, Any]],
    *, now: datetime | None = None,
) -> bool:
    """True only for a non-improving, comparable secondary offer."""
    now = now or datetime.now(UTC)
    for prior in previous:
        if not current.get("colors") or not current.get("size") or not current.get("condition"):
            continue
        try:
            expires = datetime.fromisoformat(prior.get("comparison_expires_at") or "")
        except (ValueError, TypeError):
            continue
        if expires.tzinfo is None or expires <= now:
            continue
        if any(current.get(k) != prior.get(k) for k in (
            "version", "model", "size", "condition", "price_kind", "relationship", "era", "season", "colors", "design",
            "sleeve", "subtype", "material", "size_system",
        )):
            continue
        old_price = prior.get("price_jpy")
        if not isinstance(old_price, (int, float)) or old_price <= 0:
            continue
        # Any >5% cheaper offer stays normal. This is interruption policy,
        # never an FMV claim or an opportunity eligibility threshold.
        if current["price_jpy"] >= old_price * 0.95:
            return True
    return False


def assess_substitute_attention(
    listing: Any, evaluation: ListingEvaluation, decision: AttentionDecision,
    path: str | Path, *, now: datetime | None = None,
) -> AttentionDecision:
    context = offer_comparison_context(listing, evaluation)
    if not context or decision.delivery == "quiet" or decision.role == "needs_review":
        return decision
    now = now or datetime.now(UTC)
    with get_session(path) as session:
        rows = session.exec(select(AlertSendLedger).where(
            AlertSendLedger.sent_at >= now - timedelta(hours=72),
            AlertSendLedger.source == listing.source,
            AlertSendLedger.source_listing_id != listing.source_listing_id,
        ).order_by(AlertSendLedger.sent_at.desc()).limit(200)).all()
        previous = []
        for row in rows:
            try:
                value = json.loads(row.offer_context_json or "null")
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict):
                previous.append(value)
    if compare_recent_offers(context, previous, now=now):
        return AttentionDecision("secondary_offer", "quiet",
            "recent comparable model/size/condition offer; no material price or design improvement")
    return decision


def assess_attention(
    evaluation: ListingEvaluation,
    route: str,
    settings: dict[str, Any] | None = None,
) -> AttentionDecision:
    """Quiet a narrow high-cost archive class, never veto an eligible listing.

    Named collection plus one design concept establishes archive context, not
    urgency. Preserve its card/route/history while reserving phone pushes for
    stronger independent piece evidence or actual acquisition value. This is
    intentionally not a taste score or an appraisal of resale value.
    """
    if route == "review":
        return AttentionDecision("needs_review", "normal", "inspectable risk uncertainty")
    if route in {"buyouts", "silver"}:
        return AttentionDecision(
            "actionable_opportunity", "normal", "qualified actionable primary route"
        )
    credible_value = bool(
        evaluation.auction_maturity.price_actionable
        and (
            evaluation.directional.thesis == "asymmetric_value"
            or (
                evaluation.economics.opportunity_score >= 70
                and evaluation.economics.comp_confidence in {"medium", "high"}
            )
        )
    )
    if route == "ending" and credible_value:
        return AttentionDecision("actionable_opportunity", "normal", "qualified value thesis approaching close")
    config = (settings or {}).get("alert", {})
    high_cost = float(config.get("unverified_grail_high_ticket_usd", 300))
    landed = evaluation.directional.landed_usd
    independent_design = _design_concepts(
        signal for signal in evaluation.directional.piece_signals
        if has_grail_design_evidence((signal,))
    )
    strong_piece = has_iconic_piece_context(evaluation.profile) or (
        len(independent_design) >= 2
        and has_grail_design_evidence(evaluation.directional.piece_signals)
    )
    quiet = bool(
        route in {"grails", "ending"}
        and config.get("quiet_unverified_high_ticket_archive", True)
        and high_cost > 0
        and landed is not None
        and landed > high_cost
        and not credible_value
        and has_named_collection_context(evaluation.profile)
        and not strong_piece
    )
    if quiet:
        return AttentionDecision(
            "archive_discovery",
            "quiet",
            "named archive; high acquisition cost without compelling value evidence; timing alone is not value",
        )
    return AttentionDecision(
        "archive_discovery", "normal", "qualified independent archive/piece evidence"
    )
