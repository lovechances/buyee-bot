from __future__ import annotations

from dataclasses import replace

import pytest

from buyeeye.discovery import MarketplaceSearchFilters, QuerySpec
from buyeeye.evaluation import DesirabilityResult, evaluate_listing
from buyeeye.fashion import enrich_title
from buyeeye.live_sources import LiveListing
from buyeeye.stage_a import assess_stage_a, select_stage_a_candidates


def _settings(**stage_a_overrides: object) -> dict[str, object]:
    stage_a: dict[str, object] = {
        "archive_heat_threshold": 40,
        "minimum_brand_confidence": 0.95,
        "market_minimum_heat": 18,
        "silver_minimum_heat": 30,
        "unknown_absolute_buyout_max_jpy": 10_000,
        "unknown_max_band_fraction": 0.40,
        "max_candidates_per_query": 6,
        "max_market_candidates_per_query": 3,
        "max_market_candidate_rate": 0.03,
        "path_caps": {"unknown_interesting": 1, "silver_jewelry": 2},
    }
    stage_a.update(stage_a_overrides)
    return {
        "detail": {"minimum_heat": 40},
        "alert": {"auction_alert_max_remaining_hours": 12},
        "stage_a": stage_a,
    }


def _market_spec(
    family: str,
    strategy_type: str,
    *,
    maximum_jpy: int = 25_000,
) -> QuerySpec:
    ending = strategy_type == "market_ending"
    filters = MarketplaceSearchFilters(
        category_id="23176",
        category_group="mens_fashion",
        min_current_price_jpy=300 if ending else None,
        max_current_price_jpy=maximum_jpy if ending else None,
        min_buyout_price_jpy=None if ending else 300,
        max_buyout_price_jpy=None if ending else maximum_jpy,
        buy_now_only=not ending,
        item_type="auction",
        newly_posted=not ending,
        sort_by="end",
        sort_order="a" if ending else "d",
        price_bucket="actionable",
        discovery_intent=("ending_soon_bargain" if ending else "cheap_actionable_buyout"),
    )
    return QuerySpec(
        query=family,
        provenance=f"market_structure.test.{family}.{strategy_type}",
        family=family,
        priority=80,
        cooldown_minutes=1,
        discovery_mode="market_slice",
        query_layer="market_structure",
        strategy_id=f"{family}.test.{strategy_type}",
        strategy_type=strategy_type,
        search_filters=filters,
    )


def _semantic_spec(family: str = "margiela") -> QuerySpec:
    return QuerySpec(
        query=family,
        provenance="test.semantic",
        family=family,
        priority=80,
        query_layer="broad",
    )


def _price_band_spec(family: str, minimum_jpy: int, maximum_jpy: int) -> QuerySpec:
    return QuerySpec(
        query=family,
        provenance=f"market_structure.test.{family}.price_band",
        family=family,
        priority=70,
        query_layer="market_structure",
        strategy_id=f"{family}.test.price_band",
        strategy_type="market_price_band",
        search_filters=MarketplaceSearchFilters(
            category_id="23176",
            category_group="mens_fashion",
            min_current_price_jpy=minimum_jpy,
            max_current_price_jpy=maximum_jpy,
            item_type="auction",
            sort_by="cbids",
            sort_order="a",
        ),
    )


def _listing(
    listing_id: str,
    title: str,
    *,
    brand: str = "unknown",
    category: str = "menswear",
    current_price: int = 5_000,
    buyout_price: int | None = 5_000,
    time_remaining: str = "6 day(s)",
) -> LiveListing:
    return LiveListing(
        source="buyee",
        source_listing_id=listing_id,
        url=f"https://buyee.jp/item/jdirectitems/auction/{listing_id}",
        title_original=title,
        title_normalized=title.casefold(),
        brand=brand,
        category=category,
        current_price=current_price,
        buyout_price=buyout_price,
        starting_price=current_price,
        seller_name=None,
        condition=None,
        time_remaining=time_remaining,
        bid_count=0,
        raw={},
    )


def test_archive_heat_boundary_is_eligible_for_semantic_detail() -> None:
    listing = _listing(
        "archive-boundary",
        "Maison Martin Margiela 本人期 AIDS archive graphic t-shirt",
        brand="Maison Martin Margiela",
        current_price=20_000,
        buyout_price=20_000,
    )
    evaluation = evaluate_listing(listing, settings=_settings())
    boundary_evaluation = replace(
        evaluation,
        desirability=DesirabilityResult(score=40),
    )

    assessment = assess_stage_a(
        listing,
        boundary_evaluation,
        _semantic_spec(),
        _settings(),
    )

    assert assessment.eligible is True
    assert assessment.primary_path == "semantic_archive"
    assert assessment.eligible_paths == ("semantic_archive",)
    assert "archive heat 40 >= 40" in assessment.reasons


def test_sub_40_target_brand_buyout_can_enter_detail_without_becoming_fmv() -> None:
    listing = _listing(
        "sub-40-buyout",
        "UNDERCOVER engineer boots",
        brand="Undercover",
        current_price=20_000,
        buyout_price=20_000,
    )
    settings = _settings()
    evaluation = evaluate_listing(listing, settings=settings)

    assessment = assess_stage_a(
        listing,
        evaluation,
        _market_spec("undercover", "market_buyout"),
        settings,
    )

    assert evaluation.desirability.score < 40
    assert assessment.eligible is True
    assert assessment.primary_path == "market_value"
    assert assessment.actionable_price is True
    assert assessment.price_in_discovery_band is True
    assert evaluation.economics.resale_median_jpy is None
    assert evaluation.economics.opportunity_score == 0


def test_market_path_uses_confident_item_identity_not_designer_heat_points() -> None:
    listing = _listing(
        "roen-low-heat",
        "Roen long sleeve shirt",
        brand="Roen",
        current_price=8_000,
        buyout_price=8_000,
    )
    settings = _settings()
    evaluation = evaluate_listing(listing, settings=settings)

    assessment = assess_stage_a(
        listing,
        evaluation,
        _market_spec("roen", "market_buyout", maximum_jpy=18_000),
        settings,
    )

    assert evaluation.desirability.score == 12
    assert assessment.item_type_confident is True
    assert assessment.primary_path == "market_value"


def test_ending_path_requires_mature_auction_price() -> None:
    settings = _settings()
    spec = _market_spec("needles", "market_ending", maximum_jpy=9_000)
    mature = _listing(
        "needles-mature",
        "Needles V-neck cardigan",
        brand="Needles",
        current_price=7_500,
        buyout_price=None,
        time_remaining="8 hour(s)",
    )
    immature = replace(
        mature,
        source_listing_id="needles-immature",
        time_remaining="1 day(s)",
    )

    mature_evaluation = evaluate_listing(mature, settings=settings)
    immature_evaluation = evaluate_listing(immature, settings=settings)
    mature_assessment = assess_stage_a(mature, mature_evaluation, spec, settings)
    immature_assessment = assess_stage_a(immature, immature_evaluation, spec, settings)

    assert mature_evaluation.auction_maturity.level == "medium"
    assert mature_assessment.primary_path == "ending_auction"
    assert immature_evaluation.auction_maturity.level == "low"
    assert immature_assessment.eligible is False
    assert immature_assessment.reject_reason == "immature_auction"


def test_query_brand_neighbor_does_not_use_the_market_slice() -> None:
    listing = _listing(
        "keyword-neighbor",
        "VETEMENTS sweatshirt",
        brand="Vetements",
        current_price=5_000,
        buyout_price=5_000,
    )
    settings = _settings()
    evaluation = evaluate_listing(listing, settings=settings)

    assessment = assess_stage_a(
        listing,
        evaluation,
        _market_spec("margiela", "market_buyout"),
        settings,
    )

    assert assessment.detected_family == "vetements"
    assert assessment.brand_confirmed is True
    assert assessment.target_brand_match is False
    assert assessment.eligible is False
    assert assessment.reject_reason == "query_brand_mismatch"


def test_unknown_identity_exploration_is_price_bounded_and_path_capped() -> None:
    settings = _settings(
        max_market_candidate_rate=1.0,
        max_market_candidates_per_query=6,
        path_caps={"unknown_interesting": 1},
    )
    spec = _market_spec("margiela", "market_buyout")
    cheap_listings = [
        _listing(
            f"unknown-{index}",
            "Maison Margiela",
            brand="Maison Martin Margiela",
            current_price=5_000,
            buyout_price=5_000,
        )
        for index in range(3)
    ]
    expensive = _listing(
        "unknown-expensive",
        "Maison Margiela",
        brand="Maison Martin Margiela",
        current_price=15_000,
        buyout_price=15_000,
    )
    cheap_assessments = [
        assess_stage_a(
            listing,
            evaluate_listing(listing, settings=settings),
            spec,
            settings,
        )
        for listing in cheap_listings
    ]
    expensive_assessment = assess_stage_a(
        expensive,
        evaluate_listing(expensive, settings=settings),
        spec,
        settings,
    )

    selected, capped = select_stage_a_candidates(cheap_assessments, spec, settings)

    assert all(item.eligible_paths == ("unknown_interesting",) for item in cheap_assessments)
    assert len(selected) == 1
    assert len(capped) == 2
    assert selected[0].listing_id == "unknown-0"
    assert expensive_assessment.price_in_discovery_band is True
    assert expensive_assessment.discovery_price_fraction == 0.6
    assert expensive_assessment.eligible is False
    assert expensive_assessment.reject_reason == "insufficient_card_evidence"


def test_severe_counterfeit_evidence_vetoes_even_archive_heat() -> None:
    listing = _listing(
        "severe-veto",
        (
            "Maison Martin Margiela 本人期 AIDS archive graphic artisanal "
            "再構築 leather t-shirt fake"
        ),
        brand="Maison Martin Margiela",
        current_price=4_000,
        buyout_price=4_000,
    )
    settings = _settings()
    evaluation = evaluate_listing(listing, settings=settings)

    assessment = assess_stage_a(
        listing,
        evaluation,
        _market_spec("margiela", "market_buyout"),
        settings,
    )

    assert evaluation.desirability.score >= 40
    assert evaluation.auth_risk.level == "do_not_touch"
    assert assessment.hard_veto is True
    assert assessment.eligible is False
    assert assessment.eligible_paths == ()
    assert assessment.reject_reason == "severe_auth_veto"


def test_known_mcq_card_gets_bounded_low_price_inspection() -> None:
    listing = _listing(
        "p1231987775",
        "McQ Alexander McQueen 15SS Arc",
        brand="Alexander McQueen",
        category="unknown",
        current_price=3_800,
        buyout_price=3_800,
    )
    settings = _settings()
    evaluation = evaluate_listing(listing, settings=settings)

    assessment = assess_stage_a(
        listing,
        evaluation,
        _market_spec("alexander_mcqueen", "market_buyout"),
        settings,
    )

    assert evaluation.desirability.score == 18
    assert evaluation.profile.season and evaluation.profile.season.value == "15SS"
    assert evaluation.profile.item_type is None
    assert assessment.eligible is True
    assert assessment.primary_path == "market_value"
    assert set(assessment.eligible_paths) == {"market_value", "unknown_interesting"}


def test_known_undercover_card_uses_engineer_boot_identity() -> None:
    listing = _listing(
        "t1231271479",
        "アンダーカバー グラデーションニットブーツ エンジニアブーツ",
        brand="Undercover",
        current_price=25_000,
        buyout_price=25_000,
    )
    settings = _settings()
    evaluation = evaluate_listing(listing, settings=settings)

    assessment = assess_stage_a(
        listing,
        evaluation,
        _market_spec("undercover", "market_buyout"),
        settings,
    )

    assert evaluation.desirability.score == 20
    assert evaluation.profile.item_type and evaluation.profile.item_type.value == "footwear"
    assert evaluation.profile.item_type.matched_term == "エンジニアブーツ"
    assert evaluation.profile.collaboration is None
    assert assessment.eligible is True
    assert assessment.primary_path == "market_value"
    assert assessment.eligible_paths == ("market_value",)


def test_early_current_bid_cannot_make_buyout_an_unknown_cheap_candidate() -> None:
    listing = _listing(
        "provisional-current",
        "Maison Margiela denim jeans",
        brand="Maison Martin Margiela",
        current_price=1_000,
        buyout_price=25_000,
        time_remaining="6 day(s)",
    )
    settings = _settings()
    assessment = assess_stage_a(
        listing,
        evaluate_listing(listing, settings=settings),
        _market_spec("margiela", "market_ending"),
        settings,
    )

    assert assessment.actionable_buyout_fraction == 1.0
    assert "market_value" in assessment.eligible_paths
    assert "unknown_interesting" not in assessment.eligible_paths
    assert "ending_auction" not in assessment.eligible_paths


def test_high_card_risk_can_reach_detail_but_explicit_fake_still_cannot() -> None:
    settings = _settings()
    spec = _market_spec("chrome_hearts", "market_buyout", maximum_jpy=10_000)
    risky = _listing(
        "silver-review",
        "Chrome Hearts 925 silver ring",
        brand="Chrome Hearts",
        current_price=4_000,
        buyout_price=4_000,
    )
    fake = replace(
        risky,
        source_listing_id="silver-fake",
        title_original="Chrome Hearts fake 925 silver ring",
    )
    risky_evaluation = evaluate_listing(risky, settings=settings)
    fake_evaluation = evaluate_listing(fake, settings=settings)

    risky_assessment = assess_stage_a(risky, risky_evaluation, spec, settings)
    fake_assessment = assess_stage_a(fake, fake_evaluation, spec, settings)

    assert risky_evaluation.auth_risk.level == "high"
    assert risky_assessment.eligible is True
    assert risky_assessment.primary_path == "silver_jewelry"
    assert fake_evaluation.auth_risk.level == "do_not_touch"
    assert fake_assessment.eligible is False


def test_counterfeit_terms_use_tokens_without_weakening_explicit_detection() -> None:
    settings = _settings()
    harmless = _listing(
        "stereotype",
        "Chrome Hearts STEREOTYPE 925 silver ring",
        brand="Chrome Hearts",
    )
    standalone = replace(
        harmless,
        source_listing_id="standalone-type",
        title_original="Chrome Hearts type 925 silver ring",
    )
    japanese = replace(
        harmless,
        source_listing_id="japanese-replica",
        title_original="クロムハーツ レプリカ 925 リング",
    )

    harmless_evaluation = evaluate_listing(harmless, settings=settings)
    standalone_evaluation = evaluate_listing(standalone, settings=settings)
    japanese_evaluation = evaluate_listing(japanese, settings=settings)

    assert harmless_evaluation.auth_risk.level != "do_not_touch"
    assert standalone_evaluation.auth_risk.level == "do_not_touch"
    assert japanese_evaluation.auth_risk.level == "do_not_touch"


def test_trailing_marketplace_x_is_not_collaboration_evidence() -> None:
    listing = _listing(
        "f1216156045",
        "ニードルス NEEDLES Vネック カーディガン 羽織 X",
        brand="Needles",
        current_price=7_500,
        buyout_price=7_500,
    )
    evaluation = evaluate_listing(listing, settings=_settings())

    assert evaluation.profile.item_type and evaluation.profile.item_type.value == "knitwear"
    assert evaluation.profile.collaboration is None
    assert evaluation.desirability.score == 20


@pytest.mark.parametrize(
    ("wording", "item_type", "category"),
    [
        ("graphic t-shirt", "t_shirt", "menswear"),
        ("長袖 シャツ", "shirt", "menswear"),
        ("モヘア カーディガン", "knitwear", "menswear"),
        ("hooded sweatshirt", "hoodie", "menswear"),
        ("再構築 デニム ジーンズ", "denim", "menswear"),
        ("カーゴ パンツ", "trousers", "menswear"),
        ("レザー ジャケット", "jacket", "menswear"),
        ("engineer boots", "footwear", "accessory"),
        ("leather shoulder bag", "bag", "accessory"),
        ("sterling silver chain necklace", "jewelry", "jewelry"),
    ],
)
def test_lightweight_card_item_types_cover_marketplace_categories(
    wording: str, item_type: str, category: str
) -> None:
    profile = enrich_title(f"Maison Margiela {wording}")

    assert profile.item_type and profile.item_type.value == item_type
    assert profile.category and profile.category.value == category


def test_prior_production_market_near_misses_have_conservative_card_outcomes() -> None:
    settings = _settings()
    cases = [
        (
            _listing(
                "d1239838430",
                "VETEMENTS ヴェトモン 2018AW Elephan",
                current_price=28_270,
                buyout_price=28_270,
                time_remaining="15 hour(s)",
            ),
            _market_spec("vetements", "market_buyout", maximum_jpy=40_000),
            "market_value",
        ),
        (
            _listing(
                "f1216156045",
                "ニードルス NEEDLES Vネック カーディガン 羽織 X",
                current_price=7_500,
                buyout_price=7_500,
            ),
            _market_spec("needles", "market_buyout", maximum_jpy=9_000),
            "market_value",
        ),
        (
            _listing(
                "t1231271479",
                "アンダーカバー グラデーションニットブーツ エンジニアブーツ",
                current_price=25_000,
                buyout_price=25_000,
            ),
            _market_spec("undercover", "market_buyout", maximum_jpy=25_000),
            "market_value",
        ),
        (
            _listing(
                "p1231987775",
                "McQ Alexander McQueen 15SS Arc",
                current_price=3_800,
                buyout_price=3_800,
            ),
            _market_spec("alexander_mcqueen", "market_buyout"),
            "market_value",
        ),
        (
            _listing(
                "j1242941908",
                "バレンシアガ 90s 総柄 アシンメトリー 長袖 シャツ X",
                current_price=6_980,
                buyout_price=None,
                time_remaining="1 day(s)",
            ),
            _market_spec("balenciaga", "market_ending", maximum_jpy=35_000),
            None,
        ),
        (
            _listing(
                "v1140788266",
                "Maison Margiela メゾンマルジェラ 再構築 デ",
                current_price=48_800,
                buyout_price=58_800,
                time_remaining="3 day(s)",
            ),
            _price_band_spec("margiela", 25_001, 50_000),
            None,
        ),
    ]

    outcomes = {}
    for listing, spec, expected_path in cases:
        assessment = assess_stage_a(
            listing,
            evaluate_listing(listing, settings=settings),
            spec,
            settings,
        )
        outcomes[listing.source_listing_id] = assessment.primary_path
        assert assessment.primary_path == expected_path

    assert outcomes == {
        "d1239838430": "market_value",
        "f1216156045": "market_value",
        "t1231271479": "market_value",
        "p1231987775": "market_value",
        "j1242941908": None,
        "v1140788266": None,
    }
