from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
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
    lifetime_first_seen: int | None = Field(default=0)
    historical_revisits: int | None = Field(default=0)
    material_state_changes: int | None = Field(default=0)
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
    # Nullable/additive forensic fields.  They intentionally store only compact,
    # sanitized decisions; raw marketplace HTML and browser/session data never
    # belong in SQLite.
    first_search_page: int | None = None
    first_search_rank: int | None = None
    last_search_page: int | None = None
    last_search_rank: int | None = None
    first_inspected_at: datetime | None = None
    last_inspected_at: datetime | None = None
    latest_trace_json: str | None = None
    event_history_json: str | None = None
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
    # `unique_listings_scanned` predates lifetime-ID telemetry and is retained
    # as a legacy processed-pass counter. This additive field starts cleanly
    # at migration time and counts only genuinely first-seen marketplace IDs.
    lifetime_first_seen_count: int | None = Field(default=0)
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
    lifetime_first_seen: int | None = Field(default=0)
    historical_revisits: int | None = Field(default=0)
    material_state_changes: int | None = Field(default=0)


@dataclass
class DedupeResult:
    is_duplicate: bool
    unchanged: bool
    logic_reconsidered: bool = False
    lifetime_first_seen: bool = False
    material_state_changed: bool = False
    revisit_reason: str | None = None


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


_TRACE_MAX_BYTES = 3_000
_TRACE_HISTORY_MAX_BYTES = 12_000
_CHEAP_TRACE_RETENTION_DAYS = 14
_TRACE_PRUNE_INTERVAL = timedelta(hours=6)
_last_trace_prune_at: datetime | None = None


def _short_trace_value(value: Any, limit: int = 180) -> Any:
    """Keep forensic labels readable without allowing marketplace text blobs."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(value.encode("utf-8")) <= limit:
        return value
    return value.encode("utf-8")[: limit - 3].decode("utf-8", errors="ignore") + "..."


def _trace_fallback(trace: dict[str, Any]) -> dict[str, Any]:
    """Retain the cheap-reject decision path when a caller supplies a large trace."""
    scalar_keys = (
        "recorded_at",
        "query",
        "family",
        "query_layer",
        "strategy_id",
        "strategy_type",
        "search_page",
        "search_rank",
        "title",
        "current_price_jpy",
        "buyout_price_jpy",
        "actionable_price_jpy",
        "actionable_price_kind",
        "time_remaining",
        "auction_maturity",
        "revisit_reason",
        "detail_status",
        "decision",
        "route",
        "alert_event",
        "alert_outcome",
    )
    compact = {
        key: _short_trace_value(trace[key])
        for key in scalar_keys
        if trace.get(key) is not None
    }
    stage_a = trace.get("stage_a")
    attention = trace.get("attention")
    if isinstance(attention, dict):
        compact["attention"] = {
            key: _short_trace_value(attention[key], 60)
            for key in ("role", "delivery") if key in attention
        }
    if isinstance(stage_a, dict):
        stage_a_keys = (
            "selected",
            "eligible",
            "eligible_paths",
            "primary_path",
            "reject_reason",
            "inspection_score",
            "brand_confirmed",
            "detected_family",
            "target_brand_match",
            "category_valid",
            "item_type",
            "item_type_confident",
            "value_inspection_candidate",
            "actionable_price",
            "price_in_discovery_band",
            "discovery_price_fraction",
            "actionable_buyout_fraction",
            "piece_relationship",
            "hard_veto",
            "reasons",
        )
        compact["stage_a"] = {
            key: _short_trace_value(stage_a[key])
            for key in stage_a_keys
            if stage_a.get(key) is not None
        }
    compact["trace_truncated"] = True
    return compact


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
        {"broad": 0.35, "medium": 0.15, "surgical": 0.05, "market_structure": 0.45}
        if has_market_structure
        else {"broad": 0.5, "medium": 0.3, "surgical": 0.2}
    )
    for layer, value in (layer_shares or {}).items():
        if layer in shares and float(value) > 0:
            shares[layer] = float(value)
    runs_by_layer: dict[str, int] = {}
    runs_by_strategy: dict[str, int] = {}
    runs_by_family: dict[str, int] = {}
    active_runs_by_layer: dict[str, int] = {}
    active_runs_by_strategy: dict[str, int] = {}
    lateness_cycles_by_identity: dict[str, float] = {}
    exploration_samples_by_identity: dict[str, int] = {}
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
            # A failed parser/source attempt consumed capacity for quota
            # accounting, but it did not actually cover that family.
            if event.family and not event.failed:
                runs_by_family[event.family] = runs_by_family.get(event.family, 0) + 1
        for spec in specs:
            row = session.get(QueryRuntimeState, _query_key(spec))
            if row:
                active_runs_by_layer[spec.query_layer] = (
                    active_runs_by_layer.get(spec.query_layer, 0) + row.runs
                )
                if spec.strategy_type:
                    active_runs_by_strategy[spec.strategy_type] = (
                        active_runs_by_strategy.get(spec.strategy_type, 0) + row.runs
                    )
            runs = row.runs if row else 0
            # Retain sample state for temporarily ineligible specs so the
            # scheduler can see that the last run consumed a bootstrap slot.
            exploration_samples_by_identity[spec.runtime_identity] = min(2, runs)
            if row and row.next_eligible_at:
                eligible = row.next_eligible_at
                if eligible.tzinfo is None:
                    eligible = eligible.replace(tzinfo=UTC)
                if eligible > current:
                    continue
            exploration = 8 / (1 + runs)
            staleness = 0.0
            lateness_cycles = 0.0
            if row and row.last_run_at:
                last_run = row.last_run_at
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=UTC)
                overdue_minutes = max(
                    0.0,
                    (current - last_run).total_seconds() / 60 - spec.cooldown_minutes,
                )
                staleness = min(4.0, overdue_minutes / max(1, spec.cooldown_minutes))
                lateness_cycles = overdue_minutes / max(1, spec.cooldown_minutes)
            lateness_cycles_by_identity[spec.runtime_identity] = lateness_cycles
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
    selected_type: str | None = None
    if selected_layer == "market_structure":
        by_strategy: dict[str, list[tuple[float, QuerySpec]]] = {}
        for candidate in candidates:
            by_strategy.setdefault(candidate[1].strategy_type or "market_price_band", []).append(
                candidate
            )
        type_shares = {
            "market_buyout": 0.35,
            "market_ending": 0.25,
            "market_closing": 0.20,
            "market_price_band": 0.10,
            "market_explore": 0.10,
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
    # Give every newly introduced strategy two bounded exploration samples,
    # then let configured priority, cadence and sample-shrunk yield decide.
    # Capping the comparison at two avoids forcing a new strategy to catch up
    # with years of lifetime runs while still preventing immediate deletion.
    minimum_samples = min(
        exploration_samples_by_identity.get(item[1].runtime_identity, 0)
        for item in candidates
    )
    if minimum_samples < 2:
        under_sampled = [
            item
            for item in candidates
            if exploration_samples_by_identity.get(item[1].runtime_identity, 0)
            == minimum_samples
        ]
        established = [
            item
            for item in candidates
            if exploration_samples_by_identity.get(item[1].runtime_identity, 0) >= 2
        ]

        def event_matches_spec(event: ScanEvent, spec: QuerySpec) -> bool:
            if event.family != spec.family or event.query != spec.query:
                return False
            if event.strategy_id and spec.strategy_id:
                return event.strategy_id == spec.strategy_id
            return True

        recent_bucket = [
            event
            for event in recent
            if event.query_layer == selected_layer
            and (selected_type is None or event.strategy_type == selected_type)
        ]
        last_was_exploration = bool(
            recent_bucket
            and any(
                event_matches_spec(recent_bucket[0], spec)
                and exploration_samples_by_identity.get(spec.runtime_identity, 0) < 2
                and spec.query_layer == selected_layer
                and (selected_type is None or spec.strategy_type == selected_type)
                for spec in specs
            )
        )
        # New strategies get a bounded alternating bootstrap rather than
        # freezing every established query in the bucket for a day or more.
        candidates = (
            established
            if established and last_was_exploration
            else under_sampled
        )
    # Layer/type quotas prevent one discovery philosophy from taking over, but
    # they do not by themselves stop a high-priority family with many aliases
    # from monopolizing each bucket. Reserve a bounded rolling exploration
    # floor for eligible families absent from the recent allocation window.
    # This changes query choice only; it never changes listing scores/routes.
    uncovered = [
        item for item in candidates if runs_by_family.get(item[1].family, 0) == 0
    ]
    if uncovered:
        candidates = uncovered
    # Priority should decide normal cadence, not permit permanent starvation.
    # Once an already-sampled query has waited three full cooldowns beyond its
    # eligible time, service the most overdue slice within the selected quota
    # bucket. This is bounded aging, not equal weighting or a yield reset.
    critically_overdue = [
        item
        for item in candidates
        if exploration_samples_by_identity.get(item[1].runtime_identity, 0) >= 2
        and lateness_cycles_by_identity.get(item[1].runtime_identity, 0.0) >= 3.0
    ]
    bucket_run_clock = (
        active_runs_by_strategy.get(selected_type, 0)
        if selected_type
        else active_runs_by_layer.get(selected_layer, 0)
    )
    force_overdue_service = bool(
        critically_overdue and bucket_run_clock % 2 == 0
    )
    if force_overdue_service:
        candidates = critically_overdue
    highest_family_runs = max(
        (runs_by_family.get(item[1].family, 0) for item in candidates),
        default=0,
    )

    def family_balanced_score(item: tuple[float, QuerySpec]) -> float:
        family_runs = runs_by_family.get(item[1].family, 0)
        coverage_debt = min(8.0, max(0, highest_family_runs - family_runs) * 2.0)
        return item[0] + coverage_debt

    return max(
        candidates,
        key=lambda item: (
            lateness_cycles_by_identity.get(item[1].runtime_identity, 0.0)
            if force_overdue_service
            else family_balanced_score(item),
            family_balanced_score(item),
        ),
    )[1]


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
        row.lifetime_first_seen = (row.lifetime_first_seen or 0) + int(
            getattr(result, "lifetime_first_seen", 0) or 0
        )
        row.historical_revisits = (row.historical_revisits or 0) + int(
            getattr(result, "historical_revisits", 0) or 0
        )
        row.material_state_changes = (row.material_state_changes or 0) + int(
            getattr(result, "material_state_changes", 0) or 0
        )
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
                lifetime_first_seen=int(getattr(result, "lifetime_first_seen", 0) or 0),
                historical_revisits=int(getattr(result, "historical_revisits", 0) or 0),
                material_state_changes=int(
                    getattr(result, "material_state_changes", 0) or 0
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
    fully_evaluated_freshness_hours: int = 24,
    search_page: int | None = None,
    search_rank: int | None = None,
    retry_incomplete_processing: bool = False,
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
                    first_search_page=search_page,
                    first_search_rank=search_rank,
                    last_search_page=search_page,
                    last_search_rank=search_rank,
                    decision_logic_version=decision_logic_version,
                )
            )
            session.commit()
            return DedupeResult(
                False,
                False,
                False,
                lifetime_first_seen=True,
                revisit_reason="lifetime_first_seen",
            )
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
        fully_evaluated = bool(
            row.decision_classification
            and (
                row.decision_classification.startswith("reject:")
                or row.decision_classification.startswith("final:")
            )
        )
        fully_evaluated_duplicate = bool(
            unchanged
            and "selected" in outcomes.values()
            and fully_evaluated
            and processed
            > now - timedelta(hours=max(freshness_hours, fully_evaluated_freshness_hours))
        )
        # A terminal classification is recorded only after the full pipeline
        # pass. If an unexpected exception occurs after this row is reserved,
        # the daemon's bounded retry must not hide unfinished work for the
        # normal freshness window.
        incomplete_processing_retry = bool(
            retry_incomplete_processing
            and unchanged
            and row.decision_classification is None
            and decision_logic_version
            and row.decision_logic_version == decision_logic_version
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
            unchanged
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
        duplicate = (
            (fresh_duplicate and not stage_a_revisit) or fully_evaluated_duplicate
        ) and not logic_reconsidered and not incomplete_processing_retry
        row.last_seen_at = now
        row.sightings += 1
        if not row.first_query:
            row.first_query = row.query
            row.first_strategy_id = None
            row.first_strategy_type = "legacy_unattributed"
        if row.first_search_page is None and search_page is not None:
            row.first_search_page = search_page
        if row.first_search_rank is None and search_rank is not None:
            row.first_search_rank = search_rank
        row.query = query
        row.last_strategy_id = strategy_id
        row.last_strategy_type = strategy_type
        if search_page is not None:
            row.last_search_page = search_page
        if search_rank is not None:
            row.last_search_rank = search_rank
        if not duplicate:
            row.signature = signature
            row.last_processed_at = now
            if decision_logic_version:
                row.decision_logic_version = decision_logic_version
        session.commit()
        if duplicate:
            revisit_reason = "unchanged_fully_evaluated" if fully_evaluated_duplicate else "fresh"
        elif logic_reconsidered:
            revisit_reason = "logic_reconsidered"
        elif not unchanged:
            revisit_reason = "material_state_changed"
        elif stage_a_revisit:
            revisit_reason = "candidate_rotation"
        elif incomplete_processing_retry:
            revisit_reason = "incomplete_processing_retry"
        else:
            revisit_reason = "freshness_expired"
        return DedupeResult(
            duplicate,
            unchanged,
            logic_reconsidered,
            # A failed pass never reached ScanEvent/health accounting. Carry
            # its first-seen event into the successful bounded retry exactly
            # as the daemon would have recorded it without the exception.
            lifetime_first_seen=incomplete_processing_retry,
            material_state_changed=not unchanged,
            revisit_reason=revisit_reason,
        )


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


def record_listing_traces(
    traces: dict[tuple[str, str], dict[str, Any]],
    db_path: str | Path = DB_PATH,
) -> None:
    """Persist compact per-listing diagnostics without retaining raw page content.

    Every processed listing gets one latest snapshot.  History is limited to
    meaningful candidate/decision transitions and twelve entries, keeping the
    production volume bounded while still making manual-benchmark misses
    explainable.
    """
    if not traces:
        return
    global _last_trace_prune_at

    init_db(db_path)
    now = datetime.now(UTC)
    with get_session(db_path) as session:
        for (source, source_listing_id), trace in traces.items():
            row = session.get(SeenListing, _key(source, source_listing_id))
            if row is None:
                continue
            compact = dict(trace)
            compact.setdefault("recorded_at", now.isoformat())
            encoded = json.dumps(
                compact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            # This is a diagnostic summary, not a second raw-listing store.
            if len(encoded.encode("utf-8")) > _TRACE_MAX_BYTES:
                compact.pop("reasons", None)
                compact.pop("evidence", None)
                compact["trace_truncated"] = True
                encoded = json.dumps(
                    compact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            if len(encoded.encode("utf-8")) > _TRACE_MAX_BYTES:
                compact = _trace_fallback(compact)
                encoded = json.dumps(
                    compact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            if len(encoded.encode("utf-8")) > _TRACE_MAX_BYTES:
                # Defensive last resort: this remains valid JSON and records
                # that a diagnostic existed without ever slicing UTF-8/JSON.
                compact = {
                    "recorded_at": _short_trace_value(compact.get("recorded_at"), 80),
                    "trace_truncated": True,
                }
                encoded = json.dumps(
                    compact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            row.latest_trace_json = encoded

            stage_a = compact.get("stage_a") or {}
            selected = bool(stage_a.get("selected"))
            capped = stage_a.get("reject_reason") == "candidate_budget"
            detail_status = str(compact.get("detail_status") or "")
            route = compact.get("route")
            alert_event = compact.get("alert_event")
            # A capped card is fully explained by its latest trace and compact
            # per-context Stage A outcome. Repeating every cap transition in
            # history would consume persistent volume without adding useful
            # manual-benchmark evidence.
            meaningful = bool(
                selected
                or detail_status in {"enriched", "failed"}
                or route
                or alert_event
            )
            if selected:
                row.first_inspected_at = row.first_inspected_at or now
                row.last_inspected_at = now
            if not meaningful:
                continue
            history = _json_list(row.event_history_json)
            event = {
                "at": _short_trace_value(compact["recorded_at"], 80),
                "query": _short_trace_value(compact.get("query")),
                "strategy": _short_trace_value(compact.get("strategy_type")),
                "stage_a": (
                    "selected"
                    if selected
                    else "capped"
                    if capped
                    else _short_trace_value(stage_a.get("reject_reason"))
                ),
                "detail": _short_trace_value(detail_status) if detail_status else None,
                "decision": _short_trace_value(compact.get("decision")),
                "route": _short_trace_value(route),
                "alert_event": _short_trace_value(alert_event),
                "price_jpy": _short_trace_value(
                    compact.get("actionable_price_jpy")
                ),
                "maturity": _short_trace_value(compact.get("auction_maturity")),
            }
            attention = compact.get("attention")
            if isinstance(attention, dict):
                event["attention_delivery"] = _short_trace_value(attention.get("delivery"), 30)
                event["attention_role"] = _short_trace_value(attention.get("role"), 40)
                event["alert_outcome"] = _short_trace_value(compact.get("alert_outcome"), 30)
            fingerprint = json.dumps(
                {key: value for key, value in event.items() if key != "at"},
                ensure_ascii=False,
                sort_keys=True,
            )
            prior_fingerprint = None
            if history:
                prior_fingerprint = json.dumps(
                    {key: value for key, value in history[-1].items() if key != "at"},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            if fingerprint != prior_fingerprint:
                history.append(event)
                history = history[-12:]
                history_encoded = json.dumps(
                    history,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                while (
                    len(history_encoded.encode("utf-8")) > _TRACE_HISTORY_MAX_BYTES
                    and len(history) > 1
                ):
                    history.pop(0)
                    history_encoded = json.dumps(
                        history,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                row.event_history_json = history_encoded
        # Cheap Stage A misses are valuable for the imminent manual benchmark,
        # but retaining their JSON forever would eventually fill the small
        # Railway volume.  Clear only old never-inspected traces; stable listing
        # identity, dedupe/classification columns, alert state, and every
        # inspected candidate history remain untouched. SQLite can reuse the
        # freed pages without a destructive VACUUM.
        if (
            _last_trace_prune_at is None
            or now - _last_trace_prune_at >= _TRACE_PRUNE_INTERVAL
        ):
            cutoff = now - timedelta(days=_CHEAP_TRACE_RETENTION_DAYS)
            session.exec(
                text(
                    """
                    UPDATE seenlisting
                    SET latest_trace_json = NULL
                    WHERE key IN (
                        SELECT key
                        FROM seenlisting
                        WHERE first_inspected_at IS NULL
                          AND latest_trace_json IS NOT NULL
                          AND last_processed_at < :cutoff
                        LIMIT 2000
                    )
                    """
                ),
                params={"cutoff": cutoff.replace(tzinfo=None).isoformat(sep=" ")},
            )
            _last_trace_prune_at = now
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
        "lifetime_first_seen": 0,
        "historical_revisits": 0,
        "material_state_changes": 0,
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
            "lifetime_first_seen": row.lifetime_first_seen or 0,
            "historical_revisits": row.historical_revisits or 0,
            "material_state_changes": row.material_state_changes or 0,
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
                "lifetime_first_seen": 0,
                "historical_revisits": 0,
                "material_state_changes": 0,
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
        bucket["lifetime_first_seen"] = int(bucket["lifetime_first_seen"]) + int(
            row.lifetime_first_seen or 0
        )
        bucket["historical_revisits"] = int(bucket["historical_revisits"]) + int(
            row.historical_revisits or 0
        )
        bucket["material_state_changes"] = int(bucket["material_state_changes"]) + int(
            row.material_state_changes or 0
        )
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
