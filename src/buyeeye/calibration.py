from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .evaluation import ListingEvaluation, evaluate_listing
from .live_sources import LiveListing
from .models import Comp
from .normalize import normalize_text
from .pipeline import DECISION_LOGIC_VERSION, PostDetailDecision, _post_detail_decision
from .settings import CONFIG_DIR, load_settings

DEFAULT_COHORT_PATH = CONFIG_DIR / "calibration_cohort.yaml"


class CalibrationError(ValueError):
    """Raised when the fixed calibration cohort is malformed."""


def _load_cohort(path: str | Path) -> dict[str, Any]:
    cohort_path = Path(path)
    payload = yaml.safe_load(cohort_path.read_text(encoding="utf-8")) or {}
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise CalibrationError("calibration cohort must contain a non-empty cases list")
    keys = [str(case.get("key") or "") for case in cases if isinstance(case, dict)]
    if len(keys) != len(cases) or any(not key for key in keys):
        raise CalibrationError("every calibration case must have a key")
    if len(keys) != len(set(keys)):
        raise CalibrationError("calibration case keys must be unique")
    return payload


def _listing(case: dict[str, Any]) -> LiveListing:
    record = case.get("listing")
    if not isinstance(record, dict):
        raise CalibrationError(f"case {case['key']!r} is missing listing data")
    title = str(record.get("title_original") or "").strip()
    if not title:
        raise CalibrationError(f"case {case['key']!r} has no title")
    listing_id = f"calibration-{case['key']}"
    raw = deepcopy(record.get("raw") or {})
    observed = {
        "title_original": title,
        "description_original": record.get("description_original"),
        "brand_observed": record.get("brand_observed"),
        "item_type_observed": record.get("item_type_observed"),
        "condition_original": record.get("condition"),
    }
    # The cohort may assert a card-level category on the LiveListing. Only add
    # richer Stage B hierarchy evidence when the fixture actually declares it;
    # replay must not manufacture a generic men's hierarchy for convenience.
    if "category_hierarchy" in record:
        observed["category_hierarchy"] = record["category_hierarchy"]
    raw.setdefault(
        "stage_b",
        {
            "status": "enriched",
            "observed": observed,
        },
    )
    return LiveListing(
        source="calibration",
        source_listing_id=listing_id,
        url=f"https://example.invalid/buyeeye-calibration/{case['key']}",
        title_original=title,
        title_normalized=normalize_text(title).casefold(),
        brand=str(record.get("brand") or "unknown"),
        category=str(record.get("category") or "menswear"),
        current_price=_optional_int(record.get("current_price")),
        buyout_price=_optional_int(record.get("buyout_price")),
        starting_price=_optional_int(record.get("starting_price")),
        seller_name=record.get("seller_name"),
        condition=record.get("condition"),
        image_url=None,
        time_remaining=record.get("time_remaining"),
        buyout_indicator=("buyout" if record.get("buyout_price") is not None else None),
        bid_count=_optional_int(record.get("bid_count")),
        watch_count=_optional_int(record.get("watch_count")),
        raw=raw,
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CalibrationError(f"invalid integer value in calibration cohort: {value!r}") from exc


def _comps(records: Any) -> list[Comp]:
    if not records:
        return []
    if not isinstance(records, list):
        raise CalibrationError("calibration comps must be a list")
    comps: list[Comp] = []
    for record in records:
        if not isinstance(record, dict):
            raise CalibrationError("each calibration comp must be a mapping")
        comps.append(
            Comp(
                source=str(record["source"]),
                title=str(record["title"]),
                price_jpy=_optional_int(record.get("price_jpy")),
                similarity=float(record.get("similarity", 1.0)),
                recency_days=int(record.get("recency_days", 30)),
                category=record.get("category"),
                is_sold=bool(record.get("is_sold", True)),
                provenance=str(record.get("provenance", "calibration_fixture")),
                price_evidence=str(record.get("price_evidence", "unknown")),
                match_evidence=record.get("match_evidence"),
                evidence_note=record.get("evidence_note"),
            )
        )
    return comps


def _route(
    listing: LiveListing,
    evaluation: ListingEvaluation,
    settings: dict[str, Any],
) -> PostDetailDecision:
    alert = settings.get("alert", {})
    return _post_detail_decision(
        listing,
        evaluation,
        ending_threshold_minutes=int(alert.get("ending_threshold_minutes", 60)),
        archive_watch_min_heat=int(alert.get("archive_watch_min_heat", 70)),
        silver_min_heat=int(alert.get("silver_min_score", 50)),
        grail_min_heat=int(alert.get("grail_min_score", 82)),
        archive_directional_min_heat=int(alert.get("archive_directional_min_heat", 55)),
        archive_directional_min_score=int(alert.get("archive_directional_min_score", 75)),
        unverified_grail_high_ticket_usd=float(
            alert.get("unverified_grail_high_ticket_usd", 300)
        ),
    )


def _evidence_value(value: Any) -> str | None:
    return str(value.value) if value is not None else None


def _case_result(case: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    listing = _listing(case)
    evaluation = evaluate_listing(
        listing,
        _comps(case.get("us_comps")),
        settings,
        japan_comps=_comps(case.get("japan_comps")),
    )
    decision = _route(listing, evaluation, settings)
    outcome = (
        f"route:{decision.route}"
        if decision.route
        else f"reject:{decision.rejection_reason or 'other'}"
    )
    acceptable = [str(value) for value in case.get("acceptable_outcomes", [])]
    relationship = evaluation.profile.relationship
    scope = evaluation.profile.product_scope
    return {
        "key": str(case["key"]),
        "cohort": str(case.get("cohort") or "unspecified"),
        "human_label": str(case.get("human_label") or "unlabeled"),
        # This is deliberately a source-attributed observation, not a synthetic
        # reconstruction of a historical scoring build.
        "prior_observation": deepcopy(case.get("prior_observation") or {}),
        "acceptable_outcomes": acceptable,
        "current": {
            "outcome": outcome,
            "aligned": outcome in acceptable if acceptable else None,
            "route": decision.route,
            "reason": decision.reason,
            "rejection_reason": decision.rejection_reason,
            "brand": _evidence_value(evaluation.profile.brand),
            "item_type": _evidence_value(evaluation.profile.item_type),
            "category": _evidence_value(evaluation.profile.category),
            "relationship": relationship.value if relationship else "uncertain",
            "relationship_provenance": relationship.provenance if relationship else None,
            "product_scope": {
                "target_role": scope.target_role,
                "offer_kind": scope.offer_kind,
                "audience": scope.audience,
                "offer_clarity": scope.offer_clarity,
                "acquisition_attributable": scope.acquisition_attributable,
                "reasons": list(scope.reasons),
            },
            "desirability_score": evaluation.desirability.score,
            "identity_confidence": evaluation.identity.confidence,
            "identity_score": evaluation.identity.score,
            "directional_score": evaluation.directional.score,
            "directional_confidence": evaluation.directional.confidence,
            "directional_thesis": evaluation.directional.thesis,
            "micro_asymmetry": evaluation.directional.micro_asymmetry,
            "exact_valuation_confidence": evaluation.valuation.confidence,
            "us_valuation_state": evaluation.directional.us_valuation_state,
            "japan_market_confidence": (
                evaluation.japan_comps.confidence if evaluation.japan_comps else "none"
            ),
            "landed_cost_jpy": evaluation.economics.landed_cost_jpy,
            "verified_us_median_jpy": evaluation.comps.median_jpy,
            "multiple": evaluation.economics.multiple,
            "auction_type": evaluation.auction_maturity.auction_type,
            "auction_maturity": evaluation.auction_maturity.level,
            "price_actionable": evaluation.auction_maturity.price_actionable,
            "current_bid_provisional": evaluation.auction_maturity.current_bid_provisional,
            "auth_risk": evaluation.auth_risk.level,
            "auth_reasons": list(evaluation.auth_risk.reasons),
            "piece_signals": list(evaluation.directional.piece_signals),
        },
    }


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    route_counts: Counter[str] = Counter()
    cohort_counts: Counter[str] = Counter()
    aligned_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    prior_source_counts: Counter[str] = Counter()
    known_prior_route_counts: Counter[str] = Counter()
    known_prior_misroutes_now_suppressed = 0
    for result in results:
        current = result["current"]
        prior = result["prior_observation"]
        route_counts[current["route"] or "suppressed"] += 1
        cohort_counts[result["cohort"]] += 1
        risk_counts[current["auth_risk"]] += 1
        prior_source_counts[str(prior.get("source") or "unspecified")] += 1
        prior_outcome = str(prior.get("outcome") or "")
        if prior.get("source") == "production_observation" and prior_outcome.startswith("route:"):
            known_prior_route_counts[prior_outcome.removeprefix("route:")] += 1
            if current["route"] is None:
                known_prior_misroutes_now_suppressed += 1
        alignment = current["aligned"]
        aligned_counts[
            "unscored" if alignment is None else "aligned" if alignment else "misaligned"
        ] += 1
    return {
        "cases": len(results),
        "cohorts": dict(sorted(cohort_counts.items())),
        "routes": dict(sorted(route_counts.items())),
        "auth_risk": dict(sorted(risk_counts.items())),
        "alignment": dict(sorted(aligned_counts.items())),
        "prior_observation_sources": dict(sorted(prior_source_counts.items())),
        "known_prior_production_routes": dict(sorted(known_prior_route_counts.items())),
        "known_prior_misroutes_now_suppressed": known_prior_misroutes_now_suppressed,
        "surfaced_positive": sum(
            1 for result in results if result["cohort"] == "positive" and result["current"]["route"]
        ),
        "suppressed_negative": sum(
            1
            for result in results
            if result["cohort"] == "negative" and result["current"]["route"] is None
        ),
        "purchased_now_surfaced": sum(
            1
            for result in results
            if result["prior_observation"].get("outcome") == "purchased"
            and result["current"]["route"] is not None
        ),
    }


def run_calibration(
    path: str | Path | None = None,
    *,
    settings_path: str | Path | None = None,
) -> dict[str, Any]:
    """Replay a fixed cohort through deterministic V1 evaluation and routing.

    This function intentionally performs no browser, database, Discord, or network
    work. It evaluates only the observations stored in the supplied YAML fixture.
    """
    cohort_path = Path(path) if path else DEFAULT_COHORT_PATH
    payload = _load_cohort(cohort_path)
    settings = load_settings(settings_path)
    results = [_case_result(case, settings) for case in payload["cases"]]
    return {
        "cohort_version": payload.get("version", 1),
        "decision_logic_version": DECISION_LOGIC_VERSION,
        "method": "fixed observations -> evaluate_listing -> post-detail routing",
        "historical_note": (
            "Prior observations are copied only when source-attributed in the fixture; "
            "they are not reconstructed historical scores."
        ),
        "summary": _summary(results),
        "cases": results,
    }
