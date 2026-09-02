from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import Field, SQLModel, select

from .db import DB_PATH, get_session, init_db
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
    stage_b_candidates: int = 0
    stage_b_enriched: int = 0
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
    stage_b_candidates: int = 0
    stage_b_enriched: int = 0
    comp_lookups: int = 0
    comp_cache_hits: int = 0
    comp_cache_misses: int = 0
    final_candidates: int = 0
    alerts: int = 0
    routes_json: str = "{}"
    alert_routes_json: str | None = None
    theses_json: str | None = None
    opportunities_json: str | None = None


@dataclass
class DedupeResult:
    is_duplicate: bool
    unchanged: bool


def _key(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


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
        row.stage_b_candidates += int(getattr(result, "stage_b_candidates", 0))
        row.stage_b_enriched = (row.stage_b_enriched or 0) + int(
            getattr(result, "stage_b_enriched", 0)
        )
        row.final_candidates += int(getattr(result, "final_candidates", 0))
        row.alerts += int(getattr(result, "new_alerts", 0))
        row.failures += int(failed)
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
                stage_b_candidates=int(getattr(result, "stage_b_candidates", 0)),
                stage_b_enriched=int(getattr(result, "stage_b_enriched", 0)),
                comp_lookups=int(getattr(result, "comp_queried", 0)),
                comp_cache_hits=int(getattr(result, "comp_cache_hits", 0)),
                comp_cache_misses=int(getattr(result, "comp_cache_misses", 0)),
                final_candidates=int(getattr(result, "final_candidates", 0)),
                alerts=int(getattr(result, "new_alerts", 0)),
                routes_json=json.dumps(getattr(result, "route_counts", {}) or {}),
                alert_routes_json=json.dumps(
                    getattr(result, "alert_route_counts", {}) or {}
                ),
                theses_json=json.dumps(getattr(result, "thesis_counts", {}) or {}),
                opportunities_json=json.dumps(getattr(result, "opportunity_counts", {}) or {}),
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
                )
            )
            session.commit()
            return DedupeResult(False, False)
        processed = row.last_processed_at
        if processed.tzinfo is None:
            processed = processed.replace(tzinfo=UTC)
        unchanged = row.signature == signature
        duplicate = unchanged and processed > now - timedelta(hours=freshness_hours)
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
        session.commit()
        return DedupeResult(duplicate, unchanged)


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
        "stage_b_candidates": 0,
        "stage_b_enriched": 0,
        "comp_lookups": 0,
        "comp_cache_hits": 0,
        "comp_cache_misses": 0,
        "final_candidates": 0,
        "alerts": 0,
        "routes": {},
        "alert_routes": {},
        "theses": {},
        "opportunities": {},
        "strategy_types": {},
    }
    numeric = [
        key
        for key in totals
        if key
        not in {
            "queries",
            "routes",
            "alert_routes",
            "theses",
            "opportunities",
            "strategy_types",
        }
    ]
    routes: dict[str, int] = {}
    alert_routes: dict[str, int] = {}
    theses: dict[str, int] = {}
    opportunities: dict[str, int] = {}
    strategy_types: dict[str, int] = {}
    for row in rows:
        mapping = {
            "search_pages": row.search_pages,
            "cards": row.cards_seen,
            "unique": row.unique_seen,
            "duplicates": row.duplicates,
            "stage_a_rejected": row.stage_a_rejected,
            "stage_b_candidates": row.stage_b_candidates,
            "stage_b_enriched": row.stage_b_enriched,
            "comp_lookups": row.comp_lookups,
            "comp_cache_hits": row.comp_cache_hits,
            "comp_cache_misses": row.comp_cache_misses,
            "final_candidates": row.final_candidates,
            "alerts": row.alerts,
        }
        for key in numeric:
            totals[key] = int(totals[key]) + mapping[key]
        for route, count in json.loads(row.routes_json).items():
            routes[route] = routes.get(route, 0) + int(count)
        for route, count in json.loads(row.alert_routes_json or "{}").items():
            alert_routes[route] = alert_routes.get(route, 0) + int(count)
        for thesis, count in json.loads(row.theses_json or "{}").items():
            theses[thesis] = theses.get(thesis, 0) + int(count)
        for kind, count in json.loads(row.opportunities_json or "{}").items():
            opportunities[kind] = opportunities.get(kind, 0) + int(count)
        strategy_type = row.strategy_type or "legacy_unattributed"
        strategy_types[strategy_type] = strategy_types.get(strategy_type, 0) + 1
    totals["routes"] = routes
    totals["alert_routes"] = alert_routes
    totals["theses"] = theses
    totals["opportunities"] = opportunities
    totals["strategy_types"] = strategy_types
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
                "stage_a_pass": 0,
                "stage_b": 0,
                "stage_b_enriched": 0,
                "finals": 0,
                "alerts": 0,
                "routes": {},
                "alert_routes": {},
            },
        )
        bucket["scans"] = int(bucket["scans"]) + 1
        bucket["cards"] = int(bucket["cards"]) + row.cards_seen
        bucket["unique"] = int(bucket["unique"]) + row.unique_seen
        bucket["duplicates"] = int(bucket["duplicates"]) + row.duplicates
        bucket["stage_a_pass"] = int(bucket["stage_a_pass"]) + max(
            0, row.unique_seen - (row.stage_a_rejected or 0)
        )
        bucket["stage_b"] = int(bucket["stage_b"]) + row.stage_b_candidates
        bucket["stage_b_enriched"] = int(bucket["stage_b_enriched"]) + (row.stage_b_enriched or 0)
        bucket["finals"] = int(bucket["finals"]) + row.final_candidates
        bucket["alerts"] = int(bucket["alerts"]) + row.alerts
        routes = bucket["routes"]
        if isinstance(routes, dict):
            for route, count in json.loads(row.routes_json or "{}").items():
                routes[route] = routes.get(route, 0) + int(count)
        alert_routes = bucket["alert_routes"]
        if isinstance(alert_routes, dict):
            for route, count in json.loads(row.alert_routes_json or "{}").items():
                alert_routes[route] = alert_routes.get(route, 0) + int(count)
    for bucket in grouped.values():
        unique = int(bucket["unique"])
        denominator = unique if unique > 0 else 1
        bucket["stage_a_pass_rate"] = (
            int(bucket["stage_a_pass"]) / denominator if unique else 0.0
        )
        bucket["stage_b_rate"] = int(bucket["stage_b"]) / denominator if unique else 0.0
        bucket["final_candidate_rate"] = (
            int(bucket["finals"]) / denominator if unique else 0.0
        )
    return grouped
