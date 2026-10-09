from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest

from buyeeye.db import AlertSendLedger, get_session, init_db
from buyeeye.maintenance import DEFAULT_POLICY, _aggregate_scans
from buyeeye.runtime_state import ScanDailyAggregate, ScanEvent
from buyeeye.trial_report import trial_summary

NOW = datetime(2026, 10, 20, tzinfo=UTC)


def _scan(path, at, *, cards=10):
    with get_session(path) as session:
        session.add(ScanEvent(
            recorded_at=datetime.fromisoformat(at), query="fixture", family="fixture",
            cards_seen=cards, unique_seen=cards - 1, duplicates=1,
            stage_b_candidates=2, stage_b_enriched=1, final_candidates=1, alerts=1,
            routes_json='{"buyouts":1}', alert_routes_json='{"buyouts":1}',
        ))
        session.commit()


def test_mixed_raw_and_rollups_same_day_preserve_bounds_and_total(tmp_path):
    path = tmp_path / "trial.db"
    _scan(path, "2026-10-01T00:00:00")
    _scan(path, "2026-10-01T18:00:00", cards=20)
    _scan(path, "2026-10-07T23:59:59", cards=30)
    _scan(path, "2026-10-08T00:00:00", cards=999)
    with get_session(path) as session:
        session.add(ScanDailyAggregate(
            key="rolled", day="2026-10-01", family="fixture", query_layer="broad",
            strategy_type="semantic_broad", counters_json='{"scans":2,"cards_seen":40,"unique_seen":35}',
            routes_json='{"grails":2}', alert_routes_json='{"grails":1}',
        ))
        session.add(ScanDailyAggregate(
            key="before", day="2026-09-30", family="fixture", query_layer="broad",
            strategy_type="semantic_broad", counters_json='{"scans":99,"cards_seen":999}',
        ))
        session.commit()
    before = path.read_bytes()
    report = trial_summary(path, start_day="2026-10-01", now=NOW)
    assert path.read_bytes() == before
    assert report["read_only"]
    assert report["totals"]["queries"] == 5
    assert report["totals"]["cards"] == 100
    assert report["totals"]["processable_passes"] == 92
    assert report["totals"]["stage_b_candidates"] == 6
    assert report["representation"] == {"raw_scans": 3, "rolled_up_scans": 2, "rollup_rows": 1}
    assert report["days"]["2026-10-01"]["counters"]["cards"] == 70
    assert report["final_routes"] == {"buyouts": 3, "grails": 2}
    assert report["window"]["calendar_complete"]
    assert report["window"]["elapsed_calendar_hours"] == 168
    assert report["window"]["days_with_scan_records"] == 2


def test_partial_retention_move_and_rollback_never_double_count(tmp_path):
    path = tmp_path / "move.db"
    _scan(path, "2026-10-01T01:00:00")
    _scan(path, "2026-10-01T02:00:00", cards=20)
    before = trial_summary(path, start_day="2026-10-01", now=NOW)
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        assert _aggregate_scans(connection, DEFAULT_POLICY, NOW, 1) == 1
        connection.rollback()
    rolled_back = trial_summary(path, start_day="2026-10-01", now=NOW)
    assert rolled_back["totals"] == before["totals"]
    assert rolled_back["representation"]["rolled_up_scans"] == 0
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        assert _aggregate_scans(connection, DEFAULT_POLICY, NOW, 1) == 1
    partial = trial_summary(path, start_day="2026-10-01", now=NOW)
    assert partial["totals"] == before["totals"]
    assert partial["final_routes"] == before["final_routes"]
    assert partial["representation"] == {"raw_scans": 1, "rolled_up_scans": 1, "rollup_rows": 1}


def test_report_uses_one_snapshot_across_concurrent_retention_commit(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.db"
    _scan(path, "2026-10-01T01:00:00")
    _scan(path, "2026-10-01T02:00:00", cards=20)
    original_connect = sqlite3.connect
    with closing(original_connect(path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    moved = []

    class ReadConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if "SELECT * FROM scandailyaggregate WHERE" in sql and not moved:
                with closing(original_connect(path)) as writer, writer:
                    writer.row_factory = sqlite3.Row
                    moved.append(_aggregate_scans(writer, DEFAULT_POLICY, NOW, 1))
            return super().execute(sql, *args, **kwargs)

    monkeypatch.setattr("buyeeye.trial_report.sqlite3.connect", lambda *args, **kwargs: original_connect(
        *args, factory=ReadConnection, **kwargs,
    ))
    report = trial_summary(path, start_day="2026-10-01", now=NOW)
    assert moved == [1]
    assert report["totals"]["cards"] == 30
    assert report["representation"] == {"raw_scans": 2, "rolled_up_scans": 0, "rollup_rows": 0}


def test_actual_send_events_and_distinct_ids_are_separate(tmp_path):
    path = tmp_path / "sends.db"
    with get_session(path) as session:
        for listing_id, at, route, reason in (
            ("same", "2026-10-01T00:00:00", "buyouts", "initial_alert"),
            ("same", "2026-10-02T00:00:00", "ending", "entered_ending_soon"),
            ("other", "2026-10-07T23:59:59", "grails", "initial_alert"),
            ("excluded", "2026-10-08T00:00:00", "review", "initial_alert"),
        ):
            session.add(AlertSendLedger(
                source="buyee", source_listing_id=listing_id, sent_at=datetime.fromisoformat(at),
                signature="fixture", route=route, event=reason, thesis="fixture", delivery="ping",
            ))
        session.commit()
    report = trial_summary(path, start_day="2026-10-01", now=NOW)
    assert report["successful_sends"] == {
        "events": 3, "distinct_listing_ids": 2,
        "routes": {"buyouts": 1, "ending": 1, "grails": 1}, "theses": {"fixture": 3},
        "events_by_reason": {"initial_alert": 2, "entered_ending_soon": 1},
    }
    assert report["totals"]["alerts"] == 0  # Ledger evidence is not scan-counter inference.


def test_incomplete_current_day_and_future_days_are_explicit(tmp_path):
    path = tmp_path / "partial.db"
    _scan(path, "2026-10-02T01:00:00")
    _scan(path, "2026-10-02T18:00:00", cards=999)
    report = trial_summary(path, start_day="2026-10-01", now=datetime(2026, 10, 2, 12, tzinfo=UTC))
    assert not report["window"]["calendar_complete"]
    assert report["window"]["elapsed_calendar_hours"] == 36
    assert report["days"]["2026-10-01"]["calendar_status"] == "complete"
    assert report["days"]["2026-10-02"]["calendar_status"] == "in_progress"
    assert report["days"]["2026-10-03"]["calendar_status"] == "future"
    assert report["totals"]["cards"] == 10


@pytest.mark.parametrize(("start", "days"), [("20261001", 7), ("2026-13-01", 7), ("2026-10-01", 0), ("2026-10-01", 91)])
def test_invalid_window_cannot_create_database(tmp_path, start, days):
    path = tmp_path / "absent.db"
    with pytest.raises(ValueError):
        trial_summary(path, start_day=start, days=days)
    assert not path.exists()


def test_cli_is_read_only_and_emits_json(tmp_path, monkeypatch, capsys):
    from buyeeye import cli

    path = tmp_path / "cli.db"
    init_db(path)
    monkeypatch.setenv("BUYEYE_DB_PATH", str(path))
    monkeypatch.setattr(cli, "load_env_file", lambda: None)
    monkeypatch.setattr(cli, "init_db", lambda *_: pytest.fail("report must never migrate"))
    assert cli.main(["trial-summary", "--start-day", "2026-10-01", "--days", "7"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["read_only"]
    assert report["window"]["end_exclusive"] == "2026-10-08T00:00:00+00:00"


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "absent.db"
    with pytest.raises(FileNotFoundError):
        trial_summary(path, start_day="2026-10-01", now=NOW)
    assert not path.exists()
