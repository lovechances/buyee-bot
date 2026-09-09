"""Delivery attention, separate from identity, value, risk and primary routing."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

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
    if route in {"buyouts", "ending", "silver"}:
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
        route == "grails"
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
            "named archive; high acquisition cost without compelling value evidence",
        )
    return AttentionDecision(
        "archive_discovery", "normal", "qualified independent archive/piece evidence"
    )
