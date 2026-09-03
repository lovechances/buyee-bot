from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Field, SQLModel, select

from .db import DB_PATH, AlertState, get_session, init_db
from .discovery import QuerySpec


class QueryRuntimeState(SQLModel, table=True):
    key: str = Field(primary_key=True)
    query: str = Field(index=True)
    family: str
    provenance: str
    discovery_mode: str = "direct_piece"
    query_layer: str | None = None
    strategy_id: str | None = None
    strategy_type: str | None = None
    filters_json: str | None = None
    priority: int
    runs: int = 0
    cards_seen: int = 0
    unique_seen: int = 0
    duplicates: int = 0
    stage_a_rejected: int = 0
    # New counters stay nullable so SQLite can add them safely to a populated
    # production table; all readers treat legacy NULL values as zero.
    stage_a_brand_confirmed: int | None = Field(default=0)
    stage_a_category_valid: int | None = Field(default=0)
    stage_a_eligible: int | None = Field(default=0)
    stage_a_capped: int | None = Field(default=0)
    stage_b_candidates: int = 0
    stage_b_enriched: int = 0
    stage_b_failed: int | None = Field(default=0)
    stage_b_rejected: int | None = Field(default=0)
    final_candidates: int = 0
    alerts: int = 0
    failures: int = 0
    last_run_at: datetime | None = None
    next_eligible_at: datetime | None = None


class SeenListing(SQLModel, table=True):
    key: str = Field(primary_key=True)
    source: str
    source_listing_id: str = Field(index=True)
    signature: str
    first_seen_at: datetime
    last_seen_at: datetime
    last_processed_at: datetime
    query: str
    first_query: str | None = None
    first_strategy_id: str | None = None
    first_strategy_type: str | None = None
    last_strategy_id: str | None = None
    last_strategy_type: str | None = None
    stage_a_outcomes_json: str | None = None
    decision_logic_version: str | None = None
    decision_classification: str | None = None
    decision_at: datetime | None = None
    sightings: int = 1


class RuntimeHealthState(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    started_at: datetime | None = None
    last_successful_scan: datetime | None = None
    last_auth_success: datetime | None = None
    last_parser_anomaly: datetime | None = None
    last_failure_at: datetime | None = None
    current_query: str | None = None
    last_query: str | None = None
    consecutive_source_failures: int = 0
    browser_restart_count: int = 0
    alerts_emitted: int = 0
    unique_listings_scanned: int = 0


class ScanEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    recorded_at: datetime = Field(index=True)
    query: str
    family: str
    provenance: str | None = None
    query_layer: str | None = None
    strategy_id: str | None = None
    strategy_type: str | None = None
    filters_json: str | None = None
    failed: bool = False
    search_pages: int = 0
    cards_seen: int = 0
    unique_seen: int = 0
    duplicates: int = 0
    stage_a_rejected: int = 0
    # NULL identifies pre-upgrade rows so new recognition rates never use a
    # mixed legacy denominator during a rolling deployment window.
    stage_a_telemetry_version: int | None = Field(default=None)
    stage_a_brand_confirmed: int | None = Field(default=0)
    stage_a_category_valid: int | None = Field(default=0)
    stage_a_eligible: int | None = Field(default=0)
    stage_a_capped: int | None = Field(default=0)
    stage_a_paths_json: str | None = None
    stage_a_reject_reasons_json: str | None = None
    stage_b_candidates: int = 0
    stage_b_enriched: int = 0
    stage_b_failed: int | None = Field(default=0)
    stage_b_rejected: int | None = Field(default=0)
    comp_lookups: int = 0
    comp_cache_hits: int = 0
    comp_cache_misses: int = 0
    final_candidates: int = 0
    alerts: int = 0
    routes_json: str = "{}"
    alert_routes_json: str | None = None
    theses_json: str | None = None
    opportunities_json: str | None = None
    detected_family_funnel_json: str | None = None
    # Decision telemetry is additive/nullable so a rolling Railway deployment
    # can read the existing production database without a destructive migration.
    decision_telemetry_version: int | None = Field(default=None)
    post_detail_reject_reasons_json: str | None = None
    piece_relationships_json: str | None = None
    confidence_counts_json: str | None = None
    alert_theses_json: str | None = None
    recovery_counts_json: str | None = None
    suppressed_alerts: int | None = Field(default=0)
    failed_alerts: int | None = Field(default=0)
    logic_reconsidered: int | None = Field(default=0)
    post_detail_decisions_json: str | None = None


@dataclass
class DedupeResult:
    is_duplicate: bool
    unchanged: bool
    logic_reconsidered: bool = False


def _key(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _json_list(value: str | None) -> list[dict[str, Any]]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    return [item for item in decoded if isinstance(item, dict)]


def _merge_counts(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge flat or nested count dictionaries from versioned telemetry."""
    for key, value in source.items():
        if isinstance(value, dict):
            bucket = target.setdefault(key, {})
            if not isinstance(bucket, dict):
                bucket = {}
                target[key] = bucket
            _merge_counts(bucket, value)
            continue
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        target[key] = int(target.get(key, 0)) + count


def _query_key(spec: QuerySpec) -> str:
    return _key(spec.runtime_identity)


def _adaptive_priority(row: QueryRuntimeState | None) -> float:
    """Small, sample-shrunk yield adjustment; strategies are never removed."""
    if row is None or row.runs <= 0:
        return 0.0
    sample_weight = min(1.0, row.runs / 5) * min(1.0, row.unique_seen / 100)
    stage_b_rate = row.stage_b_candidates / max(1, row.unique_seen)
    final_rate = row.final_candidates / max(1, row.unique_seen)
    alert_rate = row.alerts / max(1, row.runs)
    duplicate_rate = row.duplicates / max(1, row.cards_seen)
    failure_rate = row.failures / max(1, row.runs)
    raw = (
        stage_b_rate * 20
        + final_rate * 35
        + alert_rate * 10
        - duplicate_rate * 8
        - failure_rate * 10
    )
    return max(-8.0, min(8.0, raw * sample_weight))


def choose_query(
    specs: list[QuerySpec],
    db_path: str | Path = DB_PATH,
    now: datetime | None = None,
    layer_shares: dict[str, float] | None = None,
    strategy_shares: dict[str, float] | None = None,
    allocation_window: int = 100,
) -> QuerySpec | None:
    init_db(db_path)
    current = now or datetime.now(UTC)
    ranked: dict[str, list[tuple[float, QuerySpec]]] = {}
    has_market_structure = any(spec.query_layer == "market_structure" for spec in specs)
    shares = (
        {"broad": 0.35, "medium": 0.20, "surgical": 0.10, "market_structure": 0.35}
        if has_market_structure
        else {"broad": 0.5, "medium": 0.3, "surgical": 0.2}
    )
    for layer, value in (layer_shares or {}).items():
        if layer in shares and float(value) > 0:
            shares[layer] = float(value)
    runs_by_layer: dict[str, int] = {}
    runs_by_strategy: dict[str, int] = {}
    runs_by_identity: dict[str, int] = {}
    with get_session(db_path) as session:
        recent = session.exec(
            select(ScanEvent)
            .where(ScanEvent.query_layer.is_not(None))  # type: ignore[union-attr]
            .order_by(ScanEvent.recorded_at.desc())
            .limit(max(1, allocation_window))
        ).all()
        for event in recent:
            if event.query_layer:
                runs_by_layer[event.query_layer] = runs_by_layer.get(event.query_layer, 0) + 1
            if event.strategy_type:
                runs_by_strategy[event.strategy_type] = (
                    runs_by_strategy.get(event.strategy_type, 0) + 1
                )
        for spec in specs:
            row = session.get(QueryRuntimeState, _query_key(spec))
            if row and row.next_eligible_at:
                eligible = row.next_eligible_at
                if eligible.tzinfo is None:
                    eligible = eligible.replace(tzinfo=UTC)
                if eligible > current:
                    continue
            runs = row.runs if row else 0
            runs_by_identity[spec.runtime_identity] = runs
            exploration = 8 / (1 + runs)
            staleness = 0.0
            if row and row.last_run_at:
                last_run = row.last_run_at
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=UTC)
                overdue_minutes = max(
                    0.0,
                    (current - last_run).total_seconds() / 60 - spec.cooldown_minutes,
                )
                staleness = min(4.0, overdue_minutes / max(1, spec.cooldown_minutes))
            ranked.setdefault(spec.query_layer, []).append(
                (spec.priority + _adaptive_priority(row) + exploration + staleness, spec)
            )
    if not ranked:
        return None

    shares = {layer: value for layer, value in shares.items() if layer in ranked and value > 0}
    for layer in ranked:
        shares.setdefault(layer, 0.01)
    share_total = sum(shares.values())
    shares = {layer: value / share_total for layer, value in shares.items()}
    total_runs = sum(runs_by_layer.values())
    selected_layer = max(
        ranked,
        key=lambda layer: (
            shares.get(layer, 0.0) * (total_runs + 1) - runs_by_layer.get(layer, 0),
            shares.get(layer, 0.0),
        ),
    )
    candidates = ranked[selected_layer]
    if selected_layer == "market_structure":
        by_strategy: dict[str, list[tuple[float, QuerySpec]]] = {}
        for candidate in candidates:
            by_strategy.setdefault(candidate[1].strategy_type or "market_price_band", []).append(
                candidate
            )
        type_shares = {
            "market_buyout": 0.45,
            "market_ending": 0.45,
            "market_price_band": 0.10,
        }
        for strategy_type, value in (strategy_shares or {}).items():
            if strategy_type in type_shares and float(value) > 0:
                type_shares[strategy_type] = float(value)
        type_shares = {key: value for key, value in type_shares.items() if key in by_strategy}
        type_total = sum(type_shares.values())
        type_shares = {key: value / type_total for key, value in type_shares.items()}
        total_market_runs = sum(
            runs_by_strategy.get(strategy_type, 0) for strategy_type in type_shares
        )
        selected_type = max(
            by_strategy,
            key=lambda strategy_type: (
                type_shares.get(strategy_type, 0.0) * (total_market_runs + 1)
                - runs_by_strategy.get(strategy_type, 0),
                type_shares.get(strategy_type, 0.0),
            ),
        )
        candidates = by_strategy[selected_type]
    minimum_runs = min(runs_by_identity[item[1].runtime_identity] for item in candidates)
    # Every strategy gets two clean exploration samples before yield may create a small lead.
    allowed_lead = 0 if minimum_runs < 2 else 1
    candidates = [
        item
        for item in candidates
        if runs_by_identity[item[1].runtime_identity] <= minimum_runs + allowed_lead
    ]
    return max(candidates, key=lambda item: item[0])[1]


def record_query_result(
    spec: QuerySpec,
    result: object,
    db_path: str | Path = DB_PATH,
    failed: bool = False,
) -> None:
    init_db(db_path)
    now = datetime.now(UTC)
    key = _query_key(spec)
    with get_session(db_path) as session:
        row = session.get(QueryRuntimeState, key)
        if row is None:
            row = QueryRuntimeState(
                key=key,
                query=spec.query,
                family=spec.family,
                provenance=spec.provenance,
                discovery_mode=spec.discovery_mode,
                query_layer=spec.query_layer,
                strategy_id=spec.strategy_id,
                strategy_type=spec.strategy_type,
                filters_json=json.dumps(
                    spec.search_filters.observations() if spec.search_filters else {}
                ),
                priority=spec.priority,
            )
            session.add(row)
        row.query = spec.query
        row.family = spec.family
        row.provenance = spec.provenance
        row.discovery_mode = spec.discovery_mode
        row.query_layer = spec.query_layer
        row.strategy_id = spec.strategy_id
        row.strategy_type = spec.strategy_type
        row.filters_json = json.dumps(
            spec.search_filters.observations() if spec.search_filters else {}
        )
        row.priority = spec.priority
        row.runs += 1
        row.cards_seen += int(getattr(result, "scanned", 0))
        row.unique_seen += int(getattr(result, "unique_listings", 0))
        row.duplicates += int(getattr(result, "duplicates", 0))
        row.stage_a_rejected = (row.stage_a_rejected or 0) + int(
            getattr(result, "stage_a_rejected", 0)
        )
        row.stage_a_brand_confirmed = (row.stage_a_brand_confirmed or 0) + int(
            getattr(result, "stage_a_brand_confirmed", 0)
        )
        row.stage_a_category_valid = (row.stage_a_category_valid or 0) + int(
            getattr(result, "stage_a_category_valid", 0)
        )
        row.stage_a_eligible = (row.stage_a_eligible or 0) + int(
            getattr(result, "stage_a_eligible", 0)
        )
        row.stage_a_capped = (row.stage_a_capped or 0) + int(getattr(result, "stage_a_capped", 0))
        row.stage_b_candidates = (row.stage_b_candidates or 0) + int(
            getattr(result, "stage_b_candidates", 0)
        )
        row.stage_b_enriched = (row.stage_b_enriched or 0) + int(
            getattr(result, "stage_b_enriched", 0)
        )
        row.stage_b_failed = (row.stage_b_failed or 0) + int(getattr(result, "stage_b_failed", 0))
        row.stage_b_rejected = (row.stage_b_rejected or 0) + int(
            getattr(result, "stage_b_rejected", 0)
        )
        row.final_candidates = (row.final_candidates or 0) + int(
            getattr(result, "final_candidates", 0)
        )
        row.alerts = (row.alerts or 0) + int(getattr(result, "new_alerts", 0))
        row.failures = (row.failures or 0) + int(failed)
        row.last_run_at = now
        row.next_eligible_at = now + timedelta(minutes=spec.cooldown_minutes)
        session.add(
            ScanEvent(
                recorded_at=now,
                query=spec.query,
                family=spec.family,
                provenance=spec.provenance,
                query_layer=spec.query_layer,
                strategy_id=spec.strategy_id,
                strategy_type=spec.strategy_type,
                filters_json=json.dumps(
                    spec.search_filters.observations() if spec.search_filters else {}
                ),
                failed=failed,
                search_pages=int(getattr(result, "search_pages", 0)),
                cards_seen=int(getattr(result, "scanned", 0)),
                unique_seen=int(getattr(result, "unique_listings", 0)),
                duplicates=int(getattr(result, "duplicates", 0)),
                stage_a_rejected=int(getattr(result, "stage_a_rejected", 0)),
                stage_a_telemetry_version=1,
                stage_a_brand_confirmed=int(getattr(result, "stage_a_brand_confirmed", 0)),
                stage_a_category_valid=int(getattr(result, "stage_a_category_valid", 0)),
                stage_a_eligible=int(getattr(result, "stage_a_eligible", 0)),
                stage_a_capped=int(getattr(result, "stage_a_capped", 0)),
                stage_a_paths_json=json.dumps(getattr(result, "stage_a_path_counts", {}) or {}),
                stage_a_reject_reasons_json=json.dumps(
                    getattr(result, "stage_a_reject_counts", {}) or {}
                ),
                stage_b_candidates=int(getattr(result, "stage_b_candidates", 0)),
                stage_b_enriched=int(getattr(result, "stage_b_enriched", 0)),
                stage_b_failed=int(getattr(result, "stage_b_failed", 0)),
                stage_b_rejected=int(getattr(result, "stage_b_rejected", 0)),
                comp_lookups=int(getattr(result, "comp_queried", 0)),
                comp_cache_hits=int(getattr(result, "comp_cache_hits", 0)),
                comp_cache_misses=int(getattr(result, "comp_cache_misses", 0)),
                final_candidates=int(getattr(result, "final_candidates", 0)),
                alerts=int(getattr(result, "new_alerts", 0)),
                routes_json=json.dumps(getattr(result, "route_counts", {}) or {}),
                alert_routes_json=json.dumps(getattr(result, "alert_route_counts", {}) or {}),
                theses_json=json.dumps(getattr(result, "thesis_counts", {}) or {}),
                opportunities_json=json.dumps(getattr(result, "opportunity_counts", {}) or {}),
                detected_family_funnel_json=json.dumps(
                    getattr(result, "detected_family_funnel", {}) or {}
                ),
                decision_telemetry_version=1,
                post_detail_reject_reasons_json=json.dumps(
                    getattr(result, "post_detail_reject_counts", {}) or {}
                ),
                piece_relationships_json=json.dumps(
                    getattr(result, "piece_relationship_counts", {}) or {}
                ),
                confidence_counts_json=json.dumps(getattr(result, "confidence_counts", {}) or {}),
                alert_theses_json=json.dumps(getattr(result, "alert_thesis_counts", {}) or {}),
                recovery_counts_json=json.dumps(getattr(result, "recovery_counts", {}) or {}),
                suppressed_alerts=int(getattr(result, "suppressed_alerts", 0) or 0),
                failed_alerts=int(getattr(result, "failed_alerts", 0) or 0),
                logic_reconsidered=int(getattr(result, "logic_reconsidered", 0) or 0),
                post_detail_decisions_json=json.dumps(
                    list(getattr(result, "post_detail_decisions", []) or [])[:6]
                ),
            )
        )
        session.commit()


def mark_listing_seen(
    source: str,
    source_listing_id: str,
    signature: str,
    query: str,
    db_path: str | Path = DB_PATH,
    freshness_hours: int = 6,
    strategy_id: str | None = None,
    strategy_type: str | None = None,
    family: str | None = None,
    decision_logic_version: str | None = None,
    allow_logic_reconsideration: bool = False,
    logic_reconsideration_max_age_hours: int = 24,
    logic_reconsideration_max_per_hour: int = 2,
) -> DedupeResult:
    init_db(db_path)
    now = datetime.now(UTC)
    key = _key(source, source_listing_id)
    with get_session(db_path) as session:
        row = session.get(SeenListing, key)
        if row is None:
            session.add(
                SeenListing(
                    key=key,
                    source=source,
                    source_listing_id=source_listing_id,
                    signature=signature,
                    first_seen_at=now,
                    last_seen_at=now,
                    last_processed_at=now,
                    query=query,
                    first_query=query,
                    first_strategy_id=strategy_id,
                    first_strategy_type=strategy_type,
                    last_strategy_id=strategy_id,
                    last_strategy_type=strategy_type,
                    decision_logic_version=decision_logic_version,
                )
            )
            session.commit()
            return DedupeResult(False, False, False)
        processed = row.last_processed_at
        if processed.tzinfo is None:
            processed = processed.replace(tzinfo=UTC)
        unchanged = row.signature == signature
        fresh_duplicate = unchanged and processed > now - timedelta(hours=freshness_hours)
        context = f"{family or 'unattributed'}:{strategy_type or 'legacy_unattributed'}"
        outcomes = json.loads(row.stage_a_outcomes_json or "{}")
        if not isinstance(outcomes, dict):
            outcomes = {}
        if not unchanged:
            outcomes = {}
            row.stage_a_outcomes_json = None
        context_outcome = outcomes.get(context)
        stage_a_revisit = bool(
            fresh_duplicate
            and "selected" not in outcomes.values()
            and (context_outcome == "capped" or (outcomes and context_outcome is None))
        )
        version_changed = bool(
            decision_logic_version
            and row.decision_logic_version != decision_logic_version
        )
        within_reconsideration_age = processed > now - timedelta(
            hours=max(1, logic_reconsideration_max_age_hours)
        )
        logic_reconsidered = False
        if (
            fresh_duplicate
            and version_changed
            and context_outcome == "selected"
            and within_reconsideration_age
        ):
            prior_alert = session.exec(
                select(AlertState).where(
                    AlertState.source == source,
                    AlertState.source_listing_id == source_listing_id,
                )
            ).first()
            if prior_alert is not None:
                # Historical alerts are acknowledged at the new logic version
                # but never replayed solely because decision code changed.
                row.decision_logic_version = decision_logic_version
            elif allow_logic_reconsideration:
                recent_reconsiderations = session.exec(
                    select(ScanEvent).where(
                        ScanEvent.recorded_at >= now - timedelta(hours=1)
                    )
                ).all()
                reconsiderations_used = sum(
                    int(event.logic_reconsidered or 0)
                    for event in recent_reconsiderations
                )
                if reconsiderations_used < max(
                    0, logic_reconsideration_max_per_hour
                ):
                    logic_reconsidered = True
                    row.decision_logic_version = decision_logic_version
        duplicate = fresh_duplicate and not stage_a_revisit and not logic_reconsidered
        row.last_seen_at = now
        row.sightings += 1
        if not row.first_query:
            row.first_query = row.query
            row.first_strategy_id = None
            row.first_strategy_type = "legacy_unattributed"
        row.query = query
        row.last_strategy_id = strategy_id
        row.last_strategy_type = strategy_type
        if not duplicate:
            row.signature = signature
            row.last_processed_at = now
            if decision_logic_version:
                row.decision_logic_version = decision_logic_version
        session.commit()
        return DedupeResult(duplicate, unchanged, logic_reconsidered)


def record_stage_a_outcomes(
    outcomes: dict[tuple[str, str], str],
    db_path: str | Path = DB_PATH,
    *,
    strategy_type: str | None = None,
    family: str | None = None,
) -> None:
    """Persist bounded inspection outcomes without changing global listing identity."""
    if not outcomes:
        return
    init_db(db_path)
    context = f"{family or 'unattributed'}:{strategy_type or 'legacy_unattributed'}"
    with get_session(db_path) as session:
        for (source, source_listing_id), outcome in outcomes.items():
            row = session.get(SeenListing, _key(source, source_listing_id))
            if row is None:
                continue
            stage_a_outcomes = json.loads(row.stage_a_outcomes_json or "{}")
            if not isinstance(stage_a_outcomes, dict):
                stage_a_outcomes = {}
            stage_a_outcomes[context] = outcome
            row.stage_a_outcomes_json = json.dumps(stage_a_outcomes, sort_keys=True)
        session.commit()


def record_decision_classifications(
    classifications: dict[tuple[str, str], str],
    db_path: str | Path = DB_PATH,
    *,
    decision_logic_version: str,
) -> None:
    """Persist the latest bounded Stage-B/final decision without storing credentials."""
    if not classifications:
        return
    init_db(db_path)
    now = datetime.now(UTC)
    with get_session(db_path) as session:
        for (source, source_listing_id), classification in classifications.items():
            row = session.get(SeenListing, _key(source, source_listing_id))
            if row is None:
                continue
            row.decision_logic_version = decision_logic_version
            row.decision_classification = classification
            row.decision_at = now
        session.commit()


def update_health(db_path: str | Path = DB_PATH, **values: object) -> None:
    init_db(db_path)
    with get_session(db_path) as session:
        row = session.get(RuntimeHealthState, 1)
        if row is None:
            row = RuntimeHealthState()
            session.add(row)
        for name, value in values.items():
            if hasattr(row, name):
                setattr(row, name, value)
        session.commit()


def get_health(db_path: str | Path = DB_PATH) -> RuntimeHealthState:
    init_db(db_path)
    with get_session(db_path) as session:
        row = session.get(RuntimeHealthState, 1)
        if row is None:
            row = RuntimeHealthState()
            session.add(row)
            session.commit()
            session.refresh(row)
        session.expunge(row)
        return row


def hourly_metrics(db_path: str | Path = DB_PATH) -> dict[str, object]:
    init_db(db_path)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    with get_session(db_path) as session:
        rows = session.exec(select(ScanEvent).where(ScanEvent.recorded_at >= cutoff)).all()
    totals: dict[str, object] = {
        "queries": len(rows),
        "search_pages": 0,
        "cards": 0,
        "unique": 0,
        "duplicates": 0,
        "stage_a_rejected": 0,
        "stage_a_telemetry_scans": 0,
        "stage_a_telemetry_unique": 0,
        "stage_a_brand_confirmed": 0,
        "stage_a_category_valid": 0,
        "stage_a_eligible": 0,
        "stage_a_capped": 0,
        "stage_b_candidates": 0,
        "stage_b_enriched": 0,
        "stage_b_failed": 0,
        "stage_b_rejected": 0,
        "comp_lookups": 0,
        "comp_cache_hits": 0,
        "comp_cache_misses": 0,
        "final_candidates": 0,
        "alerts": 0,
        "decision_telemetry_scans": 0,
        "suppressed_alerts": 0,
        "failed_alerts": 0,
        "logic_reconsidered": 0,
        "routes": {},
        "alert_routes": {},
        "theses": {},
        "alert_theses": {},
        "opportunities": {},
        "strategy_types": {},
        "stage_a_paths": {},
        "stage_a_reject_reasons": {},
        "post_detail_reject_reasons": {},
        "piece_relationships": {},
        "confidence_counts": {},
        "recovery_counts": {},
        "post_detail_decisions": [],
        "detected_family_funnel": {},
    }
    numeric = [
        key
        for key in totals
        if key
        not in {
            "queries",
            "stage_a_telemetry_scans",
            "stage_a_telemetry_unique",
            "decision_telemetry_scans",
            "routes",
            "alert_routes",
            "theses",
            "alert_theses",
            "opportunities",
            "strategy_types",
            "stage_a_paths",
            "stage_a_reject_reasons",
            "post_detail_reject_reasons",
            "piece_relationships",
            "confidence_counts",
            "recovery_counts",
            "post_detail_decisions",
            "detected_family_funnel",
        }
    ]
    routes: dict[str, int] = {}
    alert_routes: dict[str, int] = {}
    theses: dict[str, int] = {}
    alert_theses: dict[str, int] = {}
    opportunities: dict[str, int] = {}
    strategy_types: dict[str, int] = {}
    stage_a_paths: dict[str, int] = {}
    stage_a_reject_reasons: dict[str, int] = {}
    post_detail_reject_reasons: dict[str, int] = {}
    piece_relationships: dict[str, int] = {}
    confidence_counts: dict[str, Any] = {}
    recovery_counts: dict[str, int] = {}
    post_detail_decisions: list[dict[str, Any]] = []
    detected_family_funnel: dict[str, dict[str, int]] = {}
    for row in rows:
        if row.stage_a_telemetry_version:
            totals["stage_a_telemetry_scans"] = int(totals["stage_a_telemetry_scans"]) + 1
            totals["stage_a_telemetry_unique"] = int(totals["stage_a_telemetry_unique"]) + (
                row.unique_seen or 0
            )
        if row.decision_telemetry_version:
            totals["decision_telemetry_scans"] = int(totals["decision_telemetry_scans"]) + 1
        mapping = {
            "search_pages": row.search_pages,
            "cards": row.cards_seen,
            "unique": row.unique_seen,
            "duplicates": row.duplicates,
            "stage_a_rejected": row.stage_a_rejected,
            "stage_a_brand_confirmed": row.stage_a_brand_confirmed or 0,
            "stage_a_category_valid": row.stage_a_category_valid or 0,
            "stage_a_eligible": row.stage_a_eligible or 0,
            "stage_a_capped": row.stage_a_capped or 0,
            "stage_b_candidates": row.stage_b_candidates,
            "stage_b_enriched": row.stage_b_enriched,
            "stage_b_failed": row.stage_b_failed or 0,
            "stage_b_rejected": row.stage_b_rejected or 0,
            "comp_lookups": row.comp_lookups,
            "comp_cache_hits": row.comp_cache_hits,
            "comp_cache_misses": row.comp_cache_misses,
            "final_candidates": row.final_candidates,
            "alerts": row.alerts,
            "suppressed_alerts": row.suppressed_alerts or 0,
            "failed_alerts": row.failed_alerts or 0,
            "logic_reconsidered": row.logic_reconsidered or 0,
        }
        for key in numeric:
            totals[key] = int(totals[key]) + mapping[key]
        for route, count in json.loads(row.routes_json).items():
            routes[route] = routes.get(route, 0) + int(count)
        for route, count in json.loads(row.alert_routes_json or "{}").items():
            alert_routes[route] = alert_routes.get(route, 0) + int(count)
        for thesis, count in json.loads(row.theses_json or "{}").items():
            theses[thesis] = theses.get(thesis, 0) + int(count)
        _merge_counts(alert_theses, _json_object(row.alert_theses_json))
        for kind, count in json.loads(row.opportunities_json or "{}").items():
            opportunities[kind] = opportunities.get(kind, 0) + int(count)
        strategy_type = row.strategy_type or "legacy_unattributed"
        strategy_types[strategy_type] = strategy_types.get(strategy_type, 0) + 1
        for path, count in json.loads(row.stage_a_paths_json or "{}").items():
            stage_a_paths[path] = stage_a_paths.get(path, 0) + int(count)
        for reason, count in json.loads(row.stage_a_reject_reasons_json or "{}").items():
            stage_a_reject_reasons[reason] = stage_a_reject_reasons.get(reason, 0) + int(count)
        _merge_counts(
            post_detail_reject_reasons,
            _json_object(row.post_detail_reject_reasons_json),
        )
        _merge_counts(piece_relationships, _json_object(row.piece_relationships_json))
        _merge_counts(confidence_counts, _json_object(row.confidence_counts_json))
        _merge_counts(recovery_counts, _json_object(row.recovery_counts_json))
        post_detail_decisions.extend(_json_list(row.post_detail_decisions_json))
        for family, funnel in json.loads(row.detected_family_funnel_json or "{}").items():
            family_bucket = detected_family_funnel.setdefault(family, {})
            for stage, count in funnel.items():
                family_bucket[stage] = family_bucket.get(stage, 0) + int(count)
    totals["routes"] = routes
    totals["alert_routes"] = alert_routes
    totals["theses"] = theses
    totals["alert_theses"] = alert_theses
    totals["opportunities"] = opportunities
    totals["strategy_types"] = strategy_types
    totals["stage_a_paths"] = stage_a_paths
    totals["stage_a_reject_reasons"] = stage_a_reject_reasons
    totals["post_detail_reject_reasons"] = post_detail_reject_reasons
    totals["piece_relationships"] = piece_relationships
    totals["confidence_counts"] = confidence_counts
    totals["recovery_counts"] = recovery_counts
    # Keep interactive health output diagnostic without allowing an hour of
    # candidate records to become an unbounded phone/terminal wall of text.
    totals["post_detail_decisions"] = post_detail_decisions[-12:]
    totals["detected_family_funnel"] = detected_family_funnel
    return totals


def strategy_metrics(
    db_path: str | Path = DB_PATH,
    *,
    hours: int = 24,
    group_by: str = "strategy_id",
) -> dict[str, dict[str, object]]:
    """Aggregate discovery yield; funnel rates use unique listings as denominator."""
    if group_by not in {"strategy_id", "strategy_type"}:
        raise ValueError("strategy metrics can group by strategy_id or strategy_type")
    init_db(db_path)
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, hours))
    with get_session(db_path) as session:
        rows = session.exec(select(ScanEvent).where(ScanEvent.recorded_at >= cutoff)).all()
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        key = (
            row.strategy_type or "legacy_unattributed"
            if group_by == "strategy_type"
            else row.strategy_id
            or f"{row.strategy_type or 'legacy_unattributed'}:{row.query.casefold()}"
        )
        bucket = grouped.setdefault(
            key,
            {
                "strategy_id": row.strategy_id,
                "strategy_type": row.strategy_type or "legacy_unattributed",
                "family": row.family,
                "query": row.query,
                "provenance": row.provenance,
                "filters": json.loads(row.filters_json or "{}"),
                "scans": 0,
                "cards": 0,
                "unique": 0,
                "duplicates": 0,
                "brand_confirmed": 0,
                "category_valid": 0,
                "stage_a_eligible": 0,
                "stage_a_capped": 0,
                "stage_a_telemetry_scans": 0,
                "stage_a_telemetry_unique": 0,
                "stage_a_pass": 0,
                "stage_b": 0,
                "stage_b_enriched": 0,
                "stage_b_failed": 0,
                "stage_b_rejected": 0,
                "finals": 0,
                "alerts": 0,
                "decision_telemetry_scans": 0,
                "suppressed_alerts": 0,
                "failed_alerts": 0,
                "logic_reconsidered": 0,
                "routes": {},
                "alert_routes": {},
                "alert_theses": {},
                "stage_a_paths": {},
                "stage_a_reject_reasons": {},
                "post_detail_reject_reasons": {},
                "piece_relationships": {},
                "confidence_counts": {},
                "recovery_counts": {},
                "detected_family_funnel": {},
            },
        )
        bucket["scans"] = int(bucket["scans"]) + 1
        bucket["cards"] = int(bucket["cards"]) + row.cards_seen
        bucket["unique"] = int(bucket["unique"]) + row.unique_seen
        bucket["duplicates"] = int(bucket["duplicates"]) + row.duplicates
        if row.stage_a_telemetry_version:
            bucket["stage_a_telemetry_scans"] = int(bucket["stage_a_telemetry_scans"]) + 1
            bucket["stage_a_telemetry_unique"] = int(bucket["stage_a_telemetry_unique"]) + (
                row.unique_seen or 0
            )
        if row.decision_telemetry_version:
            bucket["decision_telemetry_scans"] = int(bucket["decision_telemetry_scans"]) + 1
        bucket["brand_confirmed"] = int(bucket["brand_confirmed"]) + (
            row.stage_a_brand_confirmed or 0
        )
        bucket["category_valid"] = int(bucket["category_valid"]) + (row.stage_a_category_valid or 0)
        bucket["stage_a_eligible"] = int(bucket["stage_a_eligible"]) + (row.stage_a_eligible or 0)
        bucket["stage_a_capped"] = int(bucket["stage_a_capped"]) + (row.stage_a_capped or 0)
        bucket["stage_a_pass"] = int(bucket["stage_a_pass"]) + max(
            0, row.unique_seen - (row.stage_a_rejected or 0)
        )
        bucket["stage_b"] = int(bucket["stage_b"]) + row.stage_b_candidates
        bucket["stage_b_enriched"] = int(bucket["stage_b_enriched"]) + (row.stage_b_enriched or 0)
        bucket["stage_b_failed"] = int(bucket["stage_b_failed"]) + (row.stage_b_failed or 0)
        bucket["stage_b_rejected"] = int(bucket["stage_b_rejected"]) + (row.stage_b_rejected or 0)
        bucket["finals"] = int(bucket["finals"]) + row.final_candidates
        bucket["alerts"] = int(bucket["alerts"]) + row.alerts
        bucket["suppressed_alerts"] = int(bucket["suppressed_alerts"]) + (
            row.suppressed_alerts or 0
        )
        bucket["failed_alerts"] = int(bucket["failed_alerts"]) + (row.failed_alerts or 0)
        bucket["logic_reconsidered"] = int(bucket["logic_reconsidered"]) + (
            row.logic_reconsidered or 0
        )
        routes = bucket["routes"]
        if isinstance(routes, dict):
            for route, count in json.loads(row.routes_json or "{}").items():
                routes[route] = routes.get(route, 0) + int(count)
        alert_routes = bucket["alert_routes"]
        if isinstance(alert_routes, dict):
            for route, count in json.loads(row.alert_routes_json or "{}").items():
                alert_routes[route] = alert_routes.get(route, 0) + int(count)
        alert_theses = bucket["alert_theses"]
        if isinstance(alert_theses, dict):
            _merge_counts(alert_theses, _json_object(row.alert_theses_json))
        paths = bucket["stage_a_paths"]
        if isinstance(paths, dict):
            for path, count in json.loads(row.stage_a_paths_json or "{}").items():
                paths[path] = paths.get(path, 0) + int(count)
        reasons = bucket["stage_a_reject_reasons"]
        if isinstance(reasons, dict):
            for reason, count in json.loads(row.stage_a_reject_reasons_json or "{}").items():
                reasons[reason] = reasons.get(reason, 0) + int(count)
        post_detail_reasons = bucket["post_detail_reject_reasons"]
        if isinstance(post_detail_reasons, dict):
            _merge_counts(
                post_detail_reasons,
                _json_object(row.post_detail_reject_reasons_json),
            )
        relationships = bucket["piece_relationships"]
        if isinstance(relationships, dict):
            _merge_counts(relationships, _json_object(row.piece_relationships_json))
        confidence = bucket["confidence_counts"]
        if isinstance(confidence, dict):
            _merge_counts(confidence, _json_object(row.confidence_counts_json))
        recoveries = bucket["recovery_counts"]
        if isinstance(recoveries, dict):
            _merge_counts(recoveries, _json_object(row.recovery_counts_json))
        detected = bucket["detected_family_funnel"]
        if isinstance(detected, dict):
            for family, funnel in json.loads(row.detected_family_funnel_json or "{}").items():
                family_bucket = detected.setdefault(family, {})
                for stage, count in funnel.items():
                    family_bucket[stage] = family_bucket.get(stage, 0) + int(count)
    for bucket in grouped.values():
        unique = int(bucket["unique"])
        denominator = unique if unique > 0 else 1
        telemetry_unique = int(bucket["stage_a_telemetry_unique"])
        telemetry_denominator = telemetry_unique if telemetry_unique > 0 else 1
        bucket["stage_a_pass_rate"] = int(bucket["stage_a_pass"]) / denominator if unique else 0.0
        bucket["brand_confirmed_rate"] = (
            int(bucket["brand_confirmed"]) / telemetry_denominator if telemetry_unique else 0.0
        )
        bucket["category_valid_rate"] = (
            int(bucket["category_valid"]) / telemetry_denominator if telemetry_unique else 0.0
        )
        bucket["stage_a_eligible_rate"] = (
            int(bucket["stage_a_eligible"]) / telemetry_denominator if telemetry_unique else 0.0
        )
        bucket["stage_b_rate"] = int(bucket["stage_b"]) / denominator if unique else 0.0
        bucket["final_candidate_rate"] = int(bucket["finals"]) / denominator if unique else 0.0
    return grouped
