from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlmodel import select

from buyeeye.cli import main
from buyeeye.db import get_session
from buyeeye.discovery import QuerySpec
from buyeeye.runtime_state import (
    QueryRuntimeState,
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
    ):
        assert name in columns
        assert columns[name][3] == 0  # additive SQLite columns remain nullable

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
        stage_a_path_counts={"market_value": 3, "unknown_interesting": 1},
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
    assert totals["stage_a_paths"] == {
        "market_value": 3,
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

    assert main(["health"]) == 0
    output = capsys.readouterr().out

    assert "stage_a_telemetry_scans=0" in output
    assert "brand_confirmed=0" in output
    assert "category_valid=0" in output
    assert "stage_a_eligible=0" in output
    assert "stage_a_capped=0" in output
    assert "post_detail_rejected=0" in output
    assert "stage_a_paths={}" in output
    assert "stage_a_rejects={}" in output
