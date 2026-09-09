from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from buyeeye.db import get_session
from buyeeye.discovery import (
    MarketplaceSearchFilters,
    QuerySpec,
    build_market_structure_queries,
    build_query_universe,
    query_layer_counts,
)
from buyeeye.evaluation import evaluate_listing, score_desirability
from buyeeye.fashion import enrich_title
from buyeeye.live_sources import BuyeeAuthenticatedSource, LiveListing
from buyeeye.models import Comp
from buyeeye.pipeline import _route_evaluation
from buyeeye.runtime_state import (
    QueryRuntimeState,
    SeenListing,
    _adaptive_priority,
    choose_query,
    mark_listing_seen,
    record_query_result,
    strategy_metrics,
)
from buyeeye.settings import load_yaml

ROOT = Path(__file__).resolve().parents[1]


def _market_spec(
    strategy_id: str,
    strategy_type: str = "market_buyout",
    *,
    priority: int = 80,
) -> QuerySpec:
    return QuerySpec(
        query="Maison Margiela",
        provenance=f"market_structure.test.{strategy_id}",
        family="margiela",
        priority=priority,
        cooldown_minutes=1,
        discovery_mode="market_slice",
        query_layer="market_structure",
        strategy_id=strategy_id,
        strategy_type=strategy_type,
        search_filters=MarketplaceSearchFilters(
            category_id="23176",
            category_group="mens_fashion",
            min_buyout_price_jpy=300,
            max_buyout_price_jpy=25_000,
            buy_now_only=True,
            item_type="auction",
            newly_posted=True,
            sort_by="end",
            sort_order="d",
            price_bucket="actionable",
            discovery_intent="cheap_actionable_buyout",
        ),
    )


def test_market_structure_search_url_uses_verified_native_buyee_parameters():
    filters = _market_spec("url-test").search_filters

    url = BuyeeAuthenticatedSource._search_url("Number (N)ine", filters)

    assert url == (
        "https://buyee.jp/item/search/query/Number%20%28N%29ine/category/23176?"
        "aucmin_bidorbuy_price=300&aucmax_bidorbuy_price=25000&buynow=1&"
        "item_type=auction&new=1&sort=end&order=d"
    )
    assert "aucmaxprice" not in url


def test_market_ending_search_uses_mens_category_and_supported_closing_filter():
    filters = MarketplaceSearchFilters(
        category_id="23176",
        category_group="mens_fashion",
        min_current_price_jpy=3000,
        max_current_price_jpy=25_000,
        item_type="auction",
        ending_within_one_hour=True,
        sort_by="end",
        sort_order="a",
    )

    url = BuyeeAuthenticatedSource._search_url("UNDERCOVER", filters)

    assert "/category/23176?" in url
    assert "aucminprice=3000&aucmaxprice=25000" in url
    assert "item_type=auction" in url
    assert "one_hour_to_finish=1" in url
    assert "sort=end&order=a" in url
    assert "timebuf" not in url


def test_real_buyee_buyout_price_label_is_parsed_from_fixture():
    rows = json.loads(
        (ROOT / "tests" / "fixtures" / "buyee_buyout_card.json").read_text(
            encoding="utf-8"
        )
    )

    listings = BuyeeAuthenticatedSource._items_from_browser_payload(rows, "UNDERCOVER")

    assert len(listings) == 1
    assert listings[0].current_price == 1000
    assert listings[0].buyout_price == 35_999
    assert listings[0].buyout_indicator == "buyout"


def test_query_results_exclude_browsing_history_and_recommendation_cards():
    rows = json.loads(
        (ROOT / "tests" / "fixtures" / "buyee_search_cards.json").read_text(
            encoding="utf-8"
        )
    )

    listings = BuyeeAuthenticatedSource._items_from_browser_payload(
        rows, "Maison Margiela"
    )

    assert [listing.source_listing_id for listing in listings] == ["d123456789"]
    assert listings[0].title_original == "Maison Margiela archive graphic T-shirt"


def test_http_search_parser_excludes_non_result_modules_from_fixture():
    html = (ROOT / "tests" / "fixtures" / "buyee_search_page.html").read_text(
        encoding="utf-8"
    )

    listings = BuyeeAuthenticatedSource._parse_listing_html(html, "Maison Margiela")

    assert [listing.source_listing_id for listing in listings] == [
        "d123456789",
        "m222222222",
    ]
    assert listings[0].buyout_price == 8000
    assert listings[1].title_original == "Maison Margiela reconstructed denim jacket"
    # URL provenance alone is not a safe module boundary: authenticated
    # history/recommendation cards can carry DirectSearch or no tag at all.
    assert {"h123456789", "n123456789", "x123456789"}.isdisjoint(
        {listing.source_listing_id for listing in listings}
    )


def test_market_universe_keeps_same_keyword_slices_with_distinct_provenance():
    searches = load_yaml(ROOT / "config" / "searches.yaml")
    taxonomy = load_yaml(ROOT / "config" / "fashion_taxonomy.yaml")
    market = load_yaml(ROOT / "config" / "market_structure.yaml")

    market_specs = build_market_structure_queries(market, taxonomy)
    universe = build_query_universe(searches, taxonomy, market)
    margiela = [spec for spec in market_specs if spec.query == "Maison Margiela"]

    assert len(margiela) > 3
    assert len({spec.runtime_identity for spec in margiela}) == len(margiela)
    assert {spec.strategy_type for spec in margiela} >= {
        "market_buyout",
        "market_ending",
        "market_price_band",
    }
    assert all(spec.search_filters.category_id for spec in margiela)
    assert {
        spec.search_filters.category_group for spec in market_specs if spec.search_filters
    } >= {
        "mens_fashion",
        "mens_fashion_goods",
        "mens_bags",
        "mens_shoes",
        "mens_accessories",
    }
    assert query_layer_counts(universe)["market_structure"] == len(market_specs)
    assert {spec.family for spec in market_specs} >= {
        "number_nine",
        "margiela",
        "dior_homme",
        "saint_laurent",
        "vetements",
        "rick_owens",
        "chrome_hearts",
        "if_six_was_nine",
        "roen",
        "needles",
        "undercover",
        "balenciaga",
        "prada",
        "alexander_mcqueen",
        "gaboratory",
        "king_baby",
        "vintage_band_tees",
    }


def test_saint_laurent_legacy_aliases_and_market_slices_are_additive():
    searches = load_yaml(ROOT / "config" / "searches.yaml")
    taxonomy = load_yaml(ROOT / "config" / "fashion_taxonomy.yaml")
    market = load_yaml(ROOT / "config" / "market_structure.yaml")

    universe = build_query_universe(searches, taxonomy, market)
    saint_laurent = [spec for spec in universe if spec.family == "saint_laurent"]
    broad_queries = {spec.query for spec in saint_laurent if spec.query_layer == "broad"}

    assert {
        "Saint Laurent Paris",
        "YSL エディ",
        "Yves Saint Laurent",
        "サンローラン",
        "サンローランパリ",
        "イヴ・サンローラン",
        "イヴサンローラン",
        "イブサンローラン",
    } <= broad_queries

    old_label_jacket = enrich_title("YVES SAINT LAURENT◆ジャケット/S/ウール")
    old_label_boots = enrich_title("イヴ・サンローラン サイドゴアブーツ 25.5cm")
    assert old_label_jacket.brand and old_label_jacket.brand.value == "Saint Laurent"
    assert old_label_boots.brand and old_label_boots.brand.value == "Saint Laurent"
    assert old_label_boots.item_type and old_label_boots.item_type.value == "footwear"

    market_specs = [spec for spec in saint_laurent if spec.query_layer == "market_structure"]
    modern = [spec for spec in market_specs if spec.query == "Saint Laurent Paris"]
    legacy = [spec for spec in market_specs if spec.query == "Yves Saint Laurent"]

    assert {spec.search_filters.category_group for spec in modern if spec.search_filters} == {
        "mens_fashion",
        "mens_shoes",
    }
    assert {spec.search_filters.category_group for spec in legacy if spec.search_filters} == {
        "mens_fashion",
        "mens_shoes",
    }
    assert all(spec.strategy_id.startswith("saint_laurent.") for spec in modern)
    assert all(spec.strategy_id.startswith("saint_laurent_legacy.") for spec in legacy)
    assert len({spec.runtime_identity for spec in market_specs}) == len(market_specs)


def test_discovery_price_ceiling_never_becomes_fmv_or_changes_economics():
    base = LiveListing(
        source="buyee",
        source_listing_id="valuation-separation",
        url="https://buyee.jp/item/valuation-separation",
        title_original="Maison Martin Margiela 本人期 AIDS archive graphic t-shirt",
        title_normalized="maison martin margiela aids archive graphic t shirt",
        brand="Maison Martin Margiela",
        category="menswear",
        current_price=5000,
        buyout_price=5000,
        starting_price=5000,
        seller_name="seller",
        condition="Used",
        raw={"detail_photo_count": 5, "seller_feedback_count": 100},
    )
    filtered = LiveListing(
        **{
            **base.__dict__,
            "raw": {
                **(base.raw or {}),
                "discovery": _market_spec("valuation-separation").discovery_observation(),
            },
        }
    )
    comps = [
        Comp("grailed_sold", base.title_original, price, 1.0, 30)
        for price in (40_000, 45_000, 50_000)
    ]

    ordinary_evaluation = evaluate_listing(base, comps)
    filtered_evaluation = evaluate_listing(filtered, comps)

    assert filtered_evaluation.economics == ordinary_evaluation.economics
    assert filtered_evaluation.economics.resale_median_jpy == 45_000
    assert filtered_evaluation.economics.resale_median_jpy != 25_000


def test_filtered_discovery_cannot_bypass_auction_maturity_or_fake_arbitrage():
    listing = LiveListing(
        source="buyee",
        source_listing_id="market-early-auction",
        url="https://buyee.jp/item/market-early-auction",
        title_original="Maison Martin Margiela 本人期 AIDS archive graphic t-shirt",
        title_normalized="maison martin margiela aids archive graphic t shirt",
        brand="Maison Martin Margiela",
        category="menswear",
        current_price=1000,
        buyout_price=None,
        starting_price=1000,
        seller_name="seller",
        condition="Used",
        time_remaining="6 day(s)",
        bid_count=1,
        raw={"discovery": _market_spec("early-auction").discovery_observation()},
    )

    evaluation = evaluate_listing(listing)

    assert evaluation.auction_maturity.level == "low"
    assert evaluation.auction_maturity.price_actionable is False
    assert evaluation.economics.landed_cost_jpy is None
    assert evaluation.economics.multiple is None
    assert _route_evaluation(listing, evaluation) is None


def test_filtered_fixed_price_bargain_can_still_alert_immediately():
    listing = LiveListing(
        source="buyee",
        source_listing_id="market-fixed-buyout",
        url="https://buyee.jp/item/market-fixed-buyout",
        title_original="Maison Martin Margiela 本人期 AIDS archive graphic t-shirt",
        title_normalized="maison martin margiela aids archive graphic t shirt",
        brand="Maison Martin Margiela",
        category="menswear",
        current_price=4000,
        buyout_price=4000,
        starting_price=1000,
        seller_name="seller",
        condition="Used",
        time_remaining="6 day(s)",
        raw={
            "detail_photo_count": 5,
            "seller_feedback_count": 100,
            "discovery": _market_spec("fixed-buyout").discovery_observation(),
        },
    )

    evaluation = evaluate_listing(listing)

    assert evaluation.auction_maturity.price_actionable is True
    assert evaluation.directional.thesis == "asymmetric_value"
    assert _route_evaluation(listing, evaluation)[0] == "buyouts"


def test_semantic_and_market_sightings_are_globally_deduped_with_provenance(tmp_path):
    db_path = tmp_path / "global-dedupe.db"

    first = mark_listing_seen(
        "buyee",
        "same-listing",
        "unchanged-signature",
        "Maison Margiela",
        db_path,
        strategy_type="semantic_broad",
    )
    second = mark_listing_seen(
        "buyee",
        "same-listing",
        "unchanged-signature",
        "Maison Margiela",
        db_path,
        strategy_id="margiela.mens_fashion.buyout.actionable",
        strategy_type="market_buyout",
    )

    assert first.is_duplicate is False
    assert second.is_duplicate is True
    with get_session(db_path) as session:
        row = session.exec(select(SeenListing)).one()
    assert row.first_strategy_type == "semantic_broad"
    assert row.first_strategy_id is None
    assert row.last_strategy_type == "market_buyout"
    assert row.last_strategy_id == "margiela.mens_fashion.buyout.actionable"


def test_strategy_runtime_identity_and_metrics_remain_independent(tmp_path):
    db_path = tmp_path / "strategies.db"
    buyout = _market_spec("same-query-buyout", "market_buyout")
    ending = _market_spec("same-query-ending", "market_ending")
    result = SimpleNamespace(
        scanned=20,
        unique_listings=15,
        duplicates=5,
        stage_a_rejected=12,
        stage_b_candidates=3,
        stage_b_enriched=3,
        final_candidates=1,
        new_alerts=1,
        route_counts={"buyouts": 1},
        alert_route_counts={"buyouts": 1},
        thesis_counts={"asymmetric_value": 1},
        opportunity_counts={"actionable_fixed_price": 1},
    )

    record_query_result(buyout, result, db_path)
    record_query_result(ending, SimpleNamespace(), db_path)

    with get_session(db_path) as session:
        rows = session.exec(select(QueryRuntimeState)).all()
    metrics = strategy_metrics(db_path, group_by="strategy_type")
    assert len(rows) == 2
    assert {row.strategy_id for row in rows} == {"same-query-buyout", "same-query-ending"}
    assert metrics["market_buyout"]["stage_a_pass"] == 3
    assert metrics["market_buyout"]["stage_a_pass_rate"] == pytest.approx(3 / 15)
    assert metrics["market_buyout"]["stage_b_rate"] == pytest.approx(3 / 15)
    assert metrics["market_buyout"]["final_candidate_rate"] == pytest.approx(1 / 15)
    assert metrics["market_buyout"]["alerts"] == 1
    assert metrics["market_buyout"]["routes"] == {"buyouts": 1}
    assert metrics["market_buyout"]["alert_routes"] == {"buyouts": 1}
    by_id = strategy_metrics(db_path)
    assert by_id["same-query-buyout"]["family"] == "margiela"
    assert by_id["same-query-buyout"]["filters"]["max_buyout_price_jpy"] == 25_000


def test_scheduler_allocates_market_capacity_and_adapts_only_with_evidence(tmp_path):
    db_path = tmp_path / "allocation.db"
    specs: list[QuerySpec] = []
    for layer, count in (("broad", 8), ("medium", 6), ("surgical", 4)):
        specs.extend(
            QuerySpec(
                f"{layer}-{index}",
                "test",
                "brand",
                80,
                60,
                query_layer=layer,
            )
            for index in range(count)
        )
    for index in range(8):
        strategy_type = (
            "market_buyout" if index < 4 else "market_ending" if index < 7 else "market_price_band"
        )
        specs.append(_market_spec(f"allocation-{index}", strategy_type))

    selected: list[str] = []
    for _ in range(20):
        spec = choose_query(
            specs,
            db_path,
            layer_shares={
                "broad": 0.35,
                "medium": 0.20,
                "surgical": 0.10,
                "market_structure": 0.35,
            },
        )
        assert spec is not None
        selected.append(spec.query_layer)
        record_query_result(spec, SimpleNamespace(), db_path)

    assert selected.count("broad") == 7
    assert selected.count("medium") == 4
    assert selected.count("surgical") == 2
    assert selected.count("market_structure") == 7

    tiny_sample = QueryRuntimeState(
        key="tiny",
        query="tiny",
        family="test",
        provenance="test",
        priority=80,
        runs=1,
        cards_seen=1,
        unique_seen=1,
        stage_b_candidates=1,
        final_candidates=1,
        alerts=1,
    )
    productive = QueryRuntimeState(
        key="productive",
        query="productive",
        family="test",
        provenance="test",
        priority=80,
        runs=10,
        cards_seen=250,
        unique_seen=200,
        stage_b_candidates=50,
        final_candidates=20,
        alerts=5,
    )
    poor = QueryRuntimeState(
        key="poor",
        query="poor",
        family="test",
        provenance="test",
        priority=80,
        runs=10,
        cards_seen=200,
        unique_seen=10,
        duplicates=190,
    )

    assert 0 < _adaptive_priority(tiny_sample) < 1
    assert 0 < _adaptive_priority(productive) <= 8
    assert -8 <= _adaptive_priority(poor) < 0

    preserved = _market_spec("poor-but-preserved")
    record_query_result(
        preserved,
        SimpleNamespace(scanned=100, unique_listings=2, duplicates=98),
        db_path,
    )
    assert (
        choose_query(
            [preserved],
            db_path,
            now=datetime.now(UTC) + timedelta(minutes=2),
        )
        == preserved
    )


def test_scheduler_fairness_eventually_runs_lower_priority_market_strategies(tmp_path):
    db_path = tmp_path / "fairness.db"
    specs = [
        _market_spec(f"fair-{index}", priority=100 if index == 0 else 70)
        for index in range(6)
    ]
    selected: set[str] = set()
    future = datetime.now(UTC) + timedelta(hours=1)

    for _ in range(12):
        spec = choose_query(specs, db_path, now=future)
        assert spec is not None
        selected.add(spec.strategy_id)
        record_query_result(spec, SimpleNamespace(), db_path)

    assert selected == {f"fair-{index}" for index in range(6)}


def test_additive_runtime_migration_accepts_existing_query_rows(tmp_path):
    db_path = tmp_path / "legacy-runtime.db"
    key = hashlib.sha256(b"legacy query").hexdigest()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE queryruntimestate (
                key VARCHAR PRIMARY KEY NOT NULL,
                query VARCHAR NOT NULL,
                family VARCHAR NOT NULL,
                provenance VARCHAR NOT NULL,
                discovery_mode VARCHAR NOT NULL,
                priority INTEGER NOT NULL,
                runs INTEGER NOT NULL,
                cards_seen INTEGER NOT NULL,
                unique_seen INTEGER NOT NULL,
                duplicates INTEGER NOT NULL,
                stage_b_candidates INTEGER NOT NULL,
                final_candidates INTEGER NOT NULL,
                alerts INTEGER NOT NULL,
                failures INTEGER NOT NULL,
                last_run_at DATETIME,
                next_eligible_at DATETIME
            )
            """
        )
        connection.execute(
            """
            INSERT INTO queryruntimestate VALUES (
                ?, 'Legacy Query', 'legacy', 'test', 'direct_piece', 80,
                2, 20, 10, 10, 1, 0, 0, 0, NULL, NULL
            )
            """,
            (key,),
        )

    spec = QuerySpec(
        "Legacy Query",
        "test",
        "legacy",
        80,
        query_layer="broad",
    )
    record_query_result(
        spec,
        SimpleNamespace(stage_a_rejected=3, stage_b_enriched=1),
        db_path,
    )

    with get_session(db_path) as session:
        row = session.get(QueryRuntimeState, key)
    assert row.runs == 3
    assert row.query_layer == "broad"
    assert row.strategy_type == "semantic_broad"
    assert row.stage_a_rejected == 3
    assert row.stage_b_enriched == 1


def test_undercover_is_first_class_but_subline_and_collab_evidence_is_conservative():
    archive = enrich_title("UNDERCOVER 2003SS SCAB distressed jacket Jun Takahashi")
    ordinary_collection_piece = enrich_title("UNDERCOVER 2003SS SCAB graphic t-shirt")
    collaboration = enrich_title("UNIQLO x UNDERCOVER basic logo t-shirt")

    assert archive.brand.value == "Undercover"
    assert archive.designer.value == "Jun Takahashi"
    assert archive.collection.value == "SCAB"
    assert archive.item_type.value == "jacket"
    assert any(signal.value == "distressed" for signal in archive.positive_signals)
    assert not any(signal.value == "scab" for signal in archive.positive_signals)
    assert ordinary_collection_piece.collection.value == "SCAB"
    assert score_desirability(
        ordinary_collection_piece,
        "UNDERCOVER 2003SS SCAB graphic t-shirt",
    ).score < 70
    assert any("uniqlo x undercover" in signal.value for signal in collaboration.negative_signals)
