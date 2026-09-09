from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import yaml

from buyeeye.db import AlertState
from buyeeye.discord_alerts import build_listing_alert_payload
from buyeeye.evaluation import evaluate_listing
from buyeeye.fashion import enrich_title, load_taxonomy
from buyeeye.live_sources import LiveListing
from buyeeye.models import Comp
from buyeeye.pipeline import (
    AlertMaterialSnapshot,
    _alert_event_decision,
    _directional_why,
    _post_detail_decision,
)
from buyeeye.settings import CONFIG_DIR

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _listing(
    title: str,
    *,
    price: int = 8_000,
    buyout: int | None = 8_000,
    remaining: str = "5 day(s)",
    observed: dict | None = None,
    discovery_maximum: int | None = None,
) -> LiveListing:
    raw = {
        "stage_b": {"status": "enriched", "observed": observed or {}},
        "detail_photo_count": 6,
        "seller_feedback_count": 300,
    }
    if discovery_maximum is not None:
        raw["discovery"] = {
            "query_layer": "market_structure",
            "search_filters": {"max_buyout_price_jpy": discovery_maximum},
        }
    return LiveListing(
        source="buyee",
        source_listing_id="semantic-case",
        url="https://buyee.jp/item/semantic-case",
        title_original=title,
        title_normalized=title.casefold(),
        brand=None,
        category="menswear",
        current_price=price,
        buyout_price=buyout,
        starting_price=price,
        seller_name="seller",
        condition="Used",
        time_remaining=remaining,
        bid_count=2,
        watch_count=5,
        raw=raw,
    )


def _snapshot(price: int) -> AlertMaterialSnapshot:
    return AlertMaterialSnapshot(
        route="buyouts",
        thesis="asymmetric_value",
        price_jpy=price,
        price_kind="buyout",
        maturity_state="fixed_buyout",
        identity="dior homme|core|denim|-|-||manufacturer|product|unknown|clear|-",
        identity_confidence="high",
        risk_state="low",
        evidence_state="us_resale_unverified",
        verified_median_jpy=None,
    )


def _alert_state(price: int) -> AlertState:
    return AlertState(
        source="buyee",
        source_listing_id="semantic-case",
        signature="stable",
        route="buyouts",
        thesis="asymmetric_value",
        material_state_version=1,
        last_material_price_jpy=price,
        last_material_price_kind="buyout",
        last_material_maturity_state="fixed_buyout",
        last_material_identity=_snapshot(price).identity,
        last_material_risk_state="low",
        last_material_evidence_state="us_resale_unverified",
        last_verified_median_jpy=None,
        last_alert_event="initial_alert",
        last_alert_at=NOW - timedelta(hours=13),
    )


def test_final_opportunity_is_invariant_to_discovery_search_ceiling() -> None:
    title = "Maison Margiela reconstruction graphic denim jacket"
    narrow = evaluate_listing(
        _listing(title, discovery_maximum=10_000)
    )
    wide = evaluate_listing(
        _listing(title, discovery_maximum=100_000)
    )

    assert narrow.directional.discovery_price_fraction is None
    assert wide.directional.discovery_price_fraction is None
    assert narrow.directional.score == wide.directional.score
    assert narrow.directional.thesis == wide.directional.thesis
    assert _post_detail_decision(
        _listing(title, discovery_maximum=10_000), narrow
    ).route == _post_detail_decision(
        _listing(title, discovery_maximum=100_000), wide
    ).route
    assert "targeted market slice" not in _directional_why(narrow)


def test_style_reference_is_not_target_manufacturer_or_review_candidate() -> None:
    listing = _listing("クロムハーツ風 925 シルバー リング", price=830)
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.relationship
    assert evaluation.profile.relationship.value == "homage_reference"
    assert evaluation.profile.product_scope.target_role == "style_reference"
    assert evaluation.identity.confidence == "low"
    assert _post_detail_decision(listing, evaluation).rejection_reason == (
        "target_identity_mismatch"
    )


def test_style_word_not_anchored_to_brand_does_not_change_manufacturer() -> None:
    profile = enrich_title("Chrome Hearts biker style 925 silver ring")

    assert profile.relationship and profile.relationship.value == "core"
    assert profile.product_scope.target_role == "manufacturer"
    assert not profile.negative_signals


def test_cultural_reference_and_detail_brand_do_not_become_prada() -> None:
    listing = _listing(
        "The Devil Wears Prada a.v.v graphic t-shirt",
        observed={"brand_observed": "a.v.v"},
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.relationship
    assert evaluation.profile.relationship.value == "homage_reference"
    assert evaluation.profile.product_scope.target_role == "contradicted"
    assert evaluation.identity.confidence == "low"
    assert _post_detail_decision(listing, evaluation).route is None


def test_packaging_container_and_partial_offer_have_distinct_scope() -> None:
    container = enrich_title("Prada sunglasses case")
    packaging = enrich_title("Prada sunglasses case only")
    partial_title = "Number (N)ine jacket pair left sold, right remains"
    partial = enrich_title(partial_title)
    partial_listing = _listing(partial_title, price=4_000)
    partial_evaluation = evaluate_listing(partial_listing)

    assert container.item_type and container.item_type.value == "eyewear_case"
    assert container.product_scope.offer_kind == "container"
    assert packaging.product_scope.offer_kind == "packaging_only"
    assert not packaging.product_scope.acquisition_attributable
    assert partial.product_scope.offer_clarity == "partial"
    assert partial.product_scope.remaining_variant == "right"
    assert not partial.product_scope.acquisition_attributable
    assert partial_evaluation.economics.landed_cost_jpy is None
    assert _post_detail_decision(
        partial_listing, partial_evaluation
    ).rejection_reason == "partial_offer_ambiguous"


def test_kids_scope_is_grammatical_not_global_token_blacklist() -> None:
    kids_product = enrich_title("Undercover kids jacket size 120")
    mixed_script_product = enrich_title(
        "UNDERCOVER スカルポロシャツ KIDS Tシャツ"
    )
    cultural_name = enrich_title("Undercover Kids See Ghosts graphic t-shirt")

    assert kids_product.product_scope.audience == "kids"
    assert mixed_script_product.product_scope.audience == "kids"
    assert cultural_name.product_scope.audience == "unknown"
    mixed_listing = _listing("UNDERCOVER スカルポロシャツ KIDS Tシャツ")
    assert _post_detail_decision(
        mixed_listing, evaluate_listing(mixed_listing)
    ).route is None


def test_eyewear_case_is_represented_as_container_not_sunglasses() -> None:
    for title in (
        "Alexander McQueen sunglasses case",
        "Alexander McQueen メガネケース",
    ):
        listing = _listing(title, price=3_000)
        evaluation = evaluate_listing(listing)

        assert evaluation.profile.item_type
        assert evaluation.profile.item_type.value == "eyewear_case"
        assert evaluation.profile.product_scope.offer_kind == "container"
        assert evaluation.directional.thesis is None
        assert _post_detail_decision(listing, evaluation).route is None


def test_brand_observed_conflict_stops_mainline_value() -> None:
    listing = _listing(
        "Prada graphic t-shirt",
        price=2_000,
        observed={"brand_observed": "a.v.v"},
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.product_scope.target_role == "contradicted"
    assert evaluation.economics.landed_cost_jpy is None
    assert evaluation.directional.thesis is None
    assert _post_detail_decision(listing, evaluation).route is None


def test_gu_undercover_collaboration_needs_exceptional_price_and_piece_signal() -> None:
    strong = _listing("GU × Undercover distressed denim jeans", price=200, buyout=200)
    plain = _listing("GU × Undercover plain denim jeans", price=200, buyout=200)
    normal_price = _listing(
        "GU × Undercover distressed denim jeans", price=25_000, buyout=25_000
    )
    strong_evaluation = evaluate_listing(strong)

    assert strong_evaluation.profile.relationship
    assert strong_evaluation.profile.relationship.value == "collaboration"
    assert strong_evaluation.profile.relationship.participants == ("gu", "undercover")
    assert strong_evaluation.profile.designer is None
    assert strong_evaluation.directional.thesis == "asymmetric_value"
    assert _post_detail_decision(strong, strong_evaluation).route == "buyouts"
    assert evaluate_listing(plain).directional.thesis is None
    assert evaluate_listing(normal_price).directional.thesis is None


def test_balenciaga_sport_can_be_a_bounded_diffusion_ping_without_becoming_a_grail() -> None:
    interesting = _listing(
        "Balenciaga Sport vintage embroidered paneled blouson jacket",
        price=8_000,
        buyout=8_000,
    )
    ordinary_price = _listing(
        "Balenciaga Sport vintage embroidered paneled blouson jacket",
        price=20_000,
        buyout=20_000,
    )
    evaluation = evaluate_listing(interesting)

    assert evaluation.profile.relationship
    assert evaluation.profile.relationship.value == "diffusion"
    assert evaluation.directional.thesis == "asymmetric_value"
    assert _post_detail_decision(interesting, evaluation).route == "buyouts"
    assert _post_detail_decision(
        ordinary_price, evaluate_listing(ordinary_price)
    ).route is None


def test_piece_matched_japan_ended_supports_direction_not_us_fmv() -> None:
    listing = _listing(
        "Number (N)ine 01SS Time Migration shotgun graphic t-shirt",
        price=12_000,
        buyout=12_000,
    )
    sales = [
        Comp("yahoo_ended", listing.title_original, price, 1.0, 20)
        for price in (30_000, 32_000, 35_000)
    ]
    evaluation = evaluate_listing(listing, japan_comps=sales)

    assert evaluation.valuation.confidence == "unavailable"
    assert evaluation.economics.resale_median_jpy is None
    assert evaluation.directional.us_valuation_state == "us_resale_unverified"
    assert evaluation.directional.japan_market_median_jpy == 32_000
    assert evaluation.directional.japan_market_fraction == 0.375
    assert any(
        "piece-matched Japan ended-sales evidence" in reason
        for reason in evaluation.directional.reasons
    )


def test_broad_or_active_japan_observations_do_not_drive_directional_value() -> None:
    listing = _listing("Prada graphic t-shirt", price=12_000, buyout=12_000)
    broad = [
        Comp("yahoo_ended", "Prada logo t-shirt", 80_000, 1.0, 20)
        for _ in range(3)
    ]
    active = [
        Comp(
            "yahoo_ended",
            listing.title_original,
            80_000,
            1.0,
            20,
            is_sold=False,
        )
        for _ in range(3)
    ]

    assert evaluate_listing(listing, japan_comps=broad).directional.japan_market_median_jpy is None
    assert evaluate_listing(listing, japan_comps=active).directional.japan_market_median_jpy is None


def test_collection_name_alone_cannot_become_early_watch_or_ending_alert() -> None:
    early = _listing(
        "Dior Homme 04AW Strip jeans",
        price=1_000,
        buyout=None,
        remaining="5 day(s)",
    )
    ending = _listing(
        "Dior Homme 04AW Strip jeans",
        price=70_000,
        buyout=None,
        remaining="45 minute(s)",
    )

    assert _post_detail_decision(early, evaluate_listing(early)).route is None
    assert _post_detail_decision(ending, evaluate_listing(ending)).route is None


def test_specific_true_grail_can_watch_early_and_remind_when_ending() -> None:
    title = "Number (N)ine 08AW A Closed Feeling destroy mohair patchwork jacket"
    early = _listing(title, price=1_000, buyout=None, remaining="5 day(s)")
    ending = _listing(title, price=9_000, buyout=None, remaining="45 minute(s)")

    assert _post_detail_decision(early, evaluate_listing(early)).route == "grails"
    assert _post_detail_decision(ending, evaluate_listing(ending)).route == "ending"


def test_scale_aware_price_drop_requires_relative_materiality() -> None:
    tiny_expensive = _alert_event_decision(
        _alert_state(728_000), _snapshot(718_000), now=NOW
    )
    meaningful_expensive = _alert_event_decision(
        _alert_state(728_000), _snapshot(690_000), now=NOW
    )
    meaningful_cheap = _alert_event_decision(
        _alert_state(8_000), _snapshot(6_500), now=NOW
    )

    assert tiny_expensive.should_alert is False
    assert tiny_expensive.event == "unchanged_material_state"
    assert meaningful_expensive.event == "material_price_drop"
    assert meaningful_cheap.event == "material_price_drop"


def test_counterfeit_boundaries_do_not_veto_legitimate_words() -> None:
    stereotype = enrich_title("Chrome Hearts STEREOTYPE 925 silver ring")
    copyright = enrich_title("Chrome Hearts コピーライト graphic t-shirt")
    faux_material = enrich_title("Chrome Hearts faux leather jacket")
    replica = enrich_title("クロムハーツ コピー品 925 リング")

    for profile in (stereotype, copyright, faux_material):
        assert not any(
            signal.value.startswith("counterfeit:")
            for signal in profile.negative_signals
        )
    assert any(
        signal.value.startswith("counterfeit:")
        for signal in replica.negative_signals
    )


def test_all_retained_brand_families_have_taxonomy_recognition() -> None:
    retained = (
        "Number (N)ine",
        "Takahiromiyashita TheSoloist.",
        "Undercover",
        "Kapital",
        "Hysteric Glamour",
        "IF SIX WAS NINE",
        "L.G.B.",
        "Roen",
        "Mastermind Japan",
        "Needles",
        "Rebuild by Needles",
        "Yohji Yamamoto",
        "Comme des Garçons Homme Plus",
        "Junya Watanabe",
        "Issey Miyake",
        "Final Home",
        "Dior Homme",
        "Saint Laurent",
        "Raf Simons",
        "Helmut Lang",
        "Maison Martin Margiela",
        "Jean Paul Gaultier",
        "Vivienne Westwood",
        "Rick Owens",
        "DRKSHDW",
        "Prada",
        "Miu Miu",
        "Gucci",
        "Dolce & Gabbana",
        "Roberto Cavalli",
        "Oakley",
        "Vetements",
        "Balenciaga",
        "Enfants Riches Déprimés",
        "Y/Project",
        "ALYX",
        "Chrome Hearts",
        "Gaboratory",
        "King Baby",
        "Alexander McQueen",
        "Vintage band tees",
    )
    taxonomy = load_taxonomy()

    assert len(taxonomy["brands"]) >= len(retained) - 1
    assert all(enrich_title(f"{name} jacket").brand for name in retained)
    assert enrich_title("Rebuild by Needles jacket").brand.value == "Rebuild by Needles"

    configured = yaml.safe_load(
        (CONFIG_DIR / "brands.yaml").read_text(encoding="utf-8")
    )
    for alias, canonical in configured["alias_map"].items():
        if alias in {"Rebuild", "McQueen"}:
            # These remain useful exact query/config shorthands but are unsafe
            # as unconditional tokens in arbitrary marketplace titles.
            continue
        profile = enrich_title(f"{alias} jacket")
        assert profile.brand and profile.brand.value == canonical


def test_rebuild_is_contextual_not_an_unconditional_needles_brand_alias() -> None:
    neighbor = _listing(
        "Vintage Levi's rebuild patchwork distressed jeans",
        price=3_000,
        buyout=3_000,
    )
    neighbor_evaluation = evaluate_listing(neighbor)
    canonical = enrich_title("Rebuild by Needles 7 Cut reconstruction flannel shirt")

    assert not (
        neighbor_evaluation.profile.brand
        and neighbor_evaluation.profile.brand.value == "Rebuild by Needles"
    )
    assert _post_detail_decision(neighbor, neighbor_evaluation).route is None
    assert canonical.brand and canonical.brand.value == "Rebuild by Needles"


def test_steve_mcqueen_reference_is_not_alexander_mcqueen_brand_evidence() -> None:
    neighbor = enrich_title("Steve McQueen movie photo graphic t-shirt")
    explicit = enrich_title("Alexander McQueen skull graphic t-shirt")
    diffusion = enrich_title("McQ skull graphic t-shirt")

    assert not neighbor.brand
    assert explicit.brand and explicit.brand.value == "Alexander McQueen"
    assert diffusion.brand and diffusion.brand.value == "Alexander McQueen"


@pytest.mark.parametrize(
    ("title", "observed_brand", "canonical"),
    (
        (
            "Comme des Garcons Homme Plus paneled jacket",
            "Comme des Garcons",
            "Comme des Garçons Homme Plus",
        ),
        (
            "Takahiromiyashita TheSoloist distressed jacket",
            "TAKAHIROMIYASHITATheSoloist.",
            "Takahiromiyashita TheSoloist.",
        ),
        ("Dior Homme distressed jacket", "Dior", "Dior Homme"),
        (
            "Rebuild by Needles 7 Cut reconstruction shirt",
            "Needles",
            "Rebuild by Needles",
        ),
        ("Alexander McQueen skull jacket", "McQueen", "Alexander McQueen"),
    ),
)
def test_observed_parent_brand_is_compatible_with_specific_target_family(
    title: str,
    observed_brand: str,
    canonical: str,
) -> None:
    profile = enrich_title(title, brand_observed=observed_brand)

    assert profile.brand and profile.brand.value == canonical
    assert profile.product_scope.target_role != "contradicted"
    assert not profile.product_scope.hard_non_target


@pytest.mark.parametrize("observed_brand", ("No Brand", "ノーブランド"))
def test_explicit_no_brand_observation_contradicts_target_manufacturer(
    observed_brand: str,
) -> None:
    listing = _listing(
        "Prada painted graphic t-shirt",
        price=2_000,
        observed={"brand_observed": observed_brand},
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.product_scope.target_role == "contradicted"
    assert evaluation.economics.landed_cost_jpy is None
    assert evaluation.directional.thesis is None
    assert _post_detail_decision(listing, evaluation).route is None


@pytest.mark.parametrize(
    "title",
    (
        "Devil Wears Prada movie graphic t-shirt",
        "プラダを着た悪魔 movie graphic t-shirt",
    ),
)
def test_prada_cultural_phrase_without_detail_brand_is_not_manufacturer_evidence(
    title: str,
) -> None:
    listing = _listing(title, price=2_000, buyout=2_000)
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.relationship
    assert evaluation.profile.relationship.value == "homage_reference"
    assert evaluation.profile.product_scope.target_role == "style_reference"
    assert evaluation.identity.confidence == "low"
    assert evaluation.directional.thesis is None
    assert _post_detail_decision(listing, evaluation).route is None


def test_yahoo_ended_archetype_matches_are_not_called_piece_matched_directional_evidence() -> None:
    listing = _listing(
        "Prada painted leather jacket",
        price=15_000,
        buyout=15_000,
    )
    broad_archetype_sales = [
        Comp(
            "yahoo_ended",
            f"Prada plain leather jacket size {size}",
            price,
            1.0,
            20,
            source_listing_id=f"plain-prada-{size}",
        )
        for size, price in ((44, 48_000), (46, 50_000), (48, 52_000))
    ]
    evaluation = evaluate_listing(listing, japan_comps=broad_archetype_sales)

    assert evaluation.directional.japan_market_median_jpy is None
    assert evaluation.directional.japan_market_fraction is None
    assert not any(
        "piece-matched Japan ended-sales evidence" in reason
        for reason in evaluation.directional.reasons
    )


@pytest.mark.parametrize(
    "descriptor",
    (
        "runway jeans",
        "tailored jacket",
        "one button blazer",
        "1B blazer",
    ),
)
def test_weak_grail_descriptors_do_not_make_an_expensive_collection_item_alertable(
    descriptor: str,
) -> None:
    listing = _listing(
        f"Dior Homme 04AW Strip {descriptor}",
        price=200_000,
        buyout=200_000,
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.directional.thesis is None
    assert _post_detail_decision(listing, evaluation).route is None


@pytest.mark.parametrize(
    ("title", "price"),
    (
        (
            (
                "Maison Martin Margiela◆本人期/アーティザナル/再構築パッチワーク切替/"
                "ボトム/M/コーデュロイ/BRW"
            ),
            110_660,
        ),
        (
            (
                "Maison Martin Margiela◆010/アーティザナル/本人期/再構築パッチワーク切替/"
                "ボトム/M/コーデュロイ/BEG"
            ),
            99_660,
        ),
    ),
)
def test_unverified_high_ticket_era_construction_is_not_a_grail(
    title: str,
    price: int,
) -> None:
    listing = _listing(
        title,
        price=price,
        buyout=price,
        observed={
            "category_hierarchy": [
                "Auction",
                "Fashion",
                "Men's",
                "Slacks & Trousers",
                "M Size",
            ],
        },
    )
    evaluation = evaluate_listing(listing)
    decision = _post_detail_decision(listing, evaluation)

    assert evaluation.profile.era
    assert evaluation.profile.collection is None
    assert evaluation.directional.landed_usd
    assert evaluation.directional.landed_usd > 300
    assert evaluation.directional.thesis is None
    assert decision.route is None
    assert decision.rejection_reason == "unverified_high_ticket_not_iconic"


def test_same_era_construction_can_still_surface_as_a_cheap_buyout() -> None:
    listing = _listing(
        "Maison Martin Margiela 本人期 アーティザナル 再構築パッチワーク パンツ",
        price=8_000,
        buyout=8_000,
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.directional.thesis == "asymmetric_value"
    assert _post_detail_decision(listing, evaluation).route == "buyouts"


def test_verified_high_ticket_economics_still_route_as_buyout() -> None:
    listing = _listing(
        "Maison Martin Margiela 本人期 アーティザナル 再構築パッチワーク パンツ",
        price=100_000,
        buyout=100_000,
    )
    sold = [
        Comp(
            "grailed_sold",
            listing.title_original,
            price_jpy,
            1.0,
            30,
            source_listing_id=f"verified-margiela-{index}",
        )
        for index, price_jpy in enumerate(
            (450_000, 475_000, 500_000, 525_000, 550_000),
            start=1,
        )
    ]
    evaluation = evaluate_listing(listing, sold)

    assert evaluation.valuation.confidence in {"medium", "high"}
    assert _post_detail_decision(listing, evaluation).route == "buyouts"


def test_verified_negative_high_ticket_economics_do_not_rescue_era_only_grail() -> None:
    listing = _listing(
        "Maison Martin Margiela 本人期 アーティザナル 再構築パッチワーク パンツ",
        price=100_000,
        buyout=100_000,
    )
    sold = [
        Comp(
            "grailed_sold",
            listing.title_original,
            price_jpy,
            1.0,
            30,
            source_listing_id=f"negative-margiela-{index}",
        )
        for index, price_jpy in enumerate(
            (50_000, 55_000, 60_000, 65_000, 70_000),
            start=1,
        )
    ]
    evaluation = evaluate_listing(listing, sold)
    decision = _post_detail_decision(listing, evaluation)

    assert evaluation.valuation.confidence in {"medium", "high"}
    assert evaluation.economics.opportunity_score < 70
    assert evaluation.directional.thesis is None
    assert decision.route is None
    assert decision.rejection_reason == "unverified_high_ticket_not_iconic"


def test_high_ticket_exceptional_heat_cannot_be_stacked_from_era_boilerplate() -> None:
    listing = _listing(
        (
            "Maison Martin Margiela 01SS 010 本人期 アーティザナル 再構築 "
            "パッチワーク leather T-shirt"
        ),
        price=100_000,
        buyout=100_000,
    )
    evaluation = evaluate_listing(listing)
    decision = _post_detail_decision(listing, evaluation)

    assert evaluation.profile.collection is None
    assert evaluation.desirability.score >= 82
    assert evaluation.directional.thesis is None
    assert decision.route is None
    assert decision.rejection_reason == "unverified_high_ticket_not_iconic"


def test_high_ticket_scab_requires_named_piece_evidence_beyond_graphic() -> None:
    generic = _listing(
        "UNDERCOVER 03SS SCAB graphic T-shirt",
        price=50_000,
        buyout=50_000,
    )
    named = _listing(
        "UNDERCOVER 03SS SCAB No Gods graphic T-shirt",
        price=50_000,
        buyout=50_000,
    )
    generic_evaluation = evaluate_listing(generic)
    named_evaluation = evaluate_listing(named)

    assert not any(
        signal.provenance.endswith(".iconic_signals")
        for signal in generic_evaluation.profile.positive_signals
    )
    assert _post_detail_decision(generic, generic_evaluation).route is None
    assert any(
        signal.provenance.endswith(".iconic_signals")
        for signal in named_evaluation.profile.positive_signals
    )
    assert _post_detail_decision(named, named_evaluation).route == "grails"


def test_high_ticket_named_model_can_still_route_as_grail() -> None:
    listing = _listing(
        "Saint Laurent 2015 Hedi Babycat Wyatt leather boots",
        price=100_000,
        buyout=100_000,
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.collection is None
    assert sum(
        signal.provenance.endswith(".iconic_signals")
        for signal in evaluation.profile.positive_signals
    ) >= 2
    assert _post_detail_decision(listing, evaluation).route == "grails"


@pytest.mark.parametrize(
    "title",
    (
        "Maison Martin Margiela 本人期 Tabi graphic leather T-shirt",
        "Saint Laurent Hedi Wyatt graphic leather T-shirt",
        "Number (N)ine 2001 bondage graphic leather T-shirt",
        "Balenciaga Strike Bulldozer distressed leather jacket",
    ),
)
def test_high_ticket_iconic_keywords_require_compatible_item_type(title: str) -> None:
    listing = _listing(title, price=100_000, buyout=100_000)
    evaluation = evaluate_listing(listing)

    assert not any(
        signal.provenance.endswith(".iconic_signals")
        for signal in evaluation.profile.positive_signals
    )
    assert _post_detail_decision(listing, evaluation).route is None


def test_high_ticket_time_migration_with_specific_motifs_remains_grail() -> None:
    listing = _listing(
        "Number (N)ine 2001SS Time Migration shotgun skull embroidered T-shirt",
        price=100_000,
        buyout=100_000,
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.collection
    assert evaluation.profile.collection.value == "Time Migration"
    assert _post_detail_decision(listing, evaluation).route == "grails"


def test_high_ticket_grail_support_boundary_is_configurable() -> None:
    listing = _listing(
        "Maison Martin Margiela 本人期 アーティザナル 再構築パッチワーク パンツ",
        price=30_000,
        buyout=30_000,
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.directional.landed_usd
    assert 200 < evaluation.directional.landed_usd < 300
    assert _post_detail_decision(listing, evaluation).route == "grails"
    guarded = _post_detail_decision(
        listing,
        evaluation,
        unverified_grail_high_ticket_usd=200,
    )
    assert guarded.route is None
    assert guarded.rejection_reason == "unverified_high_ticket_not_iconic"


def test_mature_auction_bid_is_evaluated_independently_of_expensive_buyout() -> None:
    listing = _listing(
        "Roen skull rhinestone velour zip hoodie",
        price=1_000,
        buyout=80_000,
        remaining="45 minute(s)",
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.auction_maturity.auction_type == "open_auction"
    assert evaluation.auction_maturity.current_bid_provisional is True
    assert evaluation.auction_maturity.actionable_price_kind == "current_bid"
    assert evaluation.economics.landed_cost_jpy is not None
    assert evaluation.economics.landed_cost_jpy < listing.buyout_price
    assert evaluation.directional.thesis == "asymmetric_value"
    assert _post_detail_decision(listing, evaluation).route == "ending"
    payload = build_listing_alert_payload(
        listing, "ending", "mature current bid", 80, evaluation
    )
    assert "¥1,000 CURRENT BID" in payload["embeds"][0]["title"]
    assert "¥80,000 BUYOUT" not in payload["embeds"][0]["title"]


def test_fixed_buyout_with_expiry_cannot_route_as_ending_soon() -> None:
    listing = _listing(
        "Number (N)ine 08AW A Closed Feeling destroy mohair patchwork jacket",
        price=100_000,
        buyout=100_000,
        remaining="45 minute(s)",
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.auction_maturity.auction_type == "fixed_price"
    assert _post_detail_decision(listing, evaluation).route == "grails"


def test_multi_token_collaboration_participants_are_canonical_and_order_invariant() -> None:
    alyx_forward = enrich_title("1017 ALYX 9SM x Nike layered camo sweats")
    alyx_reverse = enrich_title("Nike x 1017 ALYX 9SM layered camo sweats")
    cdg_forward = enrich_title("Comme des Garcons Homme Plus x Nike paneled jacket")
    cdg_reverse = enrich_title("Nike x Comme des Garcons Homme Plus paneled jacket")

    assert alyx_forward.relationship and alyx_reverse.relationship
    assert alyx_forward.relationship.value == "collaboration"
    assert alyx_forward.relationship.participants == ("alyx", "nike")
    assert alyx_reverse.relationship.participants == alyx_forward.relationship.participants
    assert alyx_forward.item_type and alyx_forward.item_type.value == "trousers"
    assert alyx_forward.category and alyx_forward.category.value == "menswear"

    assert cdg_forward.relationship and cdg_reverse.relationship
    assert cdg_forward.relationship.value == "collaboration"
    assert cdg_forward.relationship.participants == (
        "comme des garçons homme plus",
        "nike",
    )
    assert cdg_reverse.relationship.participants == cdg_forward.relationship.participants


@pytest.mark.parametrize(
    "title",
    (
        "Prada dust bag graphic nylon",
        "Prada wallet box skull graphic",
    ),
)
def test_packaging_without_only_is_not_treated_as_the_branded_product(
    title: str,
) -> None:
    listing = _listing(title, price=3_000, buyout=3_000)
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.product_scope.offer_kind == "packaging_only"
    assert not evaluation.profile.product_scope.acquisition_attributable
    assert evaluation.directional.thesis is None
    assert _post_detail_decision(listing, evaluation).route is None


def test_included_packaging_and_box_logo_do_not_hide_the_actual_product() -> None:
    jacket = enrich_title("Prada nylon jacket with original dust bag")
    box_logo = enrich_title("Undercover box logo graphic t-shirt")

    assert jacket.product_scope.offer_kind == "product"
    assert jacket.product_scope.acquisition_attributable
    assert box_logo.product_scope.offer_kind == "product"
    assert box_logo.product_scope.acquisition_attributable


def test_jean_paul_gaultier_name_does_not_supply_denim_evidence() -> None:
    brand_only = enrich_title("Jean Paul Gaultier Homme archive piece")
    jacket = enrich_title("Jean Paul Gaultier Homme jean jacket")
    jeans = enrich_title("Jean Paul Gaultier Homme jeans")
    denim = enrich_title("Jean Paul Gaultier Homme denim")

    assert brand_only.brand and brand_only.brand.value == "Jean Paul Gaultier"
    assert brand_only.item_type is None
    assert jacket.item_type and jacket.item_type.value == "jacket"
    assert jeans.item_type and jeans.item_type.value == "denim"
    assert denim.item_type and denim.item_type.value == "denim"


def test_necktie_is_recognized_as_an_accessory() -> None:
    english = enrich_title("Jean Paul Gaultier patterned necktie")
    japanese = enrich_title("ジャンポールゴルチエ ネクタイ")

    assert english.item_type and english.item_type.value == "accessory"
    assert japanese.item_type and japanese.item_type.value == "accessory"


@pytest.mark.parametrize(
    "title",
    (
        "Prada leather steering wheel belt trim accessory",
        "Prada car interior trim accessory",
        "Prada vehicle parts leather trim",
        "Prada 車用ステアリング レザー ベルト",
        "Prada 純正ステアリング レザー",
        "Prada カーパーツ 内装トリム",
        "Prada シフトノブ レザー",
    ),
)
def test_automotive_offer_phrases_are_hard_non_fashion_scope(title: str) -> None:
    listing = _listing(title, price=3_500, buyout=3_500)
    evaluation = evaluate_listing(listing)
    scope = evaluation.profile.product_scope

    assert scope.offer_kind == "non_fashion"
    assert scope.hard_non_target
    assert not scope.acquisition_attributable
    assert evaluation.economics.landed_cost_jpy is None
    assert evaluation.directional.thesis is None


@pytest.mark.parametrize(
    "hierarchy",
    (
        ("Auction", "Automotive", "Interior Parts"),
        ("Auction", "Auto Parts & Accessories", "Other"),
        ("Auction", "Automobiles, Motorcycles", "Car Parts", "Interior"),
        ("オークション", "自動車、オートバイ", "パーツ", "内装品"),
    ),
)
def test_authoritative_automotive_category_rejects_keyword_stuffed_fashion_title(
    hierarchy: tuple[str, ...],
) -> None:
    profile = enrich_title(
        "Prada leather belt trim accessory",
        category_hierarchy=hierarchy,
    )

    assert profile.product_scope.offer_kind == "non_fashion"
    assert profile.product_scope.hard_non_target
    assert not profile.product_scope.acquisition_attributable


@pytest.mark.parametrize(
    ("title", "hierarchy"),
    (
        (
            "Undercover vintage car graphic t-shirt",
            ("Auction", "Fashion", "Men", "T-shirts"),
        ),
        (
            "Number (N)ine steering wheel print t-shirt",
            ("Auction", "Fashion", "Men", "T-shirts"),
        ),
        (
            "UNDERCOVER 車用ステアリング グラフィック Tシャツ",
            ("オークション", "ファッション", "メンズ", "Tシャツ"),
        ),
        (
            "Prada automotive trim inspired graphic jacket",
            ("Auction", "Fashion", "Men", "Jackets"),
        ),
        (
            "Prada contrast leather trim car coat",
            ("Auction", "Fashion", "Men", "Coats"),
        ),
    ),
)
def test_car_references_on_fashion_products_remain_products(
    title: str,
    hierarchy: tuple[str, ...],
) -> None:
    profile = enrich_title(title, category_hierarchy=hierarchy)

    assert profile.product_scope.offer_kind == "product"
    assert not profile.product_scope.hard_non_target
    assert profile.product_scope.acquisition_attributable


def test_collection_dvd_with_keyword_stuffed_garment_terms_is_media_ephemera() -> None:
    listing = _listing(
        "09AWナンバーナイン a closed feeling コレクション DVD number nine "
        "レア デニム Tシャツ レザー 宮下",
        price=3_000,
        buyout=3_000,
        observed={
            "category_hierarchy": ["Auction", "Fashion", "Men", "Other"]
        },
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.product_scope.offer_kind == "media_ephemera"
    assert evaluation.profile.product_scope.hard_non_target
    assert not evaluation.profile.product_scope.acquisition_attributable
    assert evaluation.desirability.score == 0
    assert evaluation.economics.landed_cost_jpy is None
    assert evaluation.directional.thesis is None
    assert _post_detail_decision(listing, evaluation).rejection_reason == (
        "offer_scope_mismatch"
    )


def test_explicit_collection_media_is_recognized_without_structured_category() -> None:
    dvd = enrich_title("Number (N)ine A Closed Feeling collection DVD")
    catalog = enrich_title("Undercover 2003 SCAB official catalog")

    assert dvd.product_scope.offer_kind == "media_ephemera"
    assert catalog.product_scope.offer_kind == "media_ephemera"


def test_explicit_media_category_outranks_garment_reference_keywords() -> None:
    listing = _listing(
        "Number (N)ine A Closed Feeling DVD cover graphic T-shirt rare denim",
        observed={
            "category_hierarchy": ["Collectibles", "Music", "DVD"]
        },
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.product_scope.offer_kind == "media_ephemera"
    assert not evaluation.profile.product_scope.acquisition_attributable
    assert _post_detail_decision(listing, evaluation).rejection_reason == (
        "offer_scope_mismatch"
    )


@pytest.mark.parametrize(
    ("title", "hierarchy"),
    (
        (
            "Undercover Joy Division CD cover graphic t-shirt",
            ("Music", "Apparel", "T-shirt"),
        ),
        (
            "UNDERCOVER 音楽 CDカバープリント Tシャツ",
            ("音楽", "アパレル", "Tシャツ"),
        ),
    ),
)
def test_media_ancestor_does_not_override_authoritative_apparel_leaf(
    title: str,
    hierarchy: tuple[str, ...],
) -> None:
    profile = enrich_title(title, category_hierarchy=hierarchy)

    assert profile.product_scope.offer_kind == "product"
    assert profile.product_scope.acquisition_attributable


@pytest.mark.parametrize(
    "title",
    (
        "Number (N)ine collection DVD steelbook cover artwork",
        "Number (N)ine archive DVD special coating art",
    ),
)
def test_substrings_inside_media_nouns_do_not_create_fake_garment_reference(
    title: str,
) -> None:
    profile = enrich_title(title)

    assert profile.product_scope.offer_kind == "media_ephemera"
    assert not profile.product_scope.acquisition_attributable


@pytest.mark.parametrize(
    "title",
    (
        "Number (N)ine graphic t-shirt with official collection DVD",
        "UNDERCOVER Tシャツ 公式DVD付き",
        "Dior Homme jacket カタログ付属",
    ),
)
def test_fashion_product_bundled_with_included_media_remains_product(
    title: str,
) -> None:
    profile = enrich_title(
        title,
        category_hierarchy=("Auction", "Fashion", "Men", "Other"),
    )

    assert profile.product_scope.offer_kind == "product"
    assert profile.product_scope.acquisition_attributable


@pytest.mark.parametrize(
    "title",
    (
        "Undercover A Clockwork Orange DVD cover graphic t-shirt",
        "Number (N)ine music CD print t-shirt",
        "Comme des Garcons Homme Plus CDG logo graphic shirt",
        "Dior book tote bag",
    ),
)
def test_media_references_on_actual_fashion_products_remain_products(title: str) -> None:
    profile = enrich_title(
        title,
        category_hierarchy=("Auction", "Fashion", "Men", "Other"),
    )

    assert profile.product_scope.offer_kind == "product"
    assert profile.product_scope.acquisition_attributable


def test_incidental_womens_description_does_not_override_explicit_mens_category() -> None:
    listing = _listing(
        "Undercover men's paneled jacket",
        observed={
            "description_original": (
                "This design also appeared in the women's runway presentation."
            ),
            "category_hierarchy": ["Auction", "Fashion", "Men", "Jackets"],
        },
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.product_scope.audience != "womens"
    assert not evaluation.profile.product_scope.hard_non_target
    assert _post_detail_decision(listing, evaluation).rejection_reason != "category_mismatch"


@pytest.mark.parametrize(
    "title",
    (
        "Number (N)ine jacket left chest graphic sold out model",
        "Undercover left sleeve print sold out everywhere jacket",
    ),
)
def test_garment_position_language_is_not_a_partial_sale(title: str) -> None:
    profile = enrich_title(title)

    assert profile.product_scope.offer_clarity == "clear"
    assert profile.product_scope.remaining_variant is None
    assert profile.product_scope.acquisition_attributable


def test_stage_b_failure_cannot_route_high_heat_listing_as_grail_or_ending() -> None:
    title = "Number (N)ine 08AW A Closed Feeling destroy mohair patchwork jacket"
    fixed = _listing(title, price=100_000, buyout=100_000, remaining="5 day(s)")
    ending = _listing(title, price=9_000, buyout=None, remaining="45 minute(s)")
    for listing in (fixed, ending):
        listing.raw = {
            "stage_b": {"status": "failed", "error_type": "TimeoutError"},
            "detail_photo_count": 0,
            "seller_feedback_count": 0,
        }

    fixed_evaluation = evaluate_listing(fixed)
    ending_evaluation = evaluate_listing(ending)

    assert fixed_evaluation.identity.confidence != "high"
    assert ending_evaluation.identity.confidence != "high"
    assert _post_detail_decision(fixed, fixed_evaluation).route not in {
        "grails",
        "ending",
    }
    assert _post_detail_decision(ending, ending_evaluation).route not in {
        "grails",
        "ending",
    }


@pytest.mark.parametrize(
    "title",
    (
        "Dior Homme patterned silk necktie",
        "Miu Miu leather bifold wallet",
        "Saint Laurent tailored blazer",
        "Christian Louboutin leather derby shoes",
    ),
)
def test_ordinary_accessory_and_formal_archetypes_need_independent_evidence(
    title: str,
) -> None:
    listing = _listing(title, price=4_000, buyout=4_000)
    evaluation = evaluate_listing(listing)
    decision = _post_detail_decision(listing, evaluation)

    assert evaluation.directional.thesis is None
    assert decision.route is None
    assert decision.rejection_reason == "generic_after_detail"


def test_unusual_formalwear_escapes_genericness_and_can_be_micro_asymmetry() -> None:
    listing = _listing(
        "Yves Saint Laurent open collar striped shirt",
        price=4_000,
        buyout=4_000,
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.directional.thesis == "asymmetric_value"
    assert evaluation.directional.micro_asymmetry is True
    assert _post_detail_decision(listing, evaluation).route == "buyouts"


def test_cheap_specific_tee_is_micro_asymmetry_without_invented_fmv() -> None:
    listing = _listing(
        "Number (N)ine 03SS layered striped graphic t-shirt",
        price=1_200,
        buyout=1_200,
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.valuation.confidence == "unavailable"
    assert evaluation.economics.resale_median_jpy is None
    assert evaluation.directional.thesis == "asymmetric_value"
    assert evaluation.directional.micro_asymmetry is True
    assert _post_detail_decision(listing, evaluation).route == "buyouts"


@pytest.mark.parametrize(
    "title",
    (
        "Celine Jacno boots",
        "Celine Camargue boots",
        "Christian Louboutin Louis Orlato sneakers",
    ),
)
def test_item_constrained_iconic_footwear_can_support_micro_asymmetry(
    title: str,
) -> None:
    listing = _listing(title, price=2_500, buyout=2_500)
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.item_type
    assert evaluation.profile.item_type.value == "footwear"
    assert evaluation.directional.micro_asymmetry is True


def test_cheap_legacy_ysl_chelsea_boot_has_directional_footwear_context() -> None:
    listing = _listing(
        "Yves Saint Laurent leather Chelsea boots",
        price=5_600,
        buyout=5_600,
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.item_type
    assert evaluation.profile.item_type.value == "footwear"
    assert evaluation.directional.micro_asymmetry is True
    assert _post_detail_decision(listing, evaluation).route == "buyouts"


@pytest.mark.parametrize(
    "title",
    (
        "Puma x Alexander McQueen leather sneakers",
        "Rick Owens x Champion logo cap",
        "Puma x Alexander McQueen distressed sneaker",
        "Rick Owens x Champion graphic cap",
        "GU x UNDERCOVER distressed shirt",
    ),
)
def test_collaboration_name_alone_cannot_establish_substantive_value(title: str) -> None:
    listing = _listing(title, price=4_000, buyout=4_000)
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.relationship
    assert evaluation.profile.relationship.value == "collaboration"
    assert evaluation.directional.thesis is None
    assert _post_detail_decision(listing, evaluation).route is None


@pytest.mark.parametrize(
    "title",
    (
        "Saint Laurent Hedi silk necktie",
        "Dior Homme Hedi dress shirt",
        "Dior Homme Hedi tailored blazer",
        "Celine Hedi Slimane leather wallet",
    ),
)
def test_bare_designer_era_does_not_rescue_generic_product(title: str) -> None:
    listing = _listing(title, price=4_000, buyout=4_000)
    evaluation = evaluate_listing(listing)

    assert evaluation.directional.thesis is None
    assert _post_detail_decision(listing, evaluation).rejection_reason == (
        "generic_after_detail"
    )


def test_weird_tie_can_escape_without_blanket_category_ban() -> None:
    listing = _listing(
        "Dior Homme studded embroidered skull silk necktie",
        price=4_000,
        buyout=4_000,
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.item_type
    assert evaluation.profile.item_type.value == "accessory"
    assert evaluation.directional.micro_asymmetry is True
    assert _post_detail_decision(listing, evaluation).route == "buyouts"


def test_bilingual_duplicate_motif_does_not_fake_two_design_signals() -> None:
    listing = _listing(
        "Dior Homme striped ストライプ silk necktie",
        price=4_000,
        buyout=4_000,
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.directional.thesis is None
    assert _post_detail_decision(listing, evaluation).rejection_reason == (
        "generic_after_detail"
    )


@pytest.mark.parametrize(
    "title",
    (
        "Rick Owens x Champion embroidered embroidery cap",
        "Rick Owens x Champion applique appliqué cap",
        "Rick Owens x Champion one button 1B jacket",
        "Rick Owens x Champion processed 加工 shirt",
    ),
)
def test_collaboration_synonyms_cannot_fake_independent_design_signals(
    title: str,
) -> None:
    listing = _listing(title, price=4_000, buyout=4_000)
    evaluation = evaluate_listing(listing)

    assert evaluation.directional.thesis is None
    assert _post_detail_decision(listing, evaluation).route is None


@pytest.mark.parametrize(
    "title",
    (
        "Number (N)ine pointed toe platform t-shirt",
        "Number (N)ine tailored t-shirt",
    ),
)
def test_item_incompatible_design_words_cannot_create_micro_asymmetry(
    title: str,
) -> None:
    listing = _listing(title, price=2_000, buyout=2_000)
    evaluation = evaluate_listing(listing)

    assert evaluation.profile.item_type
    assert evaluation.profile.item_type.value == "t_shirt"
    assert evaluation.directional.thesis is None
    assert _post_detail_decision(listing, evaluation).route is None


@pytest.mark.parametrize(
    "title",
    (
        "Number (N)ine ジャンク graphic t-shirt",
        "Number (N)ine holes stained graphic t-shirt",
        "Number (N)ine heavily damaged graphic t-shirt",
    ),
)
def test_explicit_poor_condition_evidence_blocks_micro_asymmetry(title: str) -> None:
    listing = _listing(title, price=2_000, buyout=2_000)
    evaluation = evaluate_listing(listing)

    assert evaluation.directional.micro_asymmetry is False
    assert _post_detail_decision(listing, evaluation).route is None


def test_destroy_is_design_language_but_ambiguous_distressed_alone_is_not() -> None:
    destroy = _listing(
        "Number (N)ine destroy t-shirt",
        price=2_000,
        buyout=2_000,
    )
    distressed = _listing(
        "Number (N)ine distressed t-shirt",
        price=2_000,
        buyout=2_000,
    )

    assert evaluate_listing(destroy).directional.micro_asymmetry is True
    assert _post_detail_decision(
        destroy,
        evaluate_listing(destroy),
    ).route == "buyouts"
    assert evaluate_listing(distressed).directional.thesis is None


def test_generic_archetype_with_verified_economics_can_still_route() -> None:
    listing = _listing("Dior Homme patterned silk necktie", price=4_000, buyout=4_000)
    sold = [
        Comp(
            "ebay_sold",
            listing.title_original,
            price,
            1.0,
            30,
            source_listing_id=f"tie-{index}",
        )
        for index, price in enumerate((45_000, 48_000, 50_000, 52_000, 55_000))
    ]
    evaluation = evaluate_listing(listing, sold)

    assert evaluation.economics.opportunity_score >= 70
    assert evaluation.directional.thesis is None
    assert _post_detail_decision(listing, evaluation).route == "buyouts"


def test_generic_implausibly_cheap_chrome_is_not_review_spam() -> None:
    listing = _listing(
        "Chrome Hearts 925 silver braided bracelet",
        price=4_000,
        buyout=4_000,
    )
    evaluation = evaluate_listing(listing)
    decision = _post_detail_decision(listing, evaluation)

    assert evaluation.auth_risk.level == "high"
    assert decision.route is None
    assert decision.rejection_reason in {"seller_risk", "unreviewable_auth_noise"}


def test_specific_chrome_with_review_evidence_remains_reviewworthy() -> None:
    listing = _listing(
        "Chrome Hearts keeper floral cross 925 silver ring",
        price=4_000,
        buyout=4_000,
    )
    evaluation = evaluate_listing(listing)

    assert evaluation.auth_risk.level == "high"
    assert _post_detail_decision(listing, evaluation).route == "review"


def test_chrome_sold_comps_do_not_make_photo_less_generic_silver_reviewworthy() -> None:
    listing = _listing(
        "Chrome Hearts 925 silver braided bracelet",
        price=4_000,
        buyout=4_000,
    )
    listing.raw["detail_photo_count"] = 0
    sold = [
        Comp(
            "ebay_sold",
            listing.title_original,
            price,
            1.0,
            30,
            source_listing_id=f"chrome-{index}",
        )
        for index, price in enumerate((45_000, 48_000, 50_000, 52_000, 55_000))
    ]

    evaluation = evaluate_listing(listing, sold)
    decision = _post_detail_decision(listing, evaluation)

    assert evaluation.economics.opportunity_score >= 70
    assert decision.route is None
    assert decision.rejection_reason == "unreviewable_auth_noise"
