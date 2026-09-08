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
