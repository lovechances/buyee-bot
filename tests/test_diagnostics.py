from __future__ import annotations

import json
import sqlite3

import pytest

from buyeeye.cli import main
from buyeeye.db import Listing, get_session, init_db
from buyeeye.diagnostics import (
    benchmark_listings,
    diagnose_listing,
    marketplace_listing_id,
    marketplace_listing_identity,
)
from buyeeye.runtime_state import (
    mark_listing_seen,
    record_decision_classifications,
    record_listing_traces,
    record_stage_a_outcomes,
)


def _create_legacy_seen_database(path) -> None:
    """Create the pre-forensics SeenListing shape without running migrations."""
    with sqlite3.connect(path) as connection:
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
                decision_logic_version VARCHAR,
                decision_classification VARCHAR,
                decision_at DATETIME,
                sightings INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO seenlisting (
                key, source, source_listing_id, signature, first_seen_at,
                last_seen_at, last_processed_at, query, first_query,
                first_strategy_id, first_strategy_type, last_strategy_id,
                last_strategy_type, stage_a_outcomes_json,
                decision_logic_version, decision_classification, decision_at,
                sightings
            ) VALUES (
                'legacy-key', 'buyee', 'x123456789', 'legacy-signature',
                '2026-09-01 12:00:00', '2026-09-01 13:00:00',
                '2026-09-01 13:00:00', 'Number (N)ine', 'Number (N)ine',
                'number_nine.broad', 'semantic_broad', 'number_nine.broad',
                'semantic_broad', '{"number_nine:semantic_broad":"selected"}',
                'legacy-v1', 'reject:insufficient_desirability',
                '2026-09-01 13:00:00', 2
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE queryruntimestate (
                key VARCHAR PRIMARY KEY,
                query VARCHAR NOT NULL,
                next_eligible_at DATETIME
            )
            """
        )
        connection.execute(
            """
            INSERT INTO queryruntimestate (key, query, next_eligible_at)
            VALUES ('legacy-query', 'Number (N)ine', '2026-09-01 14:00:00')
            """
        )


def test_marketplace_listing_id_parses_only_supported_auction_forms():
    assert (
        marketplace_listing_id(
            "https://buyee.jp/item/jdirectitems/auction/x123456789"
            "?conversionType=jdirectitems_auction_DirectSearch"
        )
        == "x123456789"
    )
    assert (
        marketplace_listing_id("https://page.auctions.yahoo.co.jp/jp/auction/h987654321#item")
        == "h987654321"
    )
    assert marketplace_listing_id("s1242650927") == "s1242650927"

    with pytest.raises(ValueError):
        marketplace_listing_id("https://example.com/item/unrelated/not-a-listing")


def test_marketplace_identity_accepts_paypay_url_and_bare_id() -> None:
    url = (
        "https://buyee.jp/paypayfleamarket/item/z609539002"
        "?conversionType=jdirectitems_auction_DirectSearch"
    )

    assert marketplace_listing_identity(url) == ("buyee_paypay", "z609539002")
    assert marketplace_listing_identity("z609539002") == (
        "buyee_paypay",
        "z609539002",
    )
    assert marketplace_listing_identity("z609539002", source="jdirectitems") == (
        "buyee",
        "z609539002",
    )


def test_benchmark_listings_batches_read_only_diagnostics_and_miss_categories(
    tmp_path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "benchmark.db"
    mark_listing_seen(
        "buyee",
        "b1157261953",
        "signature-a",
        "Number (N)ine",
        db_path,
        family="number_nine",
        strategy_type="semantic_broad",
    )
    record_decision_classifications(
        {("buyee", "b1157261953"): "stage_a:insufficient_card_evidence"},
        db_path,
        decision_logic_version="test-v1",
    )
    benchmark_path = tmp_path / "benchmark.txt"
    benchmark_path.write_text(
        "b1157261953\tinteresting tee\n"
        "x999999999\n"
        "https://buyee.jp/paypayfleamarket/item/z609539002\n",
        encoding="utf-8",
    )
    before = db_path.read_bytes()

    report = benchmark_listings(db_path, benchmark_path)

    assert report["read_only"] is True
    assert report["browser_used"] is False
    assert report["discord_used"] is False
    assert report["total"] == 3
    assert report["seen"] == 1
    assert report["outcomes"] == {
        "discovery_miss": 1,
        "source_coverage_miss": 1,
        "stage_a_reject:insufficient_card_evidence": 1,
    }
    assert report["results"][0]["annotation"] == "interesting tee"
    assert db_path.read_bytes() == before

    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.setenv("BUYEYE_DB_PATH", str(db_path))
    assert main(["benchmark-listings", str(benchmark_path)]) == 0
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["outcomes"] == report["outcomes"]


def test_diagnose_listing_unseen_is_non_mutating_and_cli_returns_one(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "unseen.db"
    sqlite3.connect(db_path).close()
    before = db_path.read_bytes()

    report = diagnose_listing(db_path, "x000000001")

    assert report["marketplace_id"] == "x000000001"
    assert report["seen"] is False
    assert report["listing"] is None
    assert db_path.read_bytes() == before

    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.setenv("BUYEYE_DB_PATH", str(db_path))
    assert main(["diagnose-listing", "x000000001"]) == 1
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["seen"] is False


def test_diagnose_listing_tolerates_legacy_schema_without_migrating_it(tmp_path):
    db_path = tmp_path / "legacy.db"
    _create_legacy_seen_database(db_path)
    with sqlite3.connect(db_path) as connection:
        columns_before = [
            row[1] for row in connection.execute("PRAGMA table_info(seenlisting)")
        ]

    report = diagnose_listing(
        db_path,
        "https://buyee.jp/item/jdirectitems/auction/x123456789",
    )

    assert report["seen"] is True
    assert report["discovery"]["first_strategy_type"] == "semantic_broad"
    assert report["discovery"]["first_page"] is None
    assert report["latest_trace"] is None
    assert report["current_replay"] is None
    assert report["legacy_limitations"]
    assert report["dedupe"]["query_next_eligible"] == "2026-09-01 14:00:00"
    with sqlite3.connect(db_path) as connection:
        columns_after = [
            row[1] for row in connection.execute("PRAGMA table_info(seenlisting)")
        ]
    assert columns_after == columns_before
    assert "latest_trace_json" not in columns_after


def test_diagnose_listing_reports_current_discovery_and_bounded_trace(tmp_path):
    db_path = tmp_path / "current.db"
    mark_listing_seen(
        "buyee",
        "r123456789",
        "signature-a",
        "Roen",
        db_path,
        family="roen",
        strategy_id="roen.tops.buyout",
        strategy_type="market_buyout",
        search_page=2,
        search_rank=17,
    )
    record_stage_a_outcomes(
        {("buyee", "r123456789"): "selected"},
        db_path,
        family="roen",
        strategy_type="market_buyout",
    )
    record_decision_classifications(
        {("buyee", "r123456789"): "final:buyouts:asymmetric_value"},
        db_path,
        decision_logic_version="ultra-test-v1",
    )
    trace = {
        "query": "Roen",
        "strategy_id": "roen.tops.buyout",
        "strategy_type": "market_buyout",
        "search_page": 2,
        "search_rank": 17,
        "stage_a": {
            "selected": True,
            "primary_path": "market_buyout_inspection",
        },
        "detail_status": "enriched",
        "decision": "final:buyouts:asymmetric_value",
        "route": "buyouts",
        "alert_event": "initial",
        "actionable_price_jpy": 2_000,
        "auction_maturity": "fixed",
    }
    record_listing_traces({("buyee", "r123456789"): trace}, db_path)

    report = diagnose_listing(db_path, "r123456789")

    assert report["seen"] is True
    assert report["discovery"] == {
        "first_query": "Roen",
        "first_strategy_id": "roen.tops.buyout",
        "first_strategy_type": "market_buyout",
        "first_page": 2,
        "first_rank": 17,
        "last_query": "Roen",
        "last_strategy_id": "roen.tops.buyout",
        "last_strategy_type": "market_buyout",
        "last_page": 2,
        "last_rank": 17,
    }
    assert report["stage_a_outcomes"] == {
        "roen:market_buyout": "selected"
    }
    assert report["decision"]["classification"] == (
        "final:buyouts:asymmetric_value"
    )
    assert report["latest_trace"]["stage_a"]["primary_path"] == (
        "market_buyout_inspection"
    )
    assert report["event_history"][0]["alert_event"] == "initial"
    assert report["event_history"][0]["price_jpy"] == 2_000
    assert report["dedupe"]["unchanged_revisit_window_hours"] == 24
    assert report["legacy_limitations"] == []


def test_diagnose_listing_uses_normal_revisit_window_until_fully_evaluated(tmp_path):
    db_path = tmp_path / "stage-a-reject.db"
    mark_listing_seen(
        "buyee",
        "a123456789",
        "signature-a",
        "Alexander McQueen",
        db_path,
        family="alexander_mcqueen",
        strategy_type="market_explore",
    )
    record_stage_a_outcomes(
        {("buyee", "a123456789"): "rejected"},
        db_path,
        family="alexander_mcqueen",
        strategy_type="market_explore",
    )

    report = diagnose_listing(db_path, "a123456789")

    assert report["dedupe"]["unchanged_revisit_window_hours"] == 6


def test_listing_trace_fallback_is_hard_bounded_and_keeps_reject_path(tmp_path):
    db_path = tmp_path / "large-trace.db"
    mark_listing_seen("buyee", "k123456789", "signature-a", "Kapital", db_path)
    record_listing_traces(
        {
            ("buyee", "k123456789"): {
                "query": "Kapital",
                "strategy_type": "market_explore",
                "search_page": 3,
                "search_rank": 41,
                "title": "KAPITAL BORO jacket",
                    "stage_a": {
                        "selected": False,
                        "eligible": True,
                        "eligible_paths": ["unknown_interesting"],
                        "primary_path": "market_exploration",
                        "inspection_score": 42.5,
                        "brand_confirmed": True,
                        "detected_family": "kapital",
                        "target_brand_match": True,
                        "category_valid": True,
                        "item_type_confident": False,
                        "actionable_price": True,
                        "price_in_discovery_band": True,
                        "discovery_price_fraction": 0.25,
                        "actionable_buyout_fraction": 0.25,
                        "piece_relationship": "core",
                        "hard_veto": False,
                        "reject_reason": "candidate_budget",
                        "reasons": ["bounded low-price identity exploration"],
                    },
                "unexpected_large_payload": "雪" * 5_000,
            }
        },
        db_path,
    )

    report = diagnose_listing(db_path, "k123456789")
    encoded = json.dumps(
        report["latest_trace"], ensure_ascii=False, sort_keys=True
    ).encode("utf-8")

    assert len(encoded) <= 3_000
    assert report["latest_trace"]["trace_truncated"] is True
    assert report["latest_trace"]["query"] == "Kapital"
    assert report["latest_trace"]["search_page"] == 3
    assert report["latest_trace"]["stage_a"]["reject_reason"] == (
        "candidate_budget"
    )
    assert report["latest_trace"]["stage_a"]["inspection_score"] == 42.5
    assert report["latest_trace"]["stage_a"]["target_brand_match"] is True
    assert report["latest_trace"]["stage_a"]["actionable_price"] is True
    assert report["latest_trace"]["stage_a"]["hard_veto"] is False
    assert len(
        json.dumps(report["event_history"], ensure_ascii=False).encode("utf-8")
    ) <= 12_000


def test_diagnose_listing_tolerates_non_object_detail_observation(tmp_path):
    db_path = tmp_path / "malformed-detail.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO detailcache (
                key, source, source_listing_id, retrieved_at, expires_at,
                content_hash, observation_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "detail-key",
                "buyee",
                "m123456789",
                "2026-09-07 12:00:00",
                "2026-09-08 12:00:00",
                "hash",
                '["legacy", "invalid-shape"]',
            ),
        )

    report = diagnose_listing(db_path, "m123456789")

    assert report["detail"]["available"] is True
    assert report["detail"]["observation_valid"] is False
    assert report["detail"]["fields"] == {}
    assert report["current_replay"] is None


def test_diagnose_listing_preserves_stored_trace_when_current_replay_fails(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "replay-failure.db"
    mark_listing_seen("buyee", "q123456789", "signature-a", "Roen", db_path)
    record_listing_traces(
        {
            ("buyee", "q123456789"): {
                "query": "Roen",
                "stage_a": {"selected": False, "reject_reason": "candidate_budget"},
                "decision": "stage_a:candidate_budget",
            }
        },
        db_path,
    )
    with get_session(db_path) as session:
        session.add(
            Listing(
                source="buyee",
                source_listing_id="q123456789",
                url="https://buyee.jp/item/jdirectitems/auction/q123456789",
                title_original="Roen rhinestone zip hoodie",
                title_normalized="roen rhinestone zip hoodie",
                brand="roen",
                category="menswear",
                current_price=2_000,
            )
        )
        session.commit()

    def fail_replay(*_args, **_kwargs):
        raise TypeError("malformed persisted value")

    monkeypatch.setattr("buyeeye.diagnostics._current_evaluation", fail_replay)

    report = diagnose_listing(db_path, "q123456789")

    assert report["seen"] is True
    assert report["latest_trace"]["decision"] == "stage_a:candidate_budget"
    assert report["current_replay"] == {
        "available": False,
        "error_type": "TypeError",
        "note": "current replay unavailable; persisted trace remains authoritative",
    }
