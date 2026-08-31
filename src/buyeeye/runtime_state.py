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
    priority: int
    runs: int = 0
    cards_seen: int = 0
    unique_seen: int = 0
    duplicates: int = 0
    stage_b_candidates: int = 0
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


@dataclass
class DedupeResult:
    is_duplicate: bool
    unchanged: bool


def _key(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def choose_query(
    specs: list[QuerySpec], db_path: str | Path = DB_PATH, now: datetime | None = None
) -> QuerySpec | None:
    init_db(db_path)
    current = now or datetime.now(UTC)
    ranked: list[tuple[float, QuerySpec]] = []
    with get_session(db_path) as session:
        for spec in specs:
            row = session.get(QueryRuntimeState, _key(spec.query.casefold()))
            if row and row.next_eligible_at:
                eligible = row.next_eligible_at
                if eligible.tzinfo is None:
                    eligible = eligible.replace(tzinfo=UTC)
                if eligible > current:
                    continue
            if row is None or row.runs == 0:
                adaptive = 25.0
                runs = 0
            else:
                candidate_rate = row.stage_b_candidates / max(1, row.unique_seen)
                duplicate_rate = row.duplicates / max(1, row.cards_seen)
                alert_rate = row.alerts / max(1, row.runs)
                adaptive = candidate_rate * 35 + alert_rate * 30 - duplicate_rate * 25
                runs = row.runs
            exploration = 12 / (1 + runs)
            ranked.append((spec.priority + adaptive + exploration, spec))
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def record_query_result(
    spec: QuerySpec,
    result: object,
    db_path: str | Path = DB_PATH,
    failed: bool = False,
) -> None:
    init_db(db_path)
    now = datetime.now(UTC)
    key = _key(spec.query.casefold())
    with get_session(db_path) as session:
        row = session.get(QueryRuntimeState, key)
        if row is None:
            row = QueryRuntimeState(
                key=key,
                query=spec.query,
                family=spec.family,
                provenance=spec.provenance,
                priority=spec.priority,
            )
            session.add(row)
        row.runs += 1
        row.cards_seen += int(getattr(result, "scanned", 0))
        row.unique_seen += int(getattr(result, "unique_listings", 0))
        row.duplicates += int(getattr(result, "duplicates", 0))
        row.stage_b_candidates += int(getattr(result, "stage_b_candidates", 0))
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
        row.query = query
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
    }
    numeric = [key for key in totals if key not in {"queries", "routes"}]
    routes: dict[str, int] = {}
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
    totals["routes"] = routes
    return totals
