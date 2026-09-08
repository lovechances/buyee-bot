from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from buyeeye import runtime_state
from buyeeye.db import get_session
from buyeeye.discovery import (
    MarketplaceSearchFilters,
    QuerySpec,
    build_query_universe,
    search_page_budget,
)
from buyeeye.runtime_state import (
    QueryRuntimeState,
    SeenListing,
    _query_key,
    choose_query,
    mark_listing_seen,
    record_decision_classifications,
    record_listing_traces,
    record_stage_a_outcomes,
)
from buyeeye.settings import load_yaml


def _semantic_spec(
    query: str,
    *,
    priority: int = 70,
    layer: str = "broad",
    deep_search: bool = False,
) -> QuerySpec:
    return QuerySpec(
        query=query,
        provenance="test.ultra",
        family=query.casefold().replace(" ", "_"),
        priority=priority,
        cooldown_minutes=1,
        query_layer=layer,
        deep_search=deep_search,
    )


def test_fully_evaluated_unchanged_listing_yields_for_24_hours_but_state_change_reenters(
    tmp_path,
):
    db_path = tmp_path / "state.db"
    first = mark_listing_seen(
        "buyee",
        "stable-1",
        "signature-a",
        "Roen",
        db_path,
        freshness_hours=6,
        fully_evaluated_freshness_hours=24,
        family="roen",
        strategy_type="market_buyout",
    )
    assert first.lifetime_first_seen is True
    record_stage_a_outcomes(
        {("buyee", "stable-1"): "selected"},
        db_path,
        family="roen",
        strategy_type="market_buyout",
    )
    record_decision_classifications(
        {("buyee", "stable-1"): "reject:insufficient_desirability"},
        db_path,
        decision_logic_version="test-v1",
    )
    with get_session(db_path) as session:
        row = session.exec(
            select(SeenListing).where(SeenListing.source_listing_id == "stable-1")
        ).one()
        row.last_processed_at = datetime.now(UTC) - timedelta(hours=7)
        session.add(row)
        session.commit()

    unchanged = mark_listing_seen(
        "buyee",
        "stable-1",
        "signature-a",
        "Roen",
        db_path,
        freshness_hours=6,
        fully_evaluated_freshness_hours=24,
        family="roen",
        strategy_type="market_buyout",
    )
    assert unchanged.is_duplicate is True
    assert unchanged.revisit_reason == "unchanged_fully_evaluated"

    moved = mark_listing_seen(
        "buyee",
        "stable-1",
        "signature-b",
        "Roen",
        db_path,
        freshness_hours=6,
        fully_evaluated_freshness_hours=24,
        family="roen",
        strategy_type="market_buyout",
    )
    assert moved.is_duplicate is False
    assert moved.material_state_changed is True


def test_daemon_retry_does_not_treat_an_unfinished_reservation_as_completed(
    tmp_path,
):
    db_path = tmp_path / "state.db"
    mark_listing_seen(
        "buyee",
        "unfinished-1",
        "signature-a",
        "Roen",
        db_path,
        family="roen",
        strategy_type="market_buyout",
        decision_logic_version="test-v1",
        retry_incomplete_processing=True,
    )

    retry = mark_listing_seen(
        "buyee",
        "unfinished-1",
        "signature-a",
        "Roen",
        db_path,
        family="roen",
        strategy_type="market_buyout",
        decision_logic_version="test-v1",
        retry_incomplete_processing=True,
    )
    assert retry.is_duplicate is False
    assert retry.revisit_reason == "incomplete_processing_retry"
    assert retry.lifetime_first_seen is True

    record_stage_a_outcomes(
        {("buyee", "unfinished-1"): "rejected"},
        db_path,
        family="roen",
        strategy_type="market_buyout",
    )
    record_decision_classifications(
        {("buyee", "unfinished-1"): "stage_a:not_worth_inspecting"},
        db_path,
        decision_logic_version="test-v1",
    )
    completed = mark_listing_seen(
        "buyee",
        "unfinished-1",
        "signature-a",
        "Roen",
        db_path,
        family="roen",
        strategy_type="market_buyout",
        decision_logic_version="test-v1",
        retry_incomplete_processing=True,
    )
    assert completed.is_duplicate is True
    assert completed.revisit_reason == "fresh"


def test_new_strategy_gets_two_samples_without_catching_up_to_lifetime_runs(
    tmp_path,
):
    db_path = tmp_path / "state.db"
    established = _semantic_spec("Established", priority=100)
    newly_added = _semantic_spec("Newly Added", priority=10)
    with get_session(db_path) as session:
        session.add(
            QueryRuntimeState(
                key=_query_key(established),
                query=established.query,
                family=established.family,
                provenance=established.provenance,
                discovery_mode=established.discovery_mode,
                query_layer=established.query_layer,
                strategy_type=established.strategy_type,
                priority=established.priority,
                runs=500,
            )
        )
        session.commit()

    first_sample = choose_query([established, newly_added], db_path)

    with get_session(db_path) as session:
        session.add(
            QueryRuntimeState(
                key=_query_key(newly_added),
                query=newly_added.query,
                family=newly_added.family,
                provenance=newly_added.provenance,
                discovery_mode=newly_added.discovery_mode,
                query_layer=newly_added.query_layer,
                strategy_type=newly_added.strategy_type,
                priority=newly_added.priority,
                runs=2,
            )
        )
        session.commit()
    after_two_samples = choose_query([established, newly_added], db_path)

    assert first_sample == newly_added
    assert after_two_samples == established


def test_detail_failure_is_not_treated_as_fully_evaluated_for_a_day(tmp_path):
    db_path = tmp_path / "state.db"
    mark_listing_seen(
        "buyee",
        "failed-detail",
        "signature-a",
        "L.G.B.",
        db_path,
        family="lgb",
        strategy_type="market_explore",
    )
    record_stage_a_outcomes(
        {("buyee", "failed-detail"): "selected"},
        db_path,
        family="lgb",
        strategy_type="market_explore",
    )
    record_decision_classifications(
        {("buyee", "failed-detail"): "detail_failed"},
        db_path,
        decision_logic_version="test-v1",
    )
    with get_session(db_path) as session:
        row = session.exec(
            select(SeenListing).where(
                SeenListing.source_listing_id == "failed-detail"
            )
        ).one()
        row.last_processed_at = datetime.now(UTC) - timedelta(hours=7)
        session.add(row)
        session.commit()

    retry = mark_listing_seen(
        "buyee",
        "failed-detail",
        "signature-a",
        "L.G.B.",
        db_path,
        freshness_hours=6,
        fully_evaluated_freshness_hours=24,
        family="lgb",
        strategy_type="market_explore",
    )

    assert retry.is_duplicate is False
    assert retry.revisit_reason == "freshness_expired"


def test_pending_alert_event_uses_normal_revisit_window(tmp_path):
    db_path = tmp_path / "state.db"
    mark_listing_seen(
        "buyee",
        "pending-alert-1",
        "signature-a",
        "Roen",
        db_path,
        freshness_hours=6,
        fully_evaluated_freshness_hours=24,
        family="roen",
        strategy_type="market_buyout",
    )
    record_stage_a_outcomes(
        {("buyee", "pending-alert-1"): "selected"},
        db_path,
        family="roen",
        strategy_type="market_buyout",
    )
    record_decision_classifications(
        {("buyee", "pending-alert-1"): "pending_alert:cooldown:material_price_drop"},
        db_path,
        decision_logic_version="test-v1",
    )
    with get_session(db_path) as session:
        row = session.exec(
            select(SeenListing).where(
                SeenListing.source_listing_id == "pending-alert-1"
            )
        ).one()
        row.last_processed_at = datetime.now(UTC) - timedelta(hours=7)
        session.add(row)
        session.commit()

    retry = mark_listing_seen(
        "buyee",
        "pending-alert-1",
        "signature-a",
        "Roen",
        db_path,
        freshness_hours=6,
        fully_evaluated_freshness_hours=24,
        family="roen",
        strategy_type="market_buyout",
        decision_logic_version="test-v1",
    )

    assert retry.is_duplicate is False
    assert retry.revisit_reason == "freshness_expired"


def test_depth_allocation_samples_lower_pages_without_deepening_hot_slices():
    scanner = {
        "search_pages_per_query": 3,
        "search_pages_by_layer": {"broad": 1},
        "deep_broad_pages": 2,
        "search_pages_by_strategy": {"market_explore": 3},
    }
    broad = _semantic_spec(
        "Maison Margiela", priority=80, layer="broad", deep_search=True
    )
    lower_priority_alias = _semantic_spec("メゾンマルジェラ", priority=78, layer="broad")
    surgical = _semantic_spec("Margiela 0 10", layer="surgical")
    market_buyout = QuerySpec(
        query="Roen",
        provenance="market.test",
        family="roen",
        priority=80,
        cooldown_minutes=1,
        discovery_mode="market_slice",
        query_layer="market_structure",
        strategy_id="roen.buyout",
        strategy_type="market_buyout",
        search_filters=MarketplaceSearchFilters(category_id="23176"),
    )
    market_explore = QuerySpec(
        **{
            **market_buyout.__dict__,
            "strategy_id": "roen.explore",
            "strategy_type": "market_explore",
        }
    )

    assert search_page_budget(broad, scanner) == 2
    assert search_page_budget(lower_priority_alias, scanner) == 1
    assert search_page_budget(surgical, scanner) == 1
    assert search_page_budget(market_buyout, scanner) == 1
    assert search_page_budget(market_explore, scanner) == 3


def test_query_universe_allocates_exactly_one_deep_broad_anchor_per_family():
    searches = load_yaml("config/searches.yaml")
    taxonomy = load_yaml("config/fashion_taxonomy.yaml")
    market = load_yaml("config/market_structure.yaml")

    universe = build_query_universe(searches, taxonomy, market)
    scanner = load_yaml("config/settings.example.yaml")["scanner"]
    broad_anchors = [
        spec for spec in universe if spec.query_layer == "broad" and spec.deep_search
    ]
    deep_specs = [spec for spec in universe if search_page_budget(spec, scanner) == 2]

    assert len(broad_anchors) == len(taxonomy["brands"])
    assert {spec.family for spec in broad_anchors} == set(taxonomy["brands"])
    assert all(
        sum(
            candidate.deep_search
            for candidate in universe
            if candidate.query_layer == "broad" and candidate.family == family
        )
        == 1
        for family in taxonomy["brands"]
    )
    assert len(deep_specs) == 68
    assert sum(spec.strategy_type == "market_explore" for spec in deep_specs) == 23
    assert sum(spec.strategy_type == "market_price_band" for spec in deep_specs) == 5
    assert all(
        search_page_budget(spec, scanner) == 1
        for spec in universe
        if spec.strategy_type in {"market_buyout", "market_ending", "market_closing"}
    )


def test_every_retained_taxonomy_family_has_an_active_query():
    searches = load_yaml("config/searches.yaml")
    taxonomy = load_yaml("config/fashion_taxonomy.yaml")
    market = load_yaml("config/market_structure.yaml")

    universe = build_query_universe(searches, taxonomy, market)

    assert {spec.family for spec in universe} == set(taxonomy["brands"])
    forbidden = {
        "julius",
        "blackmeans",
        "general_research",
        "20471120",
        "beauty_beast",
        "neighborhood",
        "bounty_hunter",
        "wacko_maria",
        "ann_demeulemeester",
        "carol_christian_poell",
        "marithe_francois_girbaud",
        "plein_sud",
        "gabor",
        "goros",
    }
    assert not ({spec.family for spec in universe} & forbidden)
    assert "gaboratory" in {spec.family for spec in universe}


def test_listing_trace_history_is_compact_and_bounded(tmp_path):
    db_path = tmp_path / "state.db"
    mark_listing_seen("buyee", "trace-1", "sig", "Roen", db_path)
    for index in range(15):
        record_listing_traces(
            {
                ("buyee", "trace-1"): {
                    "query": "Roen",
                    "strategy_type": "market_buyout",
                    "stage_a": {"selected": True, "primary_path": "market_buyout_inspection"},
                    "detail_status": "enriched",
                    "decision": "final:buyouts:asymmetric_value",
                    "route": "buyouts",
                    "actionable_price_jpy": 2000 + index,
                }
            },
            db_path,
        )

    with get_session(db_path) as session:
        row = session.exec(
            select(SeenListing).where(SeenListing.source_listing_id == "trace-1")
        ).one()
        assert row.latest_trace_json is not None
        assert row.event_history_json is not None
        assert len(json.loads(row.event_history_json)) == 12


def test_old_never_inspected_trace_is_pruned_without_removing_dedupe_identity(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "state.db"
    mark_listing_seen("buyee", "cheap-reject", "sig-reject", "Roen", db_path)
    record_listing_traces(
        {
            ("buyee", "cheap-reject"): {
                "query": "Roen",
                "stage_a": {"selected": False, "reject_reason": "not_worth_inspecting"},
                "decision": "stage_a:not_worth_inspecting",
            }
        },
        db_path,
    )
    mark_listing_seen("buyee", "inspected", "sig-inspected", "Roen", db_path)
    record_listing_traces(
        {
            ("buyee", "inspected"): {
                "query": "Roen",
                "stage_a": {"selected": True},
                "detail_status": "enriched",
                "decision": "reject:route_threshold",
            }
        },
        db_path,
    )
    with get_session(db_path) as session:
        for listing_id in ("cheap-reject", "inspected"):
            row = session.exec(
                select(SeenListing).where(
                    SeenListing.source_listing_id == listing_id
                )
            ).one()
            row.last_processed_at = datetime.now(UTC) - timedelta(days=15)
            session.add(row)
        session.commit()

    monkeypatch.setattr(runtime_state, "_last_trace_prune_at", None)
    record_listing_traces(
        {
            ("buyee", "inspected"): {
                "query": "Roen",
                "stage_a": {"selected": True},
                "detail_status": "enriched",
                "decision": "reject:route_threshold",
            }
        },
        db_path,
    )

    with get_session(db_path) as session:
        rejected = session.exec(
            select(SeenListing).where(
                SeenListing.source_listing_id == "cheap-reject"
            )
        ).one()
        inspected = session.exec(
            select(SeenListing).where(SeenListing.source_listing_id == "inspected")
        ).one()
        assert rejected.signature == "sig-reject"
        assert rejected.latest_trace_json is None
        assert inspected.latest_trace_json is not None
        assert inspected.first_inspected_at is not None
