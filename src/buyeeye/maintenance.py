"""Small, restart-safe retention slices in the existing scanner, never a browser.

IDs and send/protection ledgers remain durable and therefore grow with new IDs.
Age/row/byte caps apply to the optional working set, excluding protected evidence
and a recent-active grace period. Freed SQLite pages are reused, not VACUUMed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError

from .db import DB_PATH, ListingProtection, get_session, init_db
from .runtime_state import MaintenanceState, ScanDailyAggregate  # noqa: F401 - register tables

DEFAULT_POLICY = {
    "enabled": True,
    "interval_seconds": 60,
    "batch_rows": 200,
    "max_slice_seconds": 1.0,
    "cheap_trace_days": 3,
    "candidate_trace_days": 14,
    "trace_max_rows": 50_000,
    "listing_days": 14,
    "listing_max_rows": 100_000,
    "active_grace_hours": 24,
    "detail_days": 7,
    "detail_max_rows": 5_000,
    "detail_max_bytes": 100_000_000,
    "cold_ledger_days": 30,
    "scan_days": 7,
    "scan_max_rows": 20_000,
    "aggregate_days": 90,
    "warning_free_bytes": 256_000_000,
    "warning_free_fraction": 0.05,
    "critical_free_bytes": 128_000_000,
    "critical_free_fraction": 0.03,
    "warning_cooldown_hours": 24,
}
PHASES = ("traces", "details", "listings", "cold_ledger", "scans", "aggregates")
_ready_paths: set[str] = set()
_pressure: dict[str, tuple[float, bool]] = {}


def _policy(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    return {**DEFAULT_POLICY, **((settings or {}).get("storage", {}))}


def _stamp(now: datetime) -> str:
    return now.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")


def _key(source: str, listing_id: str) -> str:
    return hashlib.sha256(f"{source}\0{listing_id}".encode()).hexdigest()


def _connect(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    uri = f"file:{Path(path).resolve()}?mode={'ro' if readonly else 'rw'}"
    connection = sqlite3.connect(uri, uri=True, timeout=0.1)
    connection.row_factory = sqlite3.Row
    if readonly:
        connection.execute("PRAGMA query_only=ON")
    return connection


def _prepare(path: str | Path) -> None:
    # One-time per process. Additive migrations are performed before acquisition,
    # never a long compaction inside a maintenance transaction.
    identity = str(Path(path).resolve())
    if identity in _ready_paths and Path(path).exists():
        return
    from . import comp_cache, detail_enrichment  # noqa: F401 - register cache tables

    init_db(path)
    with _connect(path) as connection:
        for table, column in (
            ("seenlisting", "last_seen_at"),
            ("seenlisting", "last_processed_at"),
            ("detailcache", "retrieved_at"),
            ("detailcache", "expires_at"),
        ):
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS ix_retention_{table}_{column} "
                f"ON {table}({column})"
            )
    _ready_paths.add(identity)


def protect_listing(
    source: str,
    listing_id: str,
    db_path: str | Path = DB_PATH,
    *,
    reason: str | None = None,
    feedback_label: str | None = None,
    purchased: bool = False,
    provenance: str = "user_cli",
    overwrite_human_feedback: bool = True,
    now: datetime | None = None,
) -> None:
    """Persist human evidence; do not certify authenticity or change routing."""
    if not source or not listing_id or len(listing_id) > 100:
        raise ValueError("source and listing ID are required")
    if not (reason or feedback_label or purchased):
        raise ValueError("a pin reason or explicit human outcome is required")
    init_db(db_path)
    current = now or datetime.now(UTC)
    with get_session(db_path) as session:
        key = _key(source, listing_id)
        row = session.get(ListingProtection, key)
        if row is None:
            row = ListingProtection(key=key, source=source, source_listing_id=listing_id)
            session.add(row)
        if reason and row.pin_reason != reason[:240] and (not row.pin_reason or overwrite_human_feedback):
            row.pinned_at = row.pinned_at or current
            row.pin_reason = reason[:240]
        label = feedback_label.strip()[:80] if feedback_label else None
        if label and (row.feedback_label != label or row.feedback_provenance != provenance[:100]):
            history = json.loads(row.feedback_history_json or "[]")
            already_seeded = any(event.get("label") == label and event.get("provenance") == provenance[:100] for event in history)
            if overwrite_human_feedback or not already_seeded:
                history.append({"at": current.isoformat(), "label": label, "provenance": provenance[:100]})
                # Human outcomes are tiny, intentional durable data. Unlike
                # per-scan diagnostics, never silently truncate this history.
                row.feedback_history_json = json.dumps(history, ensure_ascii=False)
            if overwrite_human_feedback or row.feedback_label is None:
                row.feedback_label = label
                row.feedback_at = current
                row.feedback_provenance = provenance[:100]
        if purchased:
            row.purchased_at = row.purchased_at or current
        session.commit()


def seed_protections(records: list[dict[str, Any]], db_path: str | Path) -> int:
    """Only an explicit finite human-maintained manifest can seed protection."""
    if len(records) > 500:
        raise ValueError("pin manifest is limited to 500 explicit records")
    from .diagnostics import marketplace_listing_identity

    for record in records:
        source, listing_id = marketplace_listing_identity(
            str(record.get("source_listing_id") or record.get("url") or ""),
            source=record.get("source"),
        )
        protect_listing(
            source, listing_id, db_path,
            reason=str(record.get("reason") or "explicit benchmark cohort"),
            feedback_label=record.get("feedback_label"),
            purchased=record.get("purchased") is True,
            provenance=str(record.get("provenance") or "explicit_benchmark_manifest"),
            overwrite_human_feedback=False,
        )
    return len(records)


def optional_diagnostics_allowed(db_path: str | Path) -> bool:
    """Fast cached pressure check; essential ID/alert/feedback writes stay enabled."""
    key = str(Path(db_path).resolve())
    cached = _pressure.get(key)
    if cached and time.monotonic() - cached[0] < 60:
        return not cached[1]
    try:
        with _connect(db_path, readonly=True) as connection:
            row = connection.execute("SELECT low_space FROM maintenancestate WHERE id=1").fetchone()
        low = bool(row and row[0])
    except sqlite3.Error:
        low = False
    _pressure[key] = (time.monotonic(), low)
    return not low


def _unprotected(alias: str = "s") -> str:
    return f"""NOT EXISTS (
      SELECT 1 FROM listingprotection p
      WHERE p.source={alias}.source AND p.source_listing_id={alias}.source_listing_id
    ) AND NOT EXISTS (
      SELECT 1 FROM alertstate a
      WHERE a.source={alias}.source AND a.source_listing_id={alias}.source_listing_id
    ) AND COALESCE({alias}.decision_classification,'') NOT LIKE 'pending_alert:%'"""


def _expire_traces(c: sqlite3.Connection, p: dict, now: datetime, limit: int) -> int:
    total = c.execute("SELECT COUNT(*) FROM seenlisting WHERE latest_trace_json IS NOT NULL").fetchone()[0]
    excess = max(0, total - int(p["trace_max_rows"]))
    rows = c.execute(f"""SELECT s.key FROM seenlisting s
      WHERE (s.latest_trace_json IS NOT NULL OR s.event_history_json IS NOT NULL)
      AND {_unprotected()} AND (
        (s.first_inspected_at IS NULL AND s.last_processed_at < :cheap)
        OR (s.first_inspected_at IS NOT NULL AND s.last_inspected_at < :candidate
            AND s.last_processed_at < :candidate)
        OR (:excess > 0 AND s.last_processed_at < :grace)
      ) ORDER BY s.last_processed_at LIMIT :limit""", {
        "cheap": _stamp(now - timedelta(days=float(p["cheap_trace_days"]))),
        "candidate": _stamp(now - timedelta(days=float(p["candidate_trace_days"]))),
        "grace": _stamp(now - timedelta(hours=float(p["active_grace_hours"]))),
        "excess": excess, "limit": min(limit, excess) if excess else limit,
    }).fetchall()
    c.executemany("""UPDATE seenlisting SET latest_trace_json=NULL,event_history_json=NULL,
      history_expired_at=?,history_expiry_reason='retention:diagnostics' WHERE key=?""",
      [(_stamp(now), row[0]) for row in rows])
    return len(rows)


def _expire_details(c: sqlite3.Connection, p: dict, now: datetime, limit: int) -> int:
    total, payload = c.execute("SELECT COUNT(*),COALESCE(SUM(length(CAST(observation_json AS BLOB))),0) FROM detailcache").fetchone()
    over = total > int(p["detail_max_rows"]) or payload > int(p["detail_max_bytes"])
    rows = c.execute(f"""SELECT d.key,s.key seen_key FROM detailcache d
      JOIN seenlisting s ON s.source=d.source AND s.source_listing_id=d.source_listing_id
      WHERE {_unprotected()} AND d.expires_at < :now AND s.last_seen_at < :grace
      AND (d.retrieved_at < :cutoff OR :over)
      ORDER BY d.retrieved_at LIMIT :limit""", {
        "now": _stamp(now), "cutoff": _stamp(now - timedelta(days=float(p["detail_days"]))),
        "over": over, "grace": _stamp(now - timedelta(hours=float(p["active_grace_hours"]))),
        "limit": limit,
    }).fetchall()
    c.executemany("UPDATE seenlisting SET detail_expired_at=? WHERE key=?", [(_stamp(now), row[1]) for row in rows])
    c.executemany("DELETE FROM detailcache WHERE key=?", [(row[0],) for row in rows])
    return len(rows)


def _expire_listings(c: sqlite3.Connection, p: dict, now: datetime, limit: int) -> int:
    total = c.execute("SELECT COUNT(*) FROM listing").fetchone()[0]
    excess = max(0, total - int(p["listing_max_rows"]))
    rows = c.execute(f"""SELECT l.id,s.key FROM listing l
      JOIN seenlisting s ON s.source=l.source AND s.source_listing_id=l.source_listing_id
      WHERE {_unprotected()} AND l.is_alerted=0 AND s.last_seen_at < :grace
      AND (s.last_seen_at < :cutoff OR :excess > 0)
      ORDER BY s.last_seen_at LIMIT :limit""", {
        "grace": _stamp(now - timedelta(hours=float(p["active_grace_hours"]))),
        "cutoff": _stamp(now - timedelta(days=float(p["listing_days"]))),
        "excess": excess, "limit": min(limit, excess) if excess else limit,
    }).fetchall()
    c.executemany("UPDATE seenlisting SET listing_expired_at=? WHERE key=?", [(_stamp(now), row[1]) for row in rows])
    c.executemany("DELETE FROM listing WHERE id=?", [(row[0],) for row in rows])
    return len(rows)


def _compact_cold_ledger(c: sqlite3.Connection, p: dict, now: datetime, limit: int) -> int:
    rows = c.execute(f"""SELECT s.key,s.stage_a_outcomes_json FROM seenlisting s WHERE {_unprotected()}
      AND s.last_seen_at < ? AND (s.query<>'' OR s.first_query IS NOT NULL
      OR s.latest_trace_json IS NOT NULL OR s.event_history_json IS NOT NULL)
      ORDER BY s.last_seen_at LIMIT ?""", (_stamp(now - timedelta(days=float(p["cold_ledger_days"]))), limit)).fetchall()
    c.executemany("""UPDATE seenlisting SET query='',first_query=NULL,
      first_strategy_id=NULL,first_strategy_type=NULL,last_strategy_id=NULL,last_strategy_type=NULL,
      stage_a_outcomes_json=?,latest_trace_json=NULL,event_history_json=NULL,
      history_expired_at=?,history_expiry_reason='retention:cold_metadata' WHERE key=?""",
      [(('{"retained_inspection":"selected"}' if "selected" in json.loads(row[1] or "{}").values() else None),
        _stamp(now), row[0]) for row in rows])
    return len(rows)


_COUNTERS = (
    "search_pages", "cards_seen", "unique_seen", "duplicates", "failed",
    "stage_a_rejected", "stage_a_brand_confirmed", "stage_a_category_valid",
    "stage_a_eligible", "stage_a_capped", "stage_b_candidates", "stage_b_enriched",
    "stage_b_failed", "stage_b_rejected", "comp_lookups", "final_candidates", "alerts",
    "suppressed_alerts", "failed_alerts", "lifetime_first_seen", "historical_revisits",
    "material_state_changes", "logic_reconsidered",
)


def _sum_json(previous: str | None, values: dict) -> str:
    result = json.loads(previous or "{}")
    for key, value in values.items():
        result[key] = int(result.get(key) or 0) + int(value or 0)
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def _aggregate_scans(c: sqlite3.Connection, p: dict, now: datetime, limit: int) -> int:
    total = c.execute("SELECT COUNT(*) FROM scanevent").fetchone()[0]
    excess = max(0, total - int(p["scan_max_rows"]))
    cutoff = now - timedelta(days=float(p["scan_days"]))
    if excess:
        cutoff = now - timedelta(hours=float(p["active_grace_hours"]))
    rows = c.execute("SELECT * FROM scanevent WHERE recorded_at < ? ORDER BY recorded_at LIMIT ?",
                     (_stamp(cutoff), min(limit, excess) if excess else limit)).fetchall()
    for row in rows:
        day, family, layer, strategy = str(row["recorded_at"])[:10], row["family"], row["query_layer"] or "legacy", row["strategy_type"] or "legacy"
        key = hashlib.sha256(f"{day}\0{family}\0{layer}\0{strategy}".encode()).hexdigest()
        prior = c.execute("SELECT * FROM scandailyaggregate WHERE key=?", (key,)).fetchone()
        counters = _sum_json(prior["counters_json"] if prior else None, {"scans": 1, **{field: row[field] for field in _COUNTERS}})
        routes = _sum_json(prior["routes_json"] if prior else None, json.loads(row["routes_json"] or "{}"))
        alert_routes = _sum_json(prior["alert_routes_json"] if prior else None, json.loads(row["alert_routes_json"] or "{}"))
        c.execute("""INSERT INTO scandailyaggregate(key,day,family,query_layer,strategy_type,counters_json,routes_json,alert_routes_json)
          VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
          counters_json=excluded.counters_json,routes_json=excluded.routes_json,alert_routes_json=excluded.alert_routes_json""",
          (key, day, family, layer, strategy, counters, routes, alert_routes))
    # Aggregate and delete are one transaction: interruption cannot double-count.
    c.executemany("DELETE FROM scanevent WHERE id=?", [(row["id"],) for row in rows])
    return len(rows)


def _expire_aggregates(c: sqlite3.Connection, p: dict, now: datetime, limit: int) -> int:
    rows = c.execute("SELECT key FROM scandailyaggregate WHERE day < ? ORDER BY day LIMIT ?",
                     ((now - timedelta(days=float(p["aggregate_days"]))).date().isoformat(), limit)).fetchall()
    c.executemany("DELETE FROM scandailyaggregate WHERE key=?", [(row[0],) for row in rows])
    return len(rows)


def run_maintenance_slice(
    db_path: str | Path = DB_PATH,
    settings: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    force: bool = False,
    notify: bool = False,
) -> dict[str, Any]:
    """At most one small phase per call; failure leaves essential state untouched."""
    p = _policy(settings)
    if not p["enabled"]:
        return {"outcome": "disabled"}
    current = now or datetime.now(UTC)
    probe_path = Path(db_path).resolve().parent
    while not probe_path.exists():
        probe_path = probe_path.parent
    usage = shutil.disk_usage(probe_path)
    low = usage.free < max(int(p["critical_free_bytes"]), usage.total * float(p["critical_free_fraction"]))
    warning = usage.free < max(int(p["warning_free_bytes"]), usage.total * float(p["warning_free_fraction"]))
    _pressure[str(Path(db_path).resolve())] = (time.monotonic(), low)
    try:
        _prepare(db_path)
    except (sqlite3.OperationalError, SQLAlchemyOperationalError):
        # Even first-start migration/index failure must not re-enable optional
        # payload growth on a critically full filesystem.
        return {"outcome": "deferred", "reason": "preparation_unavailable", "low_space": low}
    started = time.monotonic()
    c = _connect(db_path)
    result: dict[str, Any] = {"outcome": "not_due"}
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("""INSERT OR IGNORE INTO maintenancestate(id,policy_version,phase,runs,rows_processed,low_space)
          VALUES(1,'retention-v1',0,0,0,0)""")
        state = c.execute("SELECT * FROM maintenancestate WHERE id=1").fetchone()
        if not force and state["next_due_at"] and state["next_due_at"] > _stamp(current):
            c.rollback()
            return result
        phase = int(state["phase"] or 0) % len(PHASES)
        budget = min(2.0, max(0.05, float(p["max_slice_seconds"])))
        c.set_progress_handler(lambda: int(time.monotonic() - started > budget), 1000)
        limit = min(500, max(1, int(p["batch_rows"])))
        count = (_expire_traces, _expire_details, _expire_listings, _compact_cold_ledger,
                 _aggregate_scans, _expire_aggregates)[phase](c, p, current, limit)
        c.set_progress_handler(None, 0)
        result = {"outcome": "completed", "phase": PHASES[phase], "rows": count,
                  "free_bytes": usage.free, "low_space": low, "warning": warning}
        c.execute("""UPDATE maintenancestate SET phase=?,next_due_at=?,last_completed_at=?,
          runs=runs+1,rows_processed=rows_processed+?,last_result_json=?,low_space=?,free_bytes=? WHERE id=1""",
          ((phase + 1) % len(PHASES), _stamp(current + timedelta(seconds=max(10, int(p["interval_seconds"])))),
           _stamp(current), count, json.dumps(result), low, usage.free))
        c.commit()
    except sqlite3.OperationalError as exc:
        c.set_progress_handler(None, 0)
        c.rollback()
        result = {"outcome": "deferred", "error_type": type(exc).__name__,
                  "reason": "budget_or_database_busy", "low_space": low}
    finally:
        c.close()
    if result["outcome"] == "completed" and (result["rows"] or warning):
        logging.getLogger("buyeeye.runtime").info("storage_maintenance %s", json.dumps(result))
    if notify and warning and result["outcome"] == "completed":
        _warn_storage(db_path, current, usage.free, p)
    return result


def _warn_storage(path: str | Path, now: datetime, free: int, policy: dict) -> None:
    from .discord_alerts import send_status_event

    # Persist the attempt before network delivery: a failing webhook cannot spam
    # every scan. This optional warning never modifies listing alert history.
    with _connect(path) as c:
        row = c.execute("SELECT last_warning_attempt_at FROM maintenancestate WHERE id=1").fetchone()
        cutoff = _stamp(now - timedelta(hours=float(policy["warning_cooldown_hours"])))
        if row and row[0] and row[0] > cutoff:
            return
        c.execute("UPDATE maintenancestate SET last_warning_attempt_at=? WHERE id=1", (_stamp(now),))
    sent, _, _ = send_status_event(
        f"BuyEye storage needs attention: {free / 1_000_000:.0f} MB free on /data. "
        "Bounded retention is running; protect headroom before the volume fills.", "warning"
    )
    if sent:
        with _connect(path) as c:
            c.execute("UPDATE maintenancestate SET last_warning_sent_at=? WHERE id=1", (_stamp(now),))


def storage_summary(db_path: str | Path) -> dict[str, Any]:
    """Read-only capacity and retention state; no browser/session file contents."""
    path = Path(db_path)
    usage = shutil.disk_usage(path.parent)
    result: dict[str, Any] = {"database": str(path), "filesystem_bytes": usage.total,
                              "free_bytes": usage.free, "database_bytes": path.stat().st_size,
                              "read_only": True, "browser_modified": False}
    with _connect(path, readonly=True) as c:
        names = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result["rows"] = {name: c.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                          for name in ("seenlisting", "listing", "detailcache", "scanevent", "scandailyaggregate", "listingprotection", "alertsendledger") if name in names}
        result["maintenance"] = dict(c.execute("SELECT * FROM maintenancestate WHERE id=1").fetchone() or {}) if "maintenancestate" in names else None
        result["freelist_pages"] = c.execute("PRAGMA freelist_count").fetchone()[0]
        result["page_size"] = c.execute("PRAGMA page_size").fetchone()[0]
        result["journal_mode"] = c.execute("PRAGMA journal_mode").fetchone()[0]
    result["durability_note"] = "Listing IDs, alerts, human feedback and verified comps are retained; total storage is not mathematically bounded."
    return result
