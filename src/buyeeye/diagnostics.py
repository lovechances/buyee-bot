from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .evaluation import evaluate_listing
from .live_sources import LiveListing
from .pipeline import _post_detail_decision
from .settings import load_settings


def marketplace_listing_identity(
    value: str,
    *,
    source: str | None = None,
) -> tuple[str, str]:
    """Return a stable source/ID pair from a supported URL or bare ID.

    PayPay Flea Market's current IDs use a ``z`` plus nine digits.  That bare
    form is accepted for benchmark convenience; a full URL (or explicit
    ``source`` column) remains the unambiguous form if marketplace ID formats
    ever overlap.
    """
    text = str(value or "").strip()
    requested_source = str(source or "").strip().casefold().replace("-", "_")
    source_aliases = {
        "": "",
        "buyee": "buyee",
        "jdirectitems": "buyee",
        "yahoo": "buyee",
        "yahoo_auction": "buyee",
        "paypay": "buyee_paypay",
        "paypay_fleamarket": "buyee_paypay",
        "buyee_paypay": "buyee_paypay",
    }
    if requested_source not in source_aliases:
        raise ValueError(f"Unsupported marketplace source: {source}")
    explicit_source = source_aliases[requested_source]

    paypay_match = re.search(
        r"/paypayfleamarket/item/([A-Za-z0-9]+)(?:[/?#]|$)",
        text,
        re.IGNORECASE,
    )
    if paypay_match:
        if explicit_source and explicit_source != "buyee_paypay":
            raise ValueError("Listing URL conflicts with the requested marketplace source")
        return "buyee_paypay", paypay_match.group(1)

    # BuyEye's authenticated source emits canonical Buyee JDirectItems URLs.
    # Match that full path before Yahoo's shorter auction form so intermediate
    # path components (notably ``jdirectitems``) can never become the ID.
    for pattern in (
        r"/item/jdirectitems/auction/([A-Za-z0-9]+)(?:[/?#]|$)",
        r"/jp/auction/([A-Za-z0-9]+)(?:[/?#]|$)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            if explicit_source and explicit_source != "buyee":
                raise ValueError("Listing URL conflicts with the requested marketplace source")
            return "buyee", match.group(1)
    if re.fullmatch(r"[A-Za-z0-9]+", text):
        inferred_source = (
            "buyee_paypay"
            if re.fullmatch(r"z\d{9}", text, re.IGNORECASE)
            else "buyee"
        )
        return explicit_source or inferred_source, text
    raise ValueError(
        "Expected a Buyee/JDirectItems or PayPay Flea Market listing URL/ID"
    )


def marketplace_listing_id(value: str) -> str:
    """Compatibility helper returning only the parsed marketplace ID."""
    return marketplace_listing_identity(value)[1]


def _dict_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _select_one(
    connection: sqlite3.Connection,
    table: str,
    where: str,
    parameters: tuple[Any, ...],
) -> dict[str, Any] | None:
    if not _table_exists(connection, table):
        return None
    return _dict_row(
        connection.execute(
            f'SELECT * FROM "{table}" WHERE {where} LIMIT 1', parameters
        ).fetchone()
    )


def _detail_summary(detail: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    if not detail:
        return {"available": False}, {}
    observed = _decode(detail.get("observation_json"), {})
    if not isinstance(observed, dict):
        return (
            {
                "available": True,
                "retrieved_at": detail.get("retrieved_at"),
                "expires_at": detail.get("expires_at"),
                "content_hash": detail.get("content_hash"),
                "fields": {},
                "observation_valid": False,
            },
            {},
        )
    summary = {
        "available": True,
        "retrieved_at": detail.get("retrieved_at"),
        "expires_at": detail.get("expires_at"),
        "content_hash": detail.get("content_hash"),
        "fields": {
            key: observed.get(key)
            for key in (
                "title_original",
                "category_hierarchy",
                "brand_observed",
                "item_type_observed",
                "condition_original",
                "size_original",
                "material_original",
                "seller_name",
                "seller_feedback_percent",
                "seller_feedback_count",
                "current_price",
                "buyout_price",
                "bid_count",
                "watch_count",
                "auction_end_time",
            )
            if observed.get(key) not in (None, "", [])
        },
    }
    return summary, observed


def _current_evaluation(
    listing_row: dict[str, Any] | None,
    observed: dict[str, Any],
) -> dict[str, Any] | None:
    if not listing_row:
        return None
    title = str(listing_row.get("title_original") or "")
    if not title:
        return None
    raw: dict[str, Any] = {}
    if observed:
        raw["stage_b"] = {"status": "enriched", "observed": observed}
        if observed.get("seller_feedback_percent") is not None:
            raw["seller_feedback_percent"] = observed["seller_feedback_percent"]
        if observed.get("seller_feedback_count") is not None:
            raw["seller_feedback_count"] = observed["seller_feedback_count"]
        raw["detail_photo_count"] = len(observed.get("image_urls") or [])
    listing = LiveListing(
        source=str(listing_row.get("source") or "buyee"),
        source_listing_id=str(listing_row.get("source_listing_id") or ""),
        url=str(listing_row.get("url") or ""),
        title_original=title,
        title_normalized=str(listing_row.get("title_normalized") or title.casefold()),
        brand=str(listing_row.get("brand") or "unknown"),
        category=str(listing_row.get("category") or "unknown"),
        current_price=listing_row.get("current_price"),
        buyout_price=listing_row.get("buyout_price"),
        starting_price=listing_row.get("starting_price"),
        seller_name=observed.get("seller_name"),
        condition=observed.get("condition_original"),
        image_url=listing_row.get("image_url"),
        time_remaining=listing_row.get("time_remaining"),
        buyout_indicator=listing_row.get("buyout_indicator"),
        bid_count=listing_row.get("bid_count"),
        watch_count=listing_row.get("watch_count"),
        raw=raw,
    )
    settings = load_settings()
    evaluation = evaluate_listing(listing, settings=settings)
    alert = settings.get("alert", {})
    decision = _post_detail_decision(
        listing,
        evaluation,
        ending_threshold_minutes=int(alert.get("ending_threshold_minutes", 60)),
        archive_watch_min_heat=int(alert.get("archive_watch_min_heat", 70)),
        silver_min_heat=int(alert.get("silver_min_score", 50)),
        grail_min_heat=int(alert.get("grail_min_score", 82)),
        archive_directional_min_heat=int(
            alert.get("archive_directional_min_heat", 55)
        ),
        archive_directional_min_score=int(
            alert.get("archive_directional_min_score", 75)
        ),
        unverified_grail_high_ticket_usd=float(
            alert.get("unverified_grail_high_ticket_usd", 300)
        ),
    )
    scope = evaluation.profile.product_scope
    return {
        "note": "recomputed read-only from latest listing/detail state; historical comps are not inferred",
        "brand": evaluation.profile.brand.value if evaluation.profile.brand else None,
        "item_type": (
            evaluation.profile.item_type.value if evaluation.profile.item_type else None
        ),
        "category": (
            evaluation.profile.category.value if evaluation.profile.category else None
        ),
        "relationship": (
            evaluation.profile.relationship.value
            if evaluation.profile.relationship
            else "uncertain"
        ),
        "product_scope": asdict(scope) if scope else None,
        "identity": {
            "score": evaluation.identity.score,
            "confidence": evaluation.identity.confidence,
            "reasons": evaluation.identity.reasons,
        },
        "piece_heat": evaluation.desirability.score,
        "directional": {
            "score": evaluation.directional.score,
            "confidence": evaluation.directional.confidence,
            "thesis": evaluation.directional.thesis,
            "reasons": evaluation.directional.reasons,
        },
        "risk": {
            "level": evaluation.auth_risk.level,
            "reasons": evaluation.auth_risk.reasons,
        },
        "auction_maturity": asdict(evaluation.auction_maturity),
        "route": decision.route,
        "route_reason": decision.reason,
        "rejection_reason": decision.rejection_reason,
    }


def diagnose_listing(
    db_path: str | Path,
    url_or_id: str,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """Join persistent state for one listing without browser, Discord, or DB writes."""
    path = Path(db_path)
    source, listing_id = marketplace_listing_identity(url_or_id, source=source)
    if not path.exists():
        return {
            "source": source,
            "marketplace_id": listing_id,
            "seen": False,
            "error": f"database not found: {path}",
        }
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        seen = _select_one(
            connection,
            "seenlisting",
            "source=? AND source_listing_id=?",
            (source, listing_id),
        )
        listing = _select_one(
            connection,
            "listing",
            "source=? AND source_listing_id=?",
            (source, listing_id),
        )
        detail = _select_one(
            connection,
            "detailcache",
            "source=? AND source_listing_id=?",
            (source, listing_id),
        )
        alert = _select_one(
            connection,
            "alertstate",
            "source=? AND source_listing_id=?",
            (source, listing_id),
        )
        query_state = None
        if seen and _table_exists(connection, "queryruntimestate"):
            query_columns = _columns(connection, "queryruntimestate")
            strategy_id = seen.get("last_strategy_id")
            if strategy_id and "strategy_id" in query_columns:
                query_state = _select_one(
                    connection,
                    "queryruntimestate",
                    "strategy_id=?",
                    (strategy_id,),
                )
            if (
                query_state is None
                and seen.get("query")
                and "query" in query_columns
            ):
                query_state = _select_one(
                    connection,
                    "queryruntimestate",
                    "query=?",
                    (seen["query"],),
                )
    finally:
        connection.close()

    detail_summary, observed = _detail_summary(detail)
    latest_trace = _decode((seen or {}).get("latest_trace_json"), None)
    history = _decode((seen or {}).get("event_history_json"), [])
    stage_a_outcomes = _decode((seen or {}).get("stage_a_outcomes_json"), {})
    settings = load_settings()
    next_revisit = None
    revisit_hours = None
    if seen and seen.get("last_processed_at"):
        try:
            processed = datetime.fromisoformat(str(seen["last_processed_at"]))
            processed = processed.replace(tzinfo=processed.tzinfo or UTC)
            scanner = settings.get("scanner", {})
            standard_hours = int(scanner.get("listing_dedupe_hours", 6))
            classification = str((seen or {}).get("decision_classification") or "")
            fully_evaluated = bool(
                isinstance(stage_a_outcomes, dict)
                and "selected" in stage_a_outcomes.values()
                and classification.startswith(("reject:", "final:"))
            )
            revisit_hours = (
                max(
                    standard_hours,
                    int(scanner.get("fully_evaluated_dedupe_hours", standard_hours)),
                )
                if fully_evaluated
                else standard_hours
            )
            next_revisit = (
                processed + timedelta(hours=revisit_hours)
            ).isoformat()
        except ValueError:
            pass

    stored_listing = None
    if listing:
        stored_listing = {
            key: listing.get(key)
            for key in (
                "url",
                "title_original",
                "brand",
                "category",
                "current_price",
                "buyout_price",
                "starting_price",
                "time_remaining",
                "bid_count",
                "watch_count",
                "desirability_score",
                "opportunity_score",
                "auth_risk",
                "comp_confidence",
                "decision",
                "is_alerted",
            )
        }
    try:
        current_replay = _current_evaluation(listing, observed)
    except Exception as exc:  # noqa: BLE001 - diagnostics must preserve stored evidence.
        current_replay = {
            "available": False,
            "error_type": type(exc).__name__,
            "note": "current replay unavailable; persisted trace remains authoritative",
        }

    return {
        "source": source,
        "marketplace_id": listing_id,
        "seen": seen is not None,
        "first_seen": (seen or {}).get("first_seen_at"),
        "latest_seen": (seen or {}).get("last_seen_at"),
        "last_processed": (seen or {}).get("last_processed_at"),
        "first_inspected": (seen or {}).get("first_inspected_at"),
        "last_inspected": (seen or {}).get("last_inspected_at"),
        "sightings": (seen or {}).get("sightings", 0),
        "discovery": {
            "first_query": (seen or {}).get("first_query"),
            "first_strategy_id": (seen or {}).get("first_strategy_id"),
            "first_strategy_type": (seen or {}).get("first_strategy_type"),
            "first_page": (seen or {}).get("first_search_page"),
            "first_rank": (seen or {}).get("first_search_rank"),
            "last_query": (seen or {}).get("query"),
            "last_strategy_id": (seen or {}).get("last_strategy_id"),
            "last_strategy_type": (seen or {}).get("last_strategy_type"),
            "last_page": (seen or {}).get("last_search_page"),
            "last_rank": (seen or {}).get("last_search_rank"),
        },
        "stage_a_outcomes": stage_a_outcomes,
        "decision": {
            "logic_version": (seen or {}).get("decision_logic_version"),
            "classification": (seen or {}).get("decision_classification"),
            "at": (seen or {}).get("decision_at"),
        },
        "listing": stored_listing,
        "detail": detail_summary,
        "latest_trace": latest_trace,
        "event_history": history,
        "alert_state": alert,
        "dedupe": {
            "signature": (seen or {}).get("signature"),
            "next_standard_revisit": next_revisit,
            "unchanged_revisit_window_hours": revisit_hours,
            "query_next_eligible": (query_state or {}).get("next_eligible_at"),
        },
        "current_replay": current_replay,
        "legacy_limitations": (
            []
            if latest_trace
            else [
                "page/rank and per-stage event history were not recorded before this build",
                "historical comp candidates cannot be reconstructed from aggregate scan events",
            ]
        ),
    }


def _benchmark_miss_category(report: dict[str, Any]) -> str:
    """Assign one mutually exclusive, evidence-based benchmark outcome."""
    if not report.get("seen"):
        return (
            "source_coverage_miss"
            if report.get("source") == "buyee_paypay"
            else "discovery_miss"
        )
    if report.get("alert_state"):
        return "alerted"

    trace = report.get("latest_trace") or {}
    stage_a = trace.get("stage_a") or {}
    classification = str((report.get("decision") or {}).get("classification") or "")
    detail = report.get("detail") or {}
    if stage_a.get("reject_reason") == "candidate_budget" or classification == (
        "stage_a:candidate_budget"
    ):
        return "candidate_budget"
    if classification.startswith("stage_a:"):
        return "stage_a_reject:" + classification.partition(":")[2]
    if detail.get("available") and trace.get("detail_status") == "failed":
        return "stage_b_failure"
    if classification == "detail_failed":
        return "stage_b_failure"
    if classification.startswith("reject:"):
        return "post_detail_reject:" + classification.partition(":")[2]
    if classification.startswith("final:"):
        return "final_without_alert"
    replay = report.get("current_replay") or {}
    if replay.get("rejection_reason"):
        return "current_replay_reject:" + str(replay["rejection_reason"])
    if report.get("first_inspected") is None:
        return "stage_a_history_unavailable"
    return "unclassified_seen"


def _benchmark_inputs(path: str | Path) -> list[dict[str, str | None]]:
    input_path = Path(path)
    if not input_path.is_file():
        raise ValueError(f"benchmark input not found: {input_path}")
    if input_path.suffix.casefold() == ".csv":
        with input_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            rows: list[dict[str, str | None]] = []
            for row in reader:
                value = next(
                    (
                        str(row.get(name) or "").strip()
                        for name in ("url_or_id", "url", "marketplace_id", "id")
                        if str(row.get(name) or "").strip()
                    ),
                    "",
                )
                if value:
                    rows.append(
                        {
                            "value": value,
                            "source": str(row.get("source") or "").strip() or None,
                            "annotation": str(row.get("annotation") or "").strip() or None,
                        }
                    )
            return rows

    rows = []
    for raw_line in input_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        value, _, annotation = line.partition("\t")
        rows.append(
            {
                "value": value.strip(),
                "source": None,
                "annotation": annotation.strip() or None,
            }
        )
    return rows


def benchmark_listings(
    db_path: str | Path,
    input_path: str | Path,
) -> dict[str, Any]:
    """Batch the read-only listing diagnostic and aggregate miss categories."""
    inputs = _benchmark_inputs(input_path)
    results: list[dict[str, Any]] = []
    categories: Counter[str] = Counter()
    for index, item in enumerate(inputs, start=1):
        value = str(item["value"] or "")
        try:
            report = diagnose_listing(
                db_path,
                value,
                source=item.get("source"),
            )
            category = _benchmark_miss_category(report)
            categories[category] += 1
            results.append(
                {
                    "index": index,
                    "input": value,
                    "annotation": item.get("annotation"),
                    "outcome": category,
                    **report,
                }
            )
        except ValueError as exc:
            category = "invalid_input"
            categories[category] += 1
            results.append(
                {
                    "index": index,
                    "input": value,
                    "annotation": item.get("annotation"),
                    "outcome": category,
                    "error": str(exc),
                }
            )
    return {
        "database": str(Path(db_path)),
        "read_only": True,
        "browser_used": False,
        "discord_used": False,
        "total": len(results),
        "seen": sum(bool(result.get("seen")) for result in results),
        "outcomes": dict(sorted(categories.items())),
        "results": results,
    }
