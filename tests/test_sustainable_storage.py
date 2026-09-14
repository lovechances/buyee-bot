from __future__ import annotations

import json
import sqlite3
from collections import namedtuple
from datetime import UTC, datetime, timedelta

from buyeeye.cli import main
from buyeeye.db import (
    AlertSendLedger,
    Listing,
    ListingProtection,
    get_session,
    init_db,
    save_alert_state,
)
from buyeeye.detail_enrichment import DetailCache
from buyeeye.diagnostics import diagnose_listing
from buyeeye.maintenance import (
    _key,
    optional_diagnostics_allowed,
    protect_listing,
    run_maintenance_slice,
    seed_protections,
    storage_summary,
)
from buyeeye.runtime_state import (
    MaintenanceState,
    ScanDailyAggregate,
    ScanEvent,
    SeenListing,
    mark_listing_seen,
    record_listing_traces,
)

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _seed(path, listing_id="x100000001", *, age=40, pending=False, payload=4000):
    init_db(path)
    old = NOW - timedelta(days=age)
    key = _key("buyee", listing_id)
    with get_session(path) as s:
        s.add(SeenListing(key=key, source="buyee", source_listing_id=listing_id,
            signature="stable-signature", query="brand", first_query="original discovery",
            first_seen_at=old, last_seen_at=old, last_processed_at=old,
            latest_trace_json=json.dumps({"padding": "x" * payload}),
            event_history_json='[{"decision":"old"}]',
            decision_classification="pending_alert:cooldown" if pending else "stage_a:reject"))
        s.add(Listing(source="buyee", source_listing_id=listing_id,
            title_original="test actual garment", current_price=2000))
        s.add(DetailCache(key=key, source="buyee", source_listing_id=listing_id,
            retrieved_at=old, expires_at=old + timedelta(days=1), content_hash="fixture",
            observation_json=json.dumps({"description_original": "d" * payload})))
        s.commit()
    return key


def _cycles(path, count=6, *, now=NOW, policy=None):
    return [run_maintenance_slice(path, policy, force=True, now=now) for _ in range(count)]


def test_retention_executes_preserves_identity_and_reports_expired_history(tmp_path):
    path = tmp_path / "state.db"
    key = _seed(path)
    results = _cycles(path)
    assert all(row["outcome"] == "completed" for row in results)
    assert {row["phase"] for row in results} == {"traces", "details", "listings", "cold_ledger", "scans", "aggregates"}
    with get_session(path) as s:
        seen = s.get(SeenListing, key)
        assert seen and seen.signature == "stable-signature"
        assert seen.first_seen_at and seen.last_seen_at and seen.sightings == 1
        assert seen.latest_trace_json is None and seen.event_history_json is None
        assert s.get(DetailCache, key) is None
    report = diagnose_listing(path, "x100000001")
    assert report["seen"] is True
    assert report["retention"]["coverage"] == "partial_or_expired"
    assert "expired" in report["legacy_limitations"][0]
    seen_again = mark_listing_seen("buyee", "x100000001", "stable-signature", "new query", path)
    assert not seen_again.lifetime_first_seen
    assert seen_again.unchanged


def test_pins_feedback_purchases_alerts_pending_and_fresh_details_are_protected(tmp_path):
    path = tmp_path / "protected.db"
    ids = [f"x10000000{i}" for i in range(1, 7)]
    keys = [_seed(path, listing_id, pending=(i == 4), age=0 if i == 5 else 40)
            for i, listing_id in enumerate(ids)]
    protect_listing("buyee", ids[0], path, reason="finite benchmark")
    protect_listing("buyee", ids[1], path, feedback_label="GOOD", provenance="explicit user")
    protect_listing("buyee", ids[2], path, purchased=True)
    save_alert_state("buyee", ids[3], "alertsig", "buyouts", path,
                     material_identity="identity", alert_event="initial", delivery="quiet")
    _cycles(path, 18)
    with get_session(path) as s:
        for key in keys:
            assert s.get(SeenListing, key).latest_trace_json
            assert s.get(DetailCache, key)
        assert s.get(AlertSendLedger, 1).delivery == "quiet"
        assert s.get(ListingProtection, keys[2]).purchased_at


def test_restart_resume_is_idempotent_and_batches_never_exceed_cap(tmp_path):
    path = tmp_path / "restart.db"
    for i in range(9):
        _seed(path, f"x1000000{i:02d}")
    config = {"storage": {"batch_rows": 2}}
    first = run_maintenance_slice(path, config, force=True, now=NOW)
    assert first["phase"] == "traces" and first["rows"] == 2
    # A fresh call/process consults persisted phase rather than a module timer.
    assert run_maintenance_slice(path, config, now=NOW)["outcome"] == "not_due"
    second = run_maintenance_slice(path, config, now=NOW + timedelta(minutes=2))
    assert second["phase"] == "details" and second["rows"] == 2
    results = _cycles(path, 36, now=NOW + timedelta(days=1), policy=config)
    assert all(result["rows"] <= 2 for result in results)
    with get_session(path) as s:
        assert s.get(MaintenanceState, 1).runs == 38
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM seenlisting").fetchone()[0] == 9
        assert c.execute("SELECT count(*) FROM detailcache").fetchone()[0] == 0


def test_scan_rollup_is_atomic_and_recent_metrics_remain_available(tmp_path, monkeypatch):
    path = tmp_path / "scan.db"
    init_db(path)
    with get_session(path) as s:
        for age in (20, 20, 0):
            s.add(ScanEvent(recorded_at=NOW - timedelta(days=age), query="brand", family="brand",
                query_layer="broad", strategy_type="semantic_broad", cards_seen=10,
                lifetime_first_seen=3, alerts=1, routes_json='{"buyouts":1}', alert_routes_json='{"buyouts":1}'))
        s.add(MaintenanceState(phase=4))
        s.commit()
    from buyeeye import maintenance
    original = maintenance._sum_json
    calls = 0

    def interrupted(*args):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise sqlite3.OperationalError("interrupted")
        return original(*args)

    monkeypatch.setattr(maintenance, "_sum_json", interrupted)
    assert run_maintenance_slice(path, force=True, now=NOW)["outcome"] == "deferred"
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM scanevent").fetchone()[0] == 3
        assert c.execute("SELECT count(*) FROM scandailyaggregate").fetchone()[0] == 0
    monkeypatch.setattr(maintenance, "_sum_json", original)
    assert run_maintenance_slice(path, force=True, now=NOW)["rows"] == 2
    _cycles(path, 12)
    with get_session(path) as s:
        from sqlmodel import select
        aggregate = s.exec(select(ScanDailyAggregate)).one()
        assert json.loads(aggregate.counters_json)["cards_seen"] == 20
        assert json.loads(aggregate.counters_json)["lifetime_first_seen"] == 6
        assert json.loads(aggregate.alert_routes_json) == {"buyouts": 2}
        assert len(s.exec(select(ScanEvent)).all()) == 1


def test_row_and_byte_caps_work_across_multiple_growth_cycles(tmp_path):
    path = tmp_path / "growth.db"
    policy = {"storage": {"listing_days": 365, "detail_days": 365,
        "cheap_trace_days": 365, "candidate_trace_days": 365,
        "trace_max_rows": 3, "listing_max_rows": 3, "detail_max_rows": 20,
        "detail_max_bytes": 10_000, "batch_rows": 2}}
    for cycle in range(3):
        for i in range(4):
            _seed(path, f"x200000{cycle:01d}{i:02d}", age=3, payload=5000)
        _cycles(path, 36, policy=policy)
        with sqlite3.connect(path) as c:
            assert c.execute("SELECT count(*) FROM seenlisting WHERE latest_trace_json IS NOT NULL").fetchone()[0] <= 3
            assert c.execute("SELECT count(*) FROM listing").fetchone()[0] <= 3
            assert c.execute("SELECT coalesce(sum(length(cast(observation_json AS BLOB))),0) FROM detailcache").fetchone()[0] <= 10_000
            assert c.execute("SELECT count(*) FROM seenlisting").fetchone()[0] == (cycle + 1) * 4


def test_low_space_omits_only_optional_traces_and_warning_is_restart_deduped(tmp_path, monkeypatch):
    path = tmp_path / "low.db"
    key = _seed(path, age=0)
    disk = namedtuple("usage", "total used free")
    monkeypatch.setattr("buyeeye.maintenance.shutil.disk_usage", lambda _: disk(1_000_000_000, 990_000_000, 10_000_000))
    messages = []
    monkeypatch.setattr("buyeeye.discord_alerts.send_status_event", lambda message, level: (messages.append(message) or True, 204, 0.01))
    run_maintenance_slice(path, force=True, now=NOW, notify=True)
    assert not optional_diagnostics_allowed(path)
    record_listing_traces({("buyee", "x100000001"): {"stage_a": {"selected": False}}}, path)
    with get_session(path) as s:
        assert s.get(SeenListing, key).history_expiry_reason == "low_space:optional_trace_omitted"
    record_listing_traces({("buyee", "x100000001"): {"stage_a": {"selected": True}, "detail_status": "enriched"}}, path)
    with get_session(path) as s:
        assert json.loads(s.get(SeenListing, key).latest_trace_json)["stage_a"]["selected"]
    run_maintenance_slice(path, force=True, now=NOW + timedelta(hours=1), notify=True)
    assert len(messages) == 1
    run_maintenance_slice(path, force=True, now=NOW + timedelta(hours=25), notify=True)
    assert len(messages) == 2


def test_feedback_and_pin_cli_persist_without_eligibility_or_auth_overrides(tmp_path, monkeypatch, capsys):
    path = tmp_path / "feedback.db"
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.setenv("BUYEYE_DB_PATH", str(path))
    assert main(["feedback", "x100000001", "GOOD", "--purchased"]) == 0
    assert main(["pin-listing", "x100000001", "--reason", "explicit benchmark"]) == 0
    assert main(["feedback", "x100000001", "GOOD", "--purchased"]) == 0
    with get_session(path) as s:
        row = s.get(ListingProtection, _key("buyee", "x100000001"))
        assert row.feedback_label == "GOOD" and row.purchased_at and row.pinned_at
        assert len(json.loads(row.feedback_history_json)) == 1
        assert row.feedback_provenance == "explicit_user_cli"
    assert "no authenticity certification" in capsys.readouterr().out


def test_manifest_seed_is_finite_idempotent_and_never_creates_seen_evidence(tmp_path):
    path = tmp_path / "pins.db"
    records = [{"source": "buyee", "source_listing_id": "x100000001", "reason": "benchmark", "feedback_label": "authenticity_uncertain"}]
    seed_protections(records, path)
    seed_protections(records, path)
    with get_session(path) as s:
        row = s.get(ListingProtection, _key("buyee", "x100000001"))
        assert len(json.loads(row.feedback_history_json)) == 1
        assert row.purchased_at is None
    assert diagnose_listing(path, "x100000001")["seen"] is False


def test_storage_summary_and_backup_restore_rehearsal_are_non_mutating(tmp_path):
    path = tmp_path / "baseline.db"
    _seed(path)
    original = path.read_bytes()
    summary = storage_summary(path)
    assert summary["read_only"]
    assert path.read_bytes() == original
    copy = tmp_path / "working.db"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as source, sqlite3.connect(copy) as target:
        source.backup(target)
    _cycles(copy)
    with sqlite3.connect(copy) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("PRAGMA freelist_count").fetchone()[0] > 0
    assert path.read_bytes() == original
    assert diagnose_listing(copy, "x100000001")["seen"] is True


def test_verified_comps_and_alert_dedupe_survive_maintenance(tmp_path):
    from buyeeye.comp_cache import CompRepository
    from buyeeye.db import get_alert_state
    from buyeeye.models import Comp
    from buyeeye.pipeline import AlertMaterialSnapshot, _alert_event_decision

    path = tmp_path / "durable.db"
    _seed(path)
    CompRepository(path).put("local_sold", "exact piece", [Comp(source="local_sold", title="real sold", price_jpy=50000,
        source_listing_id="verified-us-1", is_sold=True, similarity=1.0, recency_days=1, price_evidence="realized")])
    save_alert_state("buyee", "x100000001", "stable", "grails", path,
        thesis="archive_grail", material_identity="same-piece", material_price_jpy=2000,
        material_price_kind="buyout", material_maturity_state="fixed_price",
        material_risk_state="low", material_evidence_state="unverified")
    _cycles(path, 18, now=NOW + timedelta(days=365))
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM soldcompcache").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM alertsendledger").fetchone()[0] == 1
    assert get_alert_state("buyee", "x100000001", path).signature == "stable"
    current = AlertMaterialSnapshot(route="grails", thesis="archive_grail", price_jpy=2000,
        price_kind="buyout", maturity_state="fixed_price", identity="same-piece",
        identity_confidence="high", risk_state="low", evidence_state="unverified",
        verified_median_jpy=None)
    assert not _alert_event_decision(get_alert_state("buyee", "x100000001", path), current).should_alert


def test_all_human_feedback_survives_and_startup_seed_cannot_overwrite_user(tmp_path):
    path = tmp_path / "human.db"
    manifest = [{"source_listing_id": "x100000001", "feedback_label": "unrated", "reason": "benchmark"}]
    seed_protections(manifest, path)
    for i in range(20):
        protect_listing("buyee", "x100000001", path, feedback_label=f"human-outcome-{i}", provenance="explicit_user_cli")
    seed_protections(manifest, path)
    with get_session(path) as s:
        row = s.get(ListingProtection, _key("buyee", "x100000001"))
        assert row.feedback_label == "human-outcome-19"
        history = json.loads(row.feedback_history_json)
        assert len(history) == 21
        assert history[1]["label"] == "human-outcome-0"


def test_health_readiness_requires_recent_useful_progress_not_just_a_db():
    from types import SimpleNamespace

    from buyeeye.cli import _health_readiness

    report = {"database_ready": True, "session_ready": True}
    runtime = SimpleNamespace(last_successful_scan=None, consecutive_source_failures=0)
    assert _health_readiness(report, runtime, now=NOW)[0] == "starting"
    runtime.last_successful_scan = NOW - timedelta(minutes=31)
    assert _health_readiness(report, runtime, now=NOW)[0] == "degraded"
    runtime.last_successful_scan = NOW - timedelta(minutes=2)
    assert _health_readiness(report, runtime, now=NOW) == ("ready", 2.0)
    runtime.consecutive_source_failures = 1
    assert _health_readiness(report, runtime, now=NOW)[0] == "degraded"
    assert _health_readiness({**report, "session_ready": False}, runtime, now=NOW)[0] == "degraded"


def test_preparation_failure_still_suppresses_optional_growth_at_low_space(tmp_path, monkeypatch):
    from buyeeye import maintenance
    path = tmp_path / "full.db"
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(maintenance.shutil, "disk_usage", lambda _: usage(1000, 999, 1))
    def unavailable(_):
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(maintenance, "_prepare", unavailable)
    assert run_maintenance_slice(path)["outcome"] == "deferred"
    assert not optional_diagnostics_allowed(path)


def test_overdue_failed_attempt_survives_capped_classification_not_success(tmp_path):
    path = tmp_path / "retry.db"
    key = _seed(path)
    with get_session(path) as s:
        row = s.get(SeenListing, key)
        row.last_inspected_at = datetime.now(UTC) - timedelta(hours=25)
        row.first_inspected_at = row.last_inspected_at
        row.decision_classification = "stage_a:candidate_budget"
        row.event_history_json = json.dumps([
            {"stage_a": "selected", "detail": "failed"},
            {"stage_a": "capped", "detail": None},
        ])
        s.commit()
    assert mark_listing_seen("buyee", "x100000001", "new-signature", "brand", path).detail_retry_overdue
    with get_session(path) as s:
        row = s.get(SeenListing, key)
        row.event_history_json = json.dumps([
            {"stage_a": "selected", "detail": "failed"},
            {"stage_a": "selected", "detail": "enriched"},
        ])
        s.commit()
    assert not mark_listing_seen("buyee", "x100000001", "new-signature", "brand", path).detail_retry_overdue


def test_recent_activity_preserves_old_candidate_history_and_detail(tmp_path):
    path = tmp_path / "active.db"
    key = _seed(path, age=40)
    with get_session(path) as s:
        row = s.get(SeenListing, key)
        row.last_seen_at = NOW
        row.last_processed_at = NOW
        row.first_inspected_at = NOW - timedelta(days=40)
        row.last_inspected_at = NOW - timedelta(days=40)
        s.commit()
    _cycles(path)
    with get_session(path) as s:
        assert s.get(SeenListing, key).latest_trace_json
        assert s.get(DetailCache, key)
