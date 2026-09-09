from __future__ import annotations

from dataclasses import replace

import pytest

from buyeeye.discovery import MarketplaceSearchFilters, QuerySpec
from buyeeye.evaluation import DesirabilityResult, evaluate_listing
from buyeeye.fashion import enrich_title, expand_discovery_terms
from buyeeye.live_sources import LiveListing
from buyeeye.pipeline import _post_detail_decision
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
        "relationship_rank_adjustments": {
            "core": 12,
            "collaboration": -4,
            "diffusion": -12,
            "uncertain": -14,
            "homage_reference": -30,
        },
        "low_priority_collaboration_adjustment": -8,
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
    assert assessment.primary_path == "market_buyout_inspection"
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
    assert assessment.primary_path == "market_buyout_inspection"


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

    assert evaluation.desirability.score == 10
    assert evaluation.profile.relationship
    assert evaluation.profile.relationship.value == "diffusion"
    assert evaluation.profile.designer is None
    assert evaluation.profile.collection is None
    assert evaluation.profile.season and evaluation.profile.season.value == "15SS"
    assert evaluation.profile.item_type is None
    assert assessment.eligible is True
    assert assessment.primary_path == "unknown_interesting"
    assert assessment.eligible_paths == ("unknown_interesting",)
    assert assessment.piece_relationship == "diffusion"
    assert "mcq" not in {
        term["term"].casefold() for term in expand_discovery_terms("alexander_mcqueen")
    }


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
    assert assessment.primary_path == "market_buyout_inspection"
    assert assessment.eligible_paths == ("market_buyout_inspection",)


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
    assert "market_buyout_inspection" in assessment.eligible_paths
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
    assert standalone_evaluation.profile.relationship
    assert standalone_evaluation.profile.relationship.value == "homage_reference"
    assert standalone_evaluation.profile.product_scope.target_role == "style_reference"
    assert standalone_evaluation.auth_risk.level != "do_not_touch"
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
            "market_buyout_inspection",
        ),
        (
            _listing(
                "f1216156045",
                "ニードルス NEEDLES Vネック カーディガン 羽織 X",
                current_price=7_500,
                buyout_price=7_500,
            ),
            _market_spec("needles", "market_buyout", maximum_jpy=9_000),
            "market_buyout_inspection",
        ),
        (
            _listing(
                "t1231271479",
                "アンダーカバー グラデーションニットブーツ エンジニアブーツ",
                current_price=25_000,
                buyout_price=25_000,
            ),
            _market_spec("undercover", "market_buyout", maximum_jpy=25_000),
            "market_buyout_inspection",
        ),
        (
            _listing(
                "p1231987775",
                "McQ Alexander McQueen 15SS Arc",
                current_price=3_800,
                buyout_price=3_800,
            ),
            _market_spec("alexander_mcqueen", "market_buyout"),
            "unknown_interesting",
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
        "d1239838430": "market_buyout_inspection",
        "f1216156045": "market_buyout_inspection",
        "t1231271479": "market_buyout_inspection",
        "p1231987775": "unknown_interesting",
        "j1242941908": None,
        "v1140788266": None,
    }


def test_piece_relationship_classifies_core_diffusion_collaboration_and_homage() -> None:
    core = enrich_title("Alexander McQueen Lee McQueen bumster trousers")
    diffusion = enrich_title("McQ Alexander McQueen 15SS Arc jacket")
    collaboration = enrich_title("GU x UNDERCOVER track jacket")
    homage = enrich_title("Needles style track jacket")
    uncertain = enrich_title("unreadable jacket", hinted_brand="Undercover")

    assert core.relationship and core.relationship.value == "core"
    assert core.designer and core.era
    assert diffusion.relationship and diffusion.relationship.value == "diffusion"
    assert diffusion.designer is None
    assert diffusion.era is None
    assert diffusion.collection is None
    assert collaboration.relationship and collaboration.relationship.value == "collaboration"
    assert collaboration.relationship.provenance.endswith(".collaboration_low_priority")
    assert homage.relationship and homage.relationship.value == "homage_reference"
    assert uncertain.relationship and uncertain.relationship.value == "uncertain"


def test_core_line_outranks_diffusion_for_one_scarce_market_slot() -> None:
    settings = _settings(
        max_candidates_per_query=1,
        max_market_candidates_per_query=1,
        max_market_candidate_rate=1.0,
    )
    spec = _market_spec("alexander_mcqueen", "market_buyout")
    core = _listing(
        "core-mcqueen",
        "Alexander McQueen 15SS Arc jacket",
        current_price=3_800,
        buyout_price=3_800,
    )
    diffusion = _listing(
        "diffusion-mcq",
        "McQ Alexander McQueen 15SS Arc jacket",
        current_price=3_800,
        buyout_price=3_800,
    )
    assessments = [
        assess_stage_a(item, evaluate_listing(item, settings=settings), spec, settings)
        for item in (diffusion, core)
    ]

    selected, capped = select_stage_a_candidates(assessments, spec, settings)

    assert [item.listing_id for item in selected] == ["core-mcqueen"]
    assert [item.listing_id for item in capped] == ["diffusion-mcq"]
    assert all(item.eligible for item in assessments)


def test_declared_diffusion_word_does_not_become_a_blanket_generic_reject() -> None:
    settings = _settings()
    listing = _listing(
        "declared-diffusion",
        "McQ Alexander McQueen diffusion 15SS jacket",
        current_price=3_800,
        buyout_price=3_800,
    )
    assessment = assess_stage_a(
        listing,
        evaluate_listing(listing, settings=settings),
        _market_spec("alexander_mcqueen", "market_buyout"),
        settings,
    )

    assert assessment.piece_relationship == "diffusion"
    assert assessment.eligible is True
    assert assessment.primary_path == "market_buyout_inspection"


def test_known_diffusion_aliases_do_not_inherit_mainline_designer_context() -> None:
    drkshdw = enrich_title("Rick Owens DRKSHDW asymmetric jacket")
    mm6 = enrich_title("MM6 reconstruction denim jacket")

    assert drkshdw.relationship and drkshdw.relationship.value == "diffusion"
    assert drkshdw.designer is None
    assert mm6.brand and mm6.brand.value == "Maison Martin Margiela"
    assert mm6.relationship and mm6.relationship.value == "diffusion"
    assert mm6.designer is None


def test_low_priority_collaboration_is_demoted_without_being_generic_rejected() -> None:
    settings = _settings(
        max_candidates_per_query=1,
        max_market_candidates_per_query=1,
        max_market_candidate_rate=1.0,
    )
    spec = _market_spec("needles", "market_buyout")
    core = _listing("core-needles", "Needles track jacket")
    collaboration = _listing(
        "uniqlo-needles",
        "Needles x UNIQLO track jacket",
    )
    core_assessment = assess_stage_a(
        core, evaluate_listing(core, settings=settings), spec, settings
    )
    collaboration_assessment = assess_stage_a(
        collaboration,
        evaluate_listing(collaboration, settings=settings),
        spec,
        settings,
    )

    selected, capped = select_stage_a_candidates(
        [collaboration_assessment, core_assessment], spec, settings
    )

    assert collaboration_assessment.piece_relationship == "collaboration"
    assert collaboration_assessment.eligible is True
    assert collaboration_assessment.reject_reason is None
    assert collaboration_assessment.inspection_score < core_assessment.inspection_score
    assert [item.listing_id for item in selected] == ["core-needles"]
    assert [item.listing_id for item in capped] == ["uniqlo-needles"]


def test_piece_specific_strength_can_compensate_for_collaboration_rank_prior() -> None:
    settings = _settings(
        max_candidates_per_query=1,
        max_market_candidates_per_query=1,
        max_market_candidate_rate=1.0,
    )
    spec = _market_spec("undercover", "market_buyout")
    plain_core = _listing("plain-core", "UNDERCOVER jacket")
    strong_collaboration = _listing(
        "strong-collaboration",
        "UNDERCOVER x Valentino distressed reconstruction leather jacket",
    )
    assessments = [
        assess_stage_a(item, evaluate_listing(item, settings=settings), spec, settings)
        for item in (plain_core, strong_collaboration)
    ]

    selected, capped = select_stage_a_candidates(assessments, spec, settings)

    assert [item.listing_id for item in selected] == ["strong-collaboration"]
    assert [item.listing_id for item in capped] == ["plain-core"]
    assert assessments[1].piece_relationship == "collaboration"


def test_brand_specific_generic_signal_now_blocks_market_buyout_inspection_path() -> None:
    settings = _settings()
    listing = _listing(
        "generic-dior",
        "Dior Homme Dior logo t-shirt",
        current_price=5_000,
        buyout_price=5_000,
    )
    assessment = assess_stage_a(
        listing,
        evaluate_listing(listing, settings=settings),
        _market_spec("dior_homme", "market_buyout"),
        settings,
    )

    assert assessment.eligible is False
    assert assessment.reject_reason == "generic_or_junk"


def test_homage_relationship_rejects_but_stereotype_remains_core() -> None:
    settings = _settings()
    homage = _listing("homage", "UNDERCOVER homage distressed jacket")
    harmless = _listing("stereotype-core", "UNDERCOVER STEREOTYPE jacket")
    spec = _market_spec("undercover", "market_buyout")

    homage_assessment = assess_stage_a(
        homage, evaluate_listing(homage, settings=settings), spec, settings
    )
    harmless_assessment = assess_stage_a(
        harmless, evaluate_listing(harmless, settings=settings), spec, settings
    )

    assert homage_assessment.piece_relationship == "homage_reference"
    assert homage_assessment.eligible is False
    assert homage_assessment.reject_reason == "target_identity_mismatch"
    assert harmless_assessment.piece_relationship == "core"
    assert harmless_assessment.eligible is True


def test_japanese_chrome_hearts_homage_fails_closed_before_and_after_detail() -> None:
    settings = _settings()
    listing = _listing(
        "japanese-chrome-homage",
        "オマージュ バングル ブレスレット #クロムハーツ レナードカムフォート",
        brand="Chrome Hearts",
        category="jewelry",
        current_price=12_000,
        buyout_price=12_000,
    )
    listing.raw["stage_b"] = {"status": "enriched", "observed": {}}
    evaluation = evaluate_listing(listing, settings=settings)
    assessment = assess_stage_a(
        listing,
        evaluation,
        _market_spec("chrome_hearts", "market_buyout", maximum_jpy=20_000),
        settings,
    )
    post_detail = _post_detail_decision(listing, evaluation)

    assert evaluation.profile.relationship
    assert evaluation.profile.relationship.value == "homage_reference"
    assert evaluation.profile.relationship.matched_term == "オマージュ"
    assert assessment.eligible is False
    assert assessment.reject_reason == "target_identity_mismatch"
    assert post_detail.route is None
    assert post_detail.rejection_reason == "target_identity_mismatch"


def test_size_or_dimension_x_does_not_create_a_collaboration_relationship() -> None:
    sized = enrich_title("Rick Owens leather jacket size 48 x black")
    measured = enrich_title("Maison Margiela shirt 44cm x 50cm")

    assert sized.relationship and sized.relationship.value == "core"
    assert sized.collaboration is None
    assert measured.relationship and measured.relationship.value == "core"
    assert measured.collaboration is None


def test_diffusion_and_mass_market_collaboration_do_not_inherit_mainline_signals() -> None:
    diffusion = enrich_title("MM6 Margiela artisanal archive graphic jacket")
    mass_market = enrich_title(
        "GU x UNDERCOVER 03SS SCAB distressed reconstruction leather jacket"
    )

    assert diffusion.relationship and diffusion.relationship.value == "diffusion"
    assert diffusion.designer is None
    assert diffusion.collection is None
    assert diffusion.positive_signals == []
    assert mass_market.relationship
    assert mass_market.relationship.provenance.endswith(".collaboration_low_priority")
    assert mass_market.designer is None
    assert mass_market.collection is None
    assert mass_market.positive_signals == []


def test_margiela_replica_line_is_not_counterfeit_but_bare_replica_remains_vetoed() -> None:
    legitimate = evaluate_listing(
        _listing("replica-line", "Maison Margiela Replica Line leather sneakers")
    )
    counterfeit = evaluate_listing(
        _listing("bare-replica", "Maison Margiela leather sneakers replica")
    )
    mixed = evaluate_listing(
        _listing(
            "replica-line-plus-replica",
            "Maison Margiela Replica Line leather sneakers replica",
        )
    )

    assert legitimate.auth_risk.level != "do_not_touch"
    assert not any(
        signal.value == "counterfeit:replica"
        for signal in legitimate.profile.negative_signals
    )
    assert counterfeit.auth_risk.level == "do_not_touch"
    assert any(
        signal.value == "counterfeit:replica"
        for signal in counterfeit.profile.negative_signals
    )
    assert mixed.auth_risk.level == "do_not_touch"
    assert any(
        signal.value == "counterfeit:replica"
        for signal in mixed.profile.negative_signals
    )


def test_low_heat_semantic_card_can_earn_one_bounded_closet_inspection() -> None:
    listing = _listing(
        "semantic-vetements",
        "VETEMENTS layered French flag pullover hoodie XL",
        current_price=25_000,
        buyout_price=None,
        time_remaining="10 hour(s)",
    )
    settings = _settings()
    evaluation = evaluate_listing(listing, settings=settings)
    assessment = assess_stage_a(
        listing,
        evaluation,
        _semantic_spec("vetements"),
        settings,
    )

    assert evaluation.desirability.score < 40
    assert assessment.primary_path == "semantic_closet_inspection"
    assert assessment.eligible is True


def test_semantic_closet_path_does_not_reintroduce_early_bid_economics() -> None:
    listing = _listing(
        "early-semantic-vetements",
        "VETEMENTS layered French flag pullover hoodie XL",
        current_price=1_000,
        buyout_price=None,
        time_remaining="5 day(s)",
    )
    settings = _settings()
    assessment = assess_stage_a(
        listing,
        evaluate_listing(listing, settings=settings),
        _semantic_spec("vetements"),
        settings,
    )

    assert assessment.actionable_price is False
    assert assessment.eligible is False


def test_broad_brand_card_with_truncated_item_identity_gets_bounded_recovery() -> None:
    listing = _listing(
        "truncated-rick",
        "RICK OWENS DRKSHDW CARGO DRAWS",
        current_price=33_660,
        buyout_price=33_660,
        category="unknown",
    )
    settings = _settings()
    assessment = assess_stage_a(
        listing,
        evaluate_listing(listing, settings=settings),
        _semantic_spec("rick_owens"),
        settings,
    )

    assert assessment.item_type_confident is False
    assert assessment.primary_path == "identity_recovery"
    assert assessment.eligible is True


def test_lifetime_first_listing_outranks_rotating_candidate_for_scarce_slot() -> None:
    settings = _settings(
        max_candidates_per_query=1,
        path_caps={"semantic_closet_inspection": 1},
    )
    spec = _semantic_spec("vetements")
    rotating = _listing(
        "rotating",
        "VETEMENTS layered graphic hoodie",
        current_price=12_000,
        buyout_price=12_000,
    )
    rotating.raw = {"dedupe": {"revisit_reason": "candidate_rotation"}}
    unseen = _listing(
        "unseen",
        "VETEMENTS layered graphic hoodie",
        current_price=12_000,
        buyout_price=12_000,
    )
    unseen.raw = {"dedupe": {"lifetime_first_seen": True}}
    assessments = [
        assess_stage_a(item, evaluate_listing(item, settings=settings), spec, settings)
        for item in (rotating, unseen)
    ]

    selected, capped = select_stage_a_candidates(assessments, spec, settings)

    assert [item.listing_id for item in selected] == ["unseen"]
    assert [item.listing_id for item in capped] == ["rotating"]
