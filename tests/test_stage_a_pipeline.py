from __future__ import annotations

import asyncio

from buyeeye.discovery import MarketplaceSearchFilters, QuerySpec
from buyeeye.live_sources import LiveListing
from buyeeye.pipeline import PostDetailDecision, scan_once


def _settings(*, market_rate: float = 1.0) -> dict[str, object]:
    return {
        "scanner": {"search_pages_per_query": 1},
        "detail": {"minimum_heat": 40, "cache_ttl_hours": 24},
        "comps": {"minimum_heat_for_lookup": 40, "cache_ttl_hours": 72},
        "alert": {
            "auction_alert_max_remaining_hours": 12,
            "ending_threshold_minutes": 60,
            "archive_watch_min_heat": 70,
        },
        "stage_a": {
            "archive_heat_threshold": 40,
            "minimum_brand_confidence": 0.95,
            "market_minimum_heat": 18,
            "unknown_absolute_buyout_max_jpy": 10_000,
            "unknown_max_band_fraction": 0.4,
            "max_candidates_per_query": 6,
            "max_market_candidates_per_query": 3,
            "max_market_candidate_rate": market_rate,
            "path_caps": {"unknown_interesting": 1, "silver_jewelry": 2},
        },
    }


def _market_spec() -> QuerySpec:
    return QuerySpec(
        query="Maison Margiela",
        provenance="market_structure.test",
        family="margiela",
        priority=90,
        query_layer="market_structure",
        strategy_id="margiela.test.buyout",
        strategy_type="market_buyout",
        search_filters=MarketplaceSearchFilters(
            category_id="23176",
            category_group="mens_fashion",
            min_buyout_price_jpy=300,
            max_buyout_price_jpy=25_000,
            buy_now_only=True,
            item_type="auction",
            newly_posted=True,
            sort_by="bidorbuy",
            sort_order="a",
        ),
    )


def _listing(listing_id: str, title: str, price: int = 5_000) -> LiveListing:
    return LiveListing(
        source="buyee",
        source_listing_id=listing_id,
        url=f"https://buyee.jp/item/{listing_id}",
        title_original=title,
        title_normalized=title.casefold(),
        brand="unknown",
        category="unknown",
        current_price=price,
        buyout_price=price,
        starting_price=price,
        seller_name=None,
        condition=None,
        time_remaining="6 day(s)",
        raw={},
    )


def test_candidate_budget_blocks_capped_cards_from_detail_comp_and_routing(
    monkeypatch, tmp_path
) -> None:
    listings = [
        _listing(
            f"archive-{index}",
            "Maison Margiela 本人期 artisanal 再構築 leather t-shirt",
        )
        for index in range(4)
    ]
    detail_ids: list[str] = []
    comp_calls: list[str] = []
    route_ids: list[str] = []

    async def fake_run_once(self, query, persist=False):
        return listings

    async def fake_detail(self, candidates):
        detail_ids.extend(item.source_listing_id for item in candidates)
        for item in candidates:
            item.raw = {**(item.raw or {}), "stage_b": {"status": "enriched"}}
        return len(candidates), 0

    async def fake_comps(self, profile, minimum_candidates=5):
        comp_calls.append(profile.brand.value)
        return []

    def fake_route(listing, evaluation, *args, **kwargs):
        route_ids.append(listing.source_listing_id)
        return PostDetailDecision("grails", "test route")

    monkeypatch.setattr("buyeeye.pipeline.load_settings", lambda: _settings(market_rate=0.25))
    monkeypatch.setattr("buyeeye.pipeline.LiveMarketLoop.run_once", fake_run_once)
    monkeypatch.setattr("buyeeye.pipeline.BuyeeDetailService.enrich", fake_detail)
    monkeypatch.setattr("buyeeye.pipeline.CompService.retrieve", fake_comps)
    monkeypatch.setattr("buyeeye.pipeline._post_detail_decision", fake_route)

    result = asyncio.run(scan_once(_market_spec(), tmp_path / "bounded.db", send_alerts=False))

    assert result.stage_a_eligible == 4
    assert result.stage_a_capped == 3
    assert result.stage_b_candidates == 1
    assert result.stage_a_rejected == 3
    assert len(detail_ids) == 1
    assert len(comp_calls) == 2  # one U.S. and one Japan source pass for the selected card
    assert route_ids == detail_ids
    assert result.final_candidates == 1
    assert sum(bool(item.raw["stage_a"]["selected"]) for item in listings) == 1
    assert sum(item.raw["stage_a"]["reject_reason"] == "candidate_budget" for item in listings) == 3


def test_cheap_market_candidate_can_be_enriched_then_rejected_without_alert(
    monkeypatch, tmp_path
) -> None:
    listing = _listing("mcq-cheap", "McQ Alexander McQueen 15SS Arc", 3_800)
    spec = QuerySpec(
        **{
            **_market_spec().__dict__,
            "query": "Alexander McQueen",
            "family": "alexander_mcqueen",
            "strategy_id": "alexander_mcqueen.test.buyout",
        }
    )

    async def fake_run_once(self, query, persist=False):
        return [listing]

    async def fake_detail(self, candidates):
        candidates[0].raw = {
            **(candidates[0].raw or {}),
            "stage_b": {"status": "enriched", "observed": {}},
        }
        return 1, 0

    monkeypatch.setattr("buyeeye.pipeline.load_settings", _settings)
    monkeypatch.setattr("buyeeye.pipeline.LiveMarketLoop.run_once", fake_run_once)
    monkeypatch.setattr("buyeeye.pipeline.BuyeeDetailService.enrich", fake_detail)

    result = asyncio.run(scan_once(spec, tmp_path / "rejected.db", send_alerts=False))

    assert result.stage_b_candidates == 1
    assert result.stage_b_enriched == 1
    assert result.stage_b_rejected == 1
    assert result.post_detail_reject_counts == {"category_mismatch": 1}
    assert len(result.post_detail_decisions) == 1
    decision = result.post_detail_decisions[0]
    assert decision["listing_id"] == "mcq-cheap"
    assert decision["relationship"] == "diffusion"
    assert decision["identity"] == "medium"
    assert decision["valuation"] == "unavailable"
    assert decision["directional"] == "low"
    assert decision["rejection_reason"] == "category_mismatch"
    assert listing.raw["intelligence"]["post_detail_rejection_reason"] == "category_mismatch"
    assert result.comp_queried == 0
    assert result.final_candidates == 0
    assert result.new_alerts == 0
