from __future__ import annotations

import pytest

from buyeeye.detail_enrichment import apply_detail, parse_detail_payload
from buyeeye.evaluation import evaluate_listing
from buyeeye.fashion import enrich_title
from buyeeye.live_sources import LiveListing
from buyeeye.models import Comp
from buyeeye.pipeline import _auth_reviewworthy, _post_detail_decision


def item(title, price=3000, **raw):
    return LiveListing(source="buyee", source_listing_id="fixture",
        url="https://buyee.jp/item/jdirectitems/auction/fixture",
        title_original=title, title_normalized=title, brand=None, category="menswear",
        current_price=price, buyout_price=price, starting_price=price,
        seller_name="fixture", condition="Used", raw=raw)


@pytest.mark.parametrize("title,kind,subtype", [
    ("CELINE chain-pattern tie", "accessory", "tie"),
    ("Needles denim tulip hat", "accessory", "hat"),
    ("Needles デニム ラップ スカート", "skirt", "skirt"),
    ("Vivienne Westwood knit gloves", "accessory", "gloves"),
    ("Roen leopard shorts", "trousers", "shorts"),
    ("Roen スカル ポロシャツ", "shirt", "polo"),
])
def test_object_not_material_or_pattern(title, kind, subtype):
    profile = enrich_title(title)
    assert profile.item_type.value == kind
    assert profile.item_subtype.value == subtype


@pytest.mark.parametrize("title", [
    "MEN’S MELROSE velour jacket Needles", "Vetements SUPERTUFF Workwear jacket",
    "silver COATING S925 bracelet クロムハーツ好きに",
])
def test_other_maker_and_fan_language_cannot_alert_via_any_thesis(title):
    listing = item(title, 1000, detail_photo_count=20, seller_feedback_count=2000)
    evaluation = evaluate_listing(listing)
    assert evaluation.profile.product_scope.hard_non_target
    assert not evaluation.directional.micro_asymmetry
    assert _post_detail_decision(listing, evaluation).route is None


def test_late_needles_brand_is_not_automatically_a_reference():
    profile = enrich_title("総柄プリント 半袖 シアー シャツ シースルー ニードルズ")
    assert profile.brand.value == "Needles"
    assert not profile.product_scope.hard_non_target


def test_sensitive_jewelry_photos_seller_or_badge_do_not_authenticate():
    listing = item("Chrome Hearts fleur de lis 925 silver pendant", 9800,
                   detail_photo_count=15, seller_feedback_count=12,
                   seller_feedback_percent=100,
                   stage_b={"status": "enriched", "observed": {"authentication_option": True}})
    evaluation = evaluate_listing(listing)
    assert evaluation.auth_risk.level == "high"
    assert _post_detail_decision(listing, evaluation).route in {None, "review"}


def _primary_image_listing(title):
    listing = item(title, 4000, seller_feedback_count=12, seller_feedback_percent=100)
    listing.source_listing_id = "a111111111"
    listing.url = "https://buyee.jp/item/jdirectitems/auction/a111111111"
    observation = parse_detail_payload("buyee", listing.source_listing_id, {
        "association_version": 1, "page_url": listing.url, "heading": title,
        "primary_images": [{"provenance": "og_image", "attributes": {
            "src": "https://cdnyauction.buyee.jp/images.auctions.yahoo.co.jp/image/primary.jpg",
        }}],
    })
    apply_detail(listing, observation)
    return listing


def test_bound_primary_image_unknown_gallery_keeps_specific_chrome_review_possible():
    listing = _primary_image_listing("Chrome Hearts keeper floral cross 925 silver ring")
    evaluation = evaluate_listing(listing)
    assert listing.raw["detail_photo_count"] == 1
    assert listing.raw["detail_photo_coverage"] == "primary_only"
    assert evaluation.auth_risk.level == "high"
    assert _post_detail_decision(listing, evaluation).route == "review"


def test_cached_primary_proof_can_keep_fresher_item_bound_card_thumbnail():
    listing = _primary_image_listing("Chrome Hearts keeper floral cross 925 silver ring")
    listing.image_url += "?revision=current"
    listing.raw["image_integrity"] = {"status": "card_bound"}
    listing.raw["search_card"] = {
        "source": listing.source, "source_listing_id": listing.source_listing_id,
        "image_url": listing.image_url,
    }
    assert _post_detail_decision(listing, evaluate_listing(listing)).route == "review"


def test_unknown_gallery_exception_requires_high_piece_identity():
    listing = _primary_image_listing("Chrome Hearts keeper floral cross 925 silver ring")
    evaluation = evaluate_listing(listing)
    evaluation.identity.confidence = "medium"
    assert not _auth_reviewworthy(listing, evaluation, credible_economics=False)


def test_verified_comp_economics_with_primary_image_still_requires_auth_review():
    listing = _primary_image_listing("Chrome Hearts keeper floral cross 925 silver ring")
    comps = [Comp("ebay_sold", listing.title_original, price, 1.0, 30,
                  source_listing_id=f"fixture-chrome-{i}")
             for i, price in enumerate((45000, 48000, 50000, 52000, 55000))]
    evaluation = evaluate_listing(listing, comps)
    assert evaluation.economics.opportunity_score >= 70
    assert evaluation.auth_risk.level == "high"
    assert _post_detail_decision(listing, evaluation).route == "review"


@pytest.mark.parametrize("alteration", ["unbound", "other_item", "different_image", "gallery_observed", "missing_image"])
def test_one_image_without_matching_primary_proof_does_not_relax_review(alteration):
    listing = _primary_image_listing("Chrome Hearts keeper floral cross 925 silver ring")
    if alteration == "unbound":
        listing.raw["stage_b"]["observed"]["raw"].pop("association_version")
    elif alteration == "other_item":
        listing.raw["stage_b"]["observed"]["source_listing_id"] = "b222222222"
    elif alteration == "different_image":
        listing.image_url = "https://example.test/unrelated.jpg"
    elif alteration == "gallery_observed":
        listing.raw["detail_photo_coverage"] = "gallery_observed"
    else:
        listing.image_url = None
    assert _post_detail_decision(listing, evaluate_listing(listing)).route is None


@pytest.mark.parametrize("title", [
    "Chrome Hearts 925 silver braided bracelet",
    "Chrome Hearts replica keeper floral cross 925 silver ring",
    "keeper floral cross S925 silver COATING ring クロムハーツ好きに",
])
def test_primary_image_never_rescues_generic_fake_or_audience_reference(title):
    listing = _primary_image_listing(title)
    assert _post_detail_decision(listing, evaluate_listing(listing)).route is None


def test_collection_word_not_observed_motif_but_explicit_graphic_survives():
    plain = evaluate_listing(item("Oakley SKULL COLLECTION FOA406415-100 jacket"))
    graphic = evaluate_listing(item("Oakley skull graphic quilted jacket"))
    assert "skull" not in plain.directional.piece_signals
    assert "skull" in graphic.directional.piece_signals
    assert graphic.profile.item_type.value == "jacket"


def test_woman_in_artwork_name_not_audience_and_explicit_womens_still_rejected():
    assert not enrich_title("Hysteric Glamour WOMAN SKULL HILLS work shirt").product_scope.hard_non_target
    assert enrich_title("Hysteric Glamour women's work shirt").product_scope.hard_non_target


def test_diffusion_and_unproven_designer_era_do_not_inherit_context():
    assert enrich_title("D&G Dolce & Gabbana denim jeans").relationship.value == "diffusion"
    assert enrich_title("vintage Celine Homme shirt").designer is None
    assert enrich_title("Yves Saint Laurent vintage leather boots").designer is None
    assert enrich_title("CELINE The Dancing Kid jacket").designer is not None


def test_cross_item_detail_contradiction_defers_even_exceptional_price():
    listing = item("Maison Margiela graphic T-shirt", 1000,
                   stage_b={"status": "failed", "identity_conflict": True})
    evaluation = evaluate_listing(listing)
    assert evaluation.auth_risk.level == "do_not_touch"
    assert _post_detail_decision(listing, evaluation).rejection_reason == "detail_identity_conflict"


@pytest.mark.parametrize("title", ["Stussy x mastermind skull graphic T-shirt",
                                  "Puma x Alexander McQueen layered leather shoes",
                                  "Prada Linea Rossa sandals"])
def test_collaboration_and_fit_interest_not_global_negative(title):
    profile = enrich_title(title)
    assert not profile.product_scope.hard_non_target
    assert not any(s.value.startswith("counterfeit:") for s in profile.negative_signals)


def test_review_counterexamples_keep_garments_jewelry_and_richer_stage_b():
    coat = evaluate_listing(item("Chrome Hearts leather coat", 100000))
    assert not any("plated/coated" in reason for reason in coat.auth_risk.reasons)
    assert enrich_title("Chrome Hearts sterling silver dagger necklace with original hat box").item_type.value == "jewelry"
    assert enrich_title("Margiela knit sweater with matching scarf").item_type.value == "knitwear"
    profile = enrich_title("Number (N)ine 2001SS Time Migration pullover knit sweater",
                           scope_title="Number (N)ine 2001SS Time Migration")
    assert profile.item_type.value == "knitwear"
