from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlmodel import select

from buyeeye.cli import main
from buyeeye.db import get_session, save_alert_state
from buyeeye.discovery import QuerySpec
from buyeeye.live_sources import BuyeeAuthenticatedSource
from buyeeye.runtime_state import (
    QueryRuntimeState,
    ScanEvent,
    SeenListing,
    hourly_metrics,
    mark_listing_seen,
    record_query_result,
    record_stage_a_outcomes,
    strategy_metrics,
)


def _create_pre_stage_a_scan_event(db_path) -> None:
    """Create the exact ScanEvent shape deployed before Stage A telemetry."""
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE scanevent (
                id INTEGER PRIMARY KEY,
                recorded_at DATETIME NOT NULL,
                query VARCHAR NOT NULL,
                family VARCHAR NOT NULL,
                failed BOOLEAN NOT NULL,
                search_pages INTEGER NOT NULL,
                cards_seen INTEGER NOT NULL,
                unique_seen INTEGER NOT NULL,
                duplicates INTEGER NOT NULL,
                stage_a_rejected INTEGER NOT NULL,
                stage_b_candidates INTEGER NOT NULL,
                stage_b_enriched INTEGER NOT NULL,
                comp_lookups INTEGER NOT NULL,
                comp_cache_hits INTEGER NOT NULL,
                comp_cache_misses INTEGER NOT NULL,
                final_candidates INTEGER NOT NULL,
                alerts INTEGER NOT NULL,
                routes_json VARCHAR NOT NULL,
                theses_json VARCHAR,
                opportunities_json VARCHAR,
                provenance VARCHAR,
                query_layer VARCHAR,
                strategy_id VARCHAR,
                strategy_type VARCHAR,
                filters_json VARCHAR,
                alert_routes_json VARCHAR
            )
            """
        )
        connection.execute(
            """
            INSERT INTO scanevent (
                recorded_at, query, family, failed, search_pages, cards_seen,
                unique_seen, duplicates, stage_a_rejected, stage_b_candidates,
                stage_b_enriched, comp_lookups, comp_cache_hits,
                comp_cache_misses, final_candidates, alerts, routes_json,
                theses_json, opportunities_json, provenance, query_layer,
                strategy_id, strategy_type, filters_json, alert_routes_json
            ) VALUES (
                ?, 'Legacy Query', 'legacy', 0, 1, 10, 8, 2, 7, 1, 1,
                1, 0, 1, 0, 0, '{}', '{}', '{}', 'legacy.test', 'broad',
                NULL, 'semantic_broad', '{}', '{}'
            )
            """,
            (datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "),),
        )
        connection.execute(
            """
            CREATE TABLE seenlisting (
                key VARCHAR PRIMARY KEY,
                source VARCHAR NOT NULL,
                source_listing_id VARCHAR NOT NULL,
                signature VARCHAR NOT NULL,
                first_seen_at DATETIME NOT NULL,
                last_seen_at DATETIME NOT NULL,
                last_processed_at DATETIME NOT NULL,
                query VARCHAR NOT NULL,
                first_query VARCHAR,
                first_strategy_id VARCHAR,
                first_strategy_type VARCHAR,
                last_strategy_id VARCHAR,
                last_strategy_type VARCHAR,
                stage_a_outcomes_json VARCHAR,
                sightings INTEGER NOT NULL
            )
            """
        )
        now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
        connection.execute(
            """
            INSERT INTO seenlisting (
                key, source, source_listing_id, signature, first_seen_at,
                last_seen_at, last_processed_at, query, sightings
            ) VALUES (
                'legacy-seen', 'buyee', 'legacy-listing', 'legacy-signature',
                ?, ?, ?, 'Legacy Query', 1
            )
            """,
            (now, now, now),
        )


def test_stage_a_telemetry_migration_is_null_safe_and_new_events_are_attributed(
    tmp_path,
):
    db_path = tmp_path / "legacy-stage-a.db"
    _create_pre_stage_a_scan_event(db_path)

    legacy = hourly_metrics(db_path)
    assert legacy["unique"] == 8
    assert legacy["stage_a_brand_confirmed"] == 0
    assert legacy["stage_a_telemetry_scans"] == 0
    assert legacy["stage_a_telemetry_unique"] == 0
    assert legacy["stage_a_category_valid"] == 0
    assert legacy["stage_a_eligible"] == 0
    assert legacy["stage_a_capped"] == 0
    assert legacy["stage_b_failed"] == 0
    assert legacy["stage_b_rejected"] == 0
    assert legacy["decision_telemetry_scans"] == 0
    assert legacy["post_detail_reject_reasons"] == {}
    assert legacy["piece_relationships"] == {}
    assert legacy["confidence_counts"] == {}
    assert legacy["alert_theses"] == {}
    assert legacy["recovery_counts"] == {}
    assert legacy["suppressed_alerts"] == 0
    assert legacy["failed_alerts"] == 0
    assert legacy["logic_reconsidered"] == 0
    assert legacy["post_detail_decisions"] == []
    assert legacy["stage_a_paths"] == {}
    assert legacy["stage_a_reject_reasons"] == {}
    assert legacy["detected_family_funnel"] == {}

    legacy_strategy = strategy_metrics(db_path, group_by="strategy_type")["semantic_broad"]
    assert legacy_strategy["brand_confirmed"] == 0
    assert legacy_strategy["stage_b_rejected"] == 0

    with sqlite3.connect(db_path) as connection:
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(scanevent)")}
    for name in (
        "stage_a_brand_confirmed",
        "stage_a_telemetry_version",
        "stage_a_category_valid",
        "stage_a_eligible",
        "stage_a_capped",
        "stage_a_paths_json",
        "stage_a_reject_reasons_json",
        "stage_b_failed",
        "stage_b_rejected",
        "detected_family_funnel_json",
        "decision_telemetry_version",
        "post_detail_reject_reasons_json",
        "piece_relationships_json",
        "confidence_counts_json",
        "alert_theses_json",
        "recovery_counts_json",
        "suppressed_alerts",
        "failed_alerts",
        "logic_reconsidered",
        "post_detail_decisions_json",
    ):
        assert name in columns
        assert columns[name][3] == 0  # additive SQLite columns remain nullable

    with sqlite3.connect(db_path) as connection:
        seen_columns = {row[1]: row for row in connection.execute("PRAGMA table_info(seenlisting)")}
    for name in ("decision_logic_version", "decision_classification", "decision_at"):
        assert name in seen_columns
        assert seen_columns[name][3] == 0

    with get_session(db_path) as session:
        legacy_seen = session.exec(
            select(SeenListing).where(SeenListing.source_listing_id == "legacy-listing")
        ).one()
    assert legacy_seen.decision_logic_version is None
    assert legacy_seen.decision_classification is None

    spec = QuerySpec(
        "Maison Margiela",
        "test.market",
        "margiela",
        80,
        query_layer="broad",
    )
    result = SimpleNamespace(
        scanned=10,
        unique_listings=8,
        duplicates=2,
        stage_a_rejected=4,
        stage_a_brand_confirmed=7,
        stage_a_category_valid=6,
        stage_a_eligible=5,
        stage_a_capped=1,
        stage_a_path_counts={"market_buyout_inspection": 3, "unknown_interesting": 1},
        stage_a_reject_counts={"brand_unresolved": 2, "candidate_budget": 1},
        stage_b_candidates=4,
        stage_b_enriched=3,
        stage_b_failed=1,
        stage_b_rejected=2,
        comp_queried=3,
        final_candidates=1,
        new_alerts=1,
        route_counts={"buyouts": 1},
        alert_route_counts={"buyouts": 1},
        thesis_counts={"asymmetric_value": 1},
        opportunity_counts={"actionable_fixed_price": 1},
        post_detail_reject_counts={"missing_us_valuation_but_directional_weak": 2},
        piece_relationship_counts={"core": 2, "diffusion": 1},
        confidence_counts={
            "identity": {"high": 2, "medium": 1},
            "valuation": {"unavailable": 3},
            "directional": {"high": 1, "low": 2},
        },
        alert_thesis_counts={"directional_value": 1},
        recovery_counts={
            "parser_reload_attempts": 2,
            "parser_reload_successes": 1,
            "source_retries": 1,
        },
        suppressed_alerts=2,
        failed_alerts=1,
        logic_reconsidered=1,
        post_detail_decisions=[
            {"listing_id": f"candidate-{index}", "rejection_reason": "route_threshold"}
            for index in range(7)
        ],
        detected_family_funnel={
            "margiela": {
                "seen": 8,
                "brand_confirmed": 7,
                "category_valid": 6,
                "stage_a_candidates": 4,
                "stage_b_enriched": 3,
                "stage_b_failed": 1,
                "stage_b_rejected": 2,
                "finals": 1,
                "alerts": 1,
            }
        },
    )
    record_query_result(spec, result, db_path)

    totals = hourly_metrics(db_path)
    assert totals["stage_a_brand_confirmed"] == 7
    assert totals["stage_a_telemetry_scans"] == 1
    assert totals["stage_a_telemetry_unique"] == 8
    assert totals["stage_a_category_valid"] == 6
    assert totals["stage_a_eligible"] == 5
    assert totals["stage_a_capped"] == 1
    assert totals["stage_b_failed"] == 1
    assert totals["stage_b_rejected"] == 2
    assert totals["decision_telemetry_scans"] == 1
    assert totals["post_detail_reject_reasons"] == {"missing_us_valuation_but_directional_weak": 2}
    assert totals["piece_relationships"] == {"core": 2, "diffusion": 1}
    assert totals["confidence_counts"] == {
        "identity": {"high": 2, "medium": 1},
        "valuation": {"unavailable": 3},
        "directional": {"high": 1, "low": 2},
    }
    assert totals["alert_theses"] == {"directional_value": 1}
    assert totals["recovery_counts"] == {
        "parser_reload_attempts": 2,
        "parser_reload_successes": 1,
        "source_retries": 1,
    }
    assert totals["suppressed_alerts"] == 2
    assert totals["failed_alerts"] == 1
    assert totals["logic_reconsidered"] == 1
    assert len(totals["post_detail_decisions"]) == 6
    assert totals["stage_a_paths"] == {
        "market_buyout_inspection": 3,
        "unknown_interesting": 1,
    }
    assert totals["stage_a_reject_reasons"] == {
        "brand_unresolved": 2,
        "candidate_budget": 1,
    }
    assert totals["detected_family_funnel"]["margiela"]["finals"] == 1

    current_strategy = strategy_metrics(db_path, group_by="strategy_type")["semantic_broad"]
    assert current_strategy["stage_a_telemetry_scans"] == 1
    assert current_strategy["stage_a_telemetry_unique"] == 8
    assert current_strategy["brand_confirmed_rate"] == 7 / 8
    assert current_strategy["category_valid_rate"] == 6 / 8
    assert current_strategy["stage_a_eligible_rate"] == 5 / 8
    assert current_strategy["post_detail_reject_reasons"] == {
        "missing_us_valuation_but_directional_weak": 2
    }
    assert current_strategy["piece_relationships"] == {"core": 2, "diffusion": 1}
    assert current_strategy["confidence_counts"]["valuation"] == {"unavailable": 3}
    assert current_strategy["alert_theses"] == {"directional_value": 1}
    assert current_strategy["recovery_counts"]["source_retries"] == 1

    with get_session(db_path) as session:
        event = session.exec(
            select(ScanEvent).where(ScanEvent.decision_telemetry_version == 1)
        ).one()
    assert event.post_detail_decisions_json is not None
    assert len(json.loads(event.post_detail_decisions_json)) == 6

    with get_session(db_path) as session:
        runtime = session.exec(select(QueryRuntimeState)).one()
    assert runtime.stage_a_brand_confirmed == 7
    assert runtime.stage_b_failed == 1
    assert runtime.stage_b_rejected == 2


def test_global_dedupe_allows_only_new_stage_a_context_and_capped_rotation(tmp_path):
    db_path = tmp_path / "stage-a-dedupe.db"
    first = mark_listing_seen(
        "buyee",
        "contextual",
        "same-signature",
        "Maison Margiela",
        db_path,
        strategy_type="semantic_broad",
        family="margiela",
    )
    record_stage_a_outcomes(
        {("buyee", "contextual"): "ineligible"},
        db_path,
        strategy_type="semantic_broad",
        family="margiela",
    )
    market_revisit = mark_listing_seen(
        "buyee",
        "contextual",
        "same-signature",
        "Maison Margiela",
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )
    record_stage_a_outcomes(
        {("buyee", "contextual"): "selected"},
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )
    selected_duplicate = mark_listing_seen(
        "buyee",
        "contextual",
        "same-signature",
        "Maison Margiela",
        db_path,
        strategy_type="semantic_medium",
        family="margiela",
    )

    assert first.is_duplicate is False
    assert market_revisit.is_duplicate is False
    assert market_revisit.unchanged is True
    assert selected_duplicate.is_duplicate is True

    mark_listing_seen(
        "buyee",
        "capped",
        "same-signature",
        "Maison Margiela",
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )
    record_stage_a_outcomes(
        {("buyee", "capped"): "capped"},
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )
    capped_revisit = mark_listing_seen(
        "buyee",
        "capped",
        "same-signature",
        "Maison Margiela",
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )

    assert capped_revisit.is_duplicate is False
    assert capped_revisit.unchanged is True


def test_capped_context_survives_other_context_and_family_is_part_of_identity(tmp_path):
    db_path = tmp_path / "stage-a-contexts.db"
    mark_listing_seen(
        "buyee",
        "rotating",
        "same-signature",
        "Maison Margiela",
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )
    record_stage_a_outcomes(
        {("buyee", "rotating"): "capped"},
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )
    semantic = mark_listing_seen(
        "buyee",
        "rotating",
        "same-signature",
        "Maison Margiela",
        db_path,
        strategy_type="semantic_broad",
        family="margiela",
    )
    record_stage_a_outcomes(
        {("buyee", "rotating"): "ineligible"},
        db_path,
        strategy_type="semantic_broad",
        family="margiela",
    )
    market_again = mark_listing_seen(
        "buyee",
        "rotating",
        "same-signature",
        "Maison Margiela",
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )

    assert semantic.is_duplicate is False
    assert market_again.is_duplicate is False

    mark_listing_seen(
        "buyee",
        "neighbor",
        "same-signature",
        "Maison Margiela",
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )
    record_stage_a_outcomes(
        {("buyee", "neighbor"): "ineligible"},
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )
    correct_family = mark_listing_seen(
        "buyee",
        "neighbor",
        "same-signature",
        "Vetements",
        db_path,
        strategy_type="market_buyout",
        family="vetements",
    )

    assert correct_family.is_duplicate is False


def test_signature_change_resets_stage_a_context_outcomes(tmp_path):
    db_path = tmp_path / "stage-a-signature.db"
    mark_listing_seen(
        "buyee",
        "changed",
        "old-signature",
        "Maison Margiela",
        db_path,
        strategy_type="semantic_broad",
        family="margiela",
    )
    record_stage_a_outcomes(
        {("buyee", "changed"): "ineligible"},
        db_path,
        strategy_type="semantic_broad",
        family="margiela",
    )
    changed = mark_listing_seen(
        "buyee",
        "changed",
        "new-signature",
        "Maison Margiela",
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )
    record_stage_a_outcomes(
        {("buyee", "changed"): "ineligible"},
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )
    prior_context_after_change = mark_listing_seen(
        "buyee",
        "changed",
        "new-signature",
        "Maison Margiela",
        db_path,
        strategy_type="semantic_broad",
        family="margiela",
    )

    assert changed.is_duplicate is False
    assert changed.unchanged is False
    assert prior_context_after_change.is_duplicate is False


def test_decision_logic_reconsideration_is_one_shot_for_prior_stage_b_reject(tmp_path):
    db_path = tmp_path / "logic-reconsideration.db"
    mark_listing_seen(
        "buyee",
        "prior-reject",
        "unchanged-signature",
        "Maison Margiela",
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )
    record_stage_a_outcomes(
        {("buyee", "prior-reject"): "selected"},
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )

    reconsidered = mark_listing_seen(
        "buyee",
        "prior-reject",
        "unchanged-signature",
        "Maison Margiela",
        db_path,
        strategy_type="market_buyout",
        family="margiela",
        decision_logic_version="directional-v2",
        allow_logic_reconsideration=True,
    )
    same_version = mark_listing_seen(
        "buyee",
        "prior-reject",
        "unchanged-signature",
        "Maison Margiela",
        db_path,
        strategy_type="market_buyout",
        family="margiela",
        decision_logic_version="directional-v2",
        allow_logic_reconsideration=True,
    )

    assert reconsidered.is_duplicate is False
    assert reconsidered.unchanged is True
    assert reconsidered.logic_reconsidered is True
    assert same_version.is_duplicate is True
    assert same_version.logic_reconsidered is False

    with get_session(db_path) as session:
        row = session.exec(
            select(SeenListing).where(SeenListing.source_listing_id == "prior-reject")
        ).one()
    assert row.decision_logic_version == "directional-v2"


def test_decision_logic_change_never_replays_a_previously_alerted_listing(tmp_path):
    db_path = tmp_path / "logic-alert-safety.db"
    mark_listing_seen(
        "buyee",
        "prior-alert",
        "unchanged-signature",
        "Number (N)ine",
        db_path,
        strategy_type="semantic_surgical",
        family="number_nine",
    )
    record_stage_a_outcomes(
        {("buyee", "prior-alert"): "selected"},
        db_path,
        strategy_type="semantic_surgical",
        family="number_nine",
    )
    save_alert_state(
        "buyee",
        "prior-alert",
        "historical-alert-signature",
        "grails",
        db_path,
    )

    result = mark_listing_seen(
        "buyee",
        "prior-alert",
        "unchanged-signature",
        "Number (N)ine",
        db_path,
        strategy_type="semantic_surgical",
        family="number_nine",
        decision_logic_version="directional-v2",
        allow_logic_reconsideration=True,
    )

    assert result.is_duplicate is True
    assert result.unchanged is True
    assert result.logic_reconsidered is False

    with get_session(db_path) as session:
        row = session.exec(
            select(SeenListing).where(SeenListing.source_listing_id == "prior-alert")
        ).one()
    assert row.decision_logic_version == "directional-v2"


def test_same_decision_logic_version_preserves_normal_global_dedupe(tmp_path):
    db_path = tmp_path / "same-logic-version.db"
    first = mark_listing_seen(
        "buyee",
        "current-version",
        "same-signature",
        "Rick Owens",
        db_path,
        strategy_type="market_buyout",
        family="rick_owens",
        decision_logic_version="directional-v2",
    )
    record_stage_a_outcomes(
        {("buyee", "current-version"): "selected"},
        db_path,
        strategy_type="market_buyout",
        family="rick_owens",
    )
    duplicate = mark_listing_seen(
        "buyee",
        "current-version",
        "same-signature",
        "Rick Owens",
        db_path,
        strategy_type="market_buyout",
        family="rick_owens",
        decision_logic_version="directional-v2",
        allow_logic_reconsideration=True,
    )

    assert first.is_duplicate is False
    assert duplicate.is_duplicate is True
    assert duplicate.logic_reconsidered is False


def test_decision_logic_reconsideration_has_a_persistent_hourly_flood_cap(tmp_path):
    db_path = tmp_path / "logic-reconsideration-cap.db"
    mark_listing_seen(
        "buyee",
        "queued-prior-reject",
        "unchanged-signature",
        "Maison Margiela",
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )
    record_stage_a_outcomes(
        {("buyee", "queued-prior-reject"): "selected"},
        db_path,
        strategy_type="market_buyout",
        family="margiela",
    )
    with get_session(db_path) as session:
        session.add(
            ScanEvent(
                recorded_at=datetime.now(UTC),
                query="Prior reconsideration",
                family="margiela",
                logic_reconsidered=2,
            )
        )
        session.commit()

    capped = mark_listing_seen(
        "buyee",
        "queued-prior-reject",
        "unchanged-signature",
        "Maison Margiela",
        db_path,
        strategy_type="market_buyout",
        family="margiela",
        decision_logic_version="directional-v2",
        allow_logic_reconsideration=True,
        logic_reconsideration_max_per_hour=2,
    )

    assert capped.is_duplicate is True
    assert capped.logic_reconsidered is False
    with get_session(db_path) as session:
        row = session.exec(
            select(SeenListing).where(
                SeenListing.source_listing_id == "queued-prior-reject"
            )
        ).one()
    # The listing remains queued for a later hour; it is not replayed now and
    # does not silently consume its one logic-version reconsideration.
    assert row.decision_logic_version is None


def test_health_exposes_stage_a_recognition_telemetry(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "health.db"
    monkeypatch.setattr("buyeeye.cli.runtime_state_path", lambda *args: db_path)
    monkeypatch.setattr(
        "buyeeye.cli.health_report",
        lambda path: {
            "database": str(path),
            "database_ready": True,
            "session_ready": True,
        },
    )

    # A fresh database exposes telemetry but is not yet a healthy live scanner.
    assert main(["health"]) == 1
    output = capsys.readouterr().out

    assert "stage_a_telemetry_scans=0" in output
    assert "decision_telemetry_scans=0" in output
    assert "brand_confirmed=0" in output
    assert "category_valid=0" in output
    assert "stage_a_eligible=0" in output
    assert "stage_a_capped=0" in output
    assert "post_detail_rejected=0" in output
    assert "post_detail_rejects={}" in output
    assert "relationships={}" in output
    assert "confidences={}" in output
    assert "alert_theses={}" in output
    assert "recoveries={}" in output
    assert "suppressed_alerts=0" in output
    assert "failed_alerts=0" in output
    assert "logic_reconsidered=0" in output
    assert "stage_a_paths={}" in output
    assert "stage_a_rejects={}" in output


def test_buyee_source_recovery_counters_are_distinct_from_daemon_retries() -> None:
    source = BuyeeAuthenticatedSource()

    assert source.parser_reload_attempts == 0
    assert source.parser_reload_successes == 0
