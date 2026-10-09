"""Read-only calendar-window summary that survives normal raw-scan retention."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from .maintenance import _COUNTERS


def _counts(value: str | None) -> dict[str, int]:
    parsed = json.loads(value or "{}")
    if not isinstance(parsed, dict):
        raise TypeError("invalid retained counter object")
    return {str(key): int(count or 0) for key, count in parsed.items()}


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")


def trial_summary(
    db_path: str | Path, *, start_day: str, days: int = 7, now: datetime | None = None,
) -> dict[str, Any]:
    """Combine disjoint raw/rolled-up scan records in one consistent snapshot.

    Retention adds a scan to its daily rollup and deletes the raw row in the same
    transaction. Both representations can legitimately coexist for one day;
    excluding either entire day would lose data. BEGIN keeps a report from
    observing that transfer on two different sides of a maintenance commit.
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_day):
        raise ValueError("start-day must be YYYY-MM-DD in UTC")
    if not 1 <= days <= 90:
        raise ValueError("days must be between 1 and 90")
    start = datetime.combine(date.fromisoformat(start_day), time.min, UTC)
    end = start + timedelta(days=days)
    current = now or datetime.now(UTC)
    current = current.replace(tzinfo=current.tzinfo or UTC).astimezone(UTC)
    cutoff = min(end, current)
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    totals: Counter = Counter()
    routes: Counter = Counter()
    alert_routes: Counter = Counter()
    by_day: dict[str, dict[str, Any]] = {}
    for offset in range(days):
        day_start = start + timedelta(days=offset)
        by_day[day_start.date().isoformat()] = {
            "calendar_status": "complete" if day_start + timedelta(days=1) <= current
            else "in_progress" if day_start <= current else "future",
            "counters": Counter(), "raw_scans": 0, "rolled_up_scans": 0,
        }
    representation = {"raw_scans": 0, "rolled_up_scans": 0, "rollup_rows": 0}
    sends: dict[str, Any] = {
        "events": 0, "distinct_listing_ids": 0, "routes": {}, "theses": {}, "events_by_reason": {},
    }
    missing_tables = []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        def add(day: str, counters: dict, source: str, final_routes: str, sent_routes: str):
            bucket = by_day[day]
            bucket["counters"].update(counters)
            bucket[source] += counters.get("scans", 0)
            totals.update(counters)
            representation[source] += counters.get("scans", 0)
            routes.update(_counts(final_routes))
            alert_routes.update(_counts(sent_routes))

        if "scanevent" in tables:
            for row in connection.execute(
                "SELECT * FROM scanevent WHERE recorded_at >= ? AND recorded_at < ?",
                (_stamp(start), _stamp(cutoff)),
            ):
                values = dict(row)
                add(str(row["recorded_at"])[:10], {
                    "scans": 1, **{key: int(values.get(key) or 0) for key in _COUNTERS},
                }, "raw_scans", values.get("routes_json"), values.get("alert_routes_json"))
        else:
            missing_tables.append("scanevent")
        if "scandailyaggregate" in tables:
            for row in connection.execute(
                "SELECT * FROM scandailyaggregate WHERE day >= ? AND day < ? AND day <= ?",
                (start_day, end.date().isoformat(), current.date().isoformat()),
            ):
                add(row["day"], _counts(row["counters_json"]), "rolled_up_scans",
                    row["routes_json"], row["alert_routes_json"])
                representation["rollup_rows"] += 1
        else:
            missing_tables.append("scandailyaggregate")
        if "alertsendledger" in tables:
            bounds = (_stamp(start), _stamp(cutoff))
            sends["events"] = connection.execute(
                "SELECT COUNT(*) FROM alertsendledger WHERE sent_at >= ? AND sent_at < ?", bounds,
            ).fetchone()[0]
            sends["distinct_listing_ids"] = connection.execute(
                "SELECT COUNT(*) FROM (SELECT DISTINCT source,source_listing_id FROM alertsendledger "
                "WHERE sent_at >= ? AND sent_at < ?)", bounds,
            ).fetchone()[0]
            for field, name in (("route", "routes"), ("thesis", "theses"), ("event", "events_by_reason")):
                sends[name] = {
                    row[0] or "unrecorded": row[1] for row in connection.execute(
                        f"SELECT {field},COUNT(*) FROM alertsendledger WHERE sent_at >= ? "
                        f"AND sent_at < ? GROUP BY {field}", bounds,
                    )
                }
        else:
            missing_tables.append("alertsendledger")
        snapshots = {}
        for table, fields in (
            ("runtimehealthstate", ("started_at", "last_successful_scan", "last_verified_auth_success",
                                    "last_auth_observed_at", "last_auth_confirmed", "consecutive_source_failures")),
            ("maintenancestate", ("last_completed_at", "runs", "rows_processed", "low_space", "free_bytes")),
        ):
            row = connection.execute(f"SELECT * FROM {table} WHERE id=1").fetchone() if table in tables else None
            record = dict(row or {})
            snapshots[table] = {field: record.get(field) for field in fields} if row else None

    names = {"scans": "queries", "cards_seen": "cards", "unique_seen": "processable_passes"}

    def named(counters):
        return {names.get(key, key): int(counters.get(key, 0)) for key in ("scans", *_COUNTERS)}

    elapsed_hours = max(0, (cutoff - start).total_seconds() / 3600)
    return {
        "read_only": True, "generated_at": current.isoformat(),
        "window": {"start_inclusive": start.isoformat(), "end_exclusive": end.isoformat(),
                   "calendar_complete": current >= end, "elapsed_calendar_hours": elapsed_hours,
                   "days_with_scan_records": sum(bool(row["counters"]) for row in by_day.values())},
        "representation": representation, "missing_tables": missing_tables,
        "totals": named(totals), "final_routes": dict(routes), "scan_alert_routes": dict(alert_routes),
        "successful_sends": sends,
        "per_elapsed_calendar_hour": {
            name: round(count / elapsed_hours, 3) if elapsed_hours else None
            for name, count in (("cards", totals["cards_seen"]), ("inspections", totals["stage_b_candidates"]),
                                ("enriched", totals["stage_b_enriched"]), ("finals", totals["final_candidates"]),
                                ("successful_sends", sends["events"]))
        },
        "days": {day: {**row, "counters": named(row["counters"])} for day, row in by_day.items()},
        "current_snapshot": {"observed_at": current.isoformat(), "database_bytes": path.stat().st_size, **snapshots},
        "limitations": [
            "Cards, processable passes, inspections and enrichments can repeat listing IDs; only successful-send IDs are distinct here.",
            "Calendar coverage is not uptime proof; absent records can mean no scans or expired/missing telemetry.",
            "Raw scans and retained daily rollups are disjoint records, even when both contribute to the same day.",
            "Current health/storage snapshots are not historical resource or cost measurements.",
            "No owner usefulness/precision or marketplace-wide recall is inferred from these counts.",
        ],
    }
