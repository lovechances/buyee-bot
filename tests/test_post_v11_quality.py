import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from buyeeye.attention import (
    assess_attention,
    assess_substitute_attention,
    compare_recent_offers,
    offer_comparison_context,
)
from buyeeye.db import save_alert_state
from buyeeye.discord_alerts import build_listing_alert_payload
from buyeeye.evaluation import evaluate_listing
from buyeeye.live_sources import LiveListing
from buyeeye.models import Comp
from buyeeye.pipeline import _post_detail_decision


def item(title, price=3000):
    return LiveListing(source="buyee", source_listing_id="a1234567890",
        url="https://buyee.jp/item/jdirectitems/auction/a1234567890", title_original=title,
        title_normalized=title, brand=None, category="menswear", current_price=price,
        buyout_price=price, starting_price=price, seller_name="seller", condition="Used",
        time_remaining="5 day(s)", raw={"stage_b": {"status": "enriched", "observed": {}}})


def test_common_motif_is_not_a_value_claim_but_actual_low_downside_survives():
    ordinary = item("Maison Margiela AIDS graphic T-shirt S black", 7980)
    evaluation = evaluate_listing(ordinary)
    assert evaluation.directional.thesis is None
    assert _post_detail_decision(ordinary, evaluation).route is None
    cheap = item(ordinary.title_original, 1500)
    assert _post_detail_decision(cheap, evaluate_listing(cheap)).route == "buyouts"
    assert evaluate_listing(cheap).economics.resale_median_jpy is None


@pytest.mark.parametrize("title,price", [
    ("Roen velour rhinestone zip hoodie", 3990),
    ("Dolce&Gabbana leather-trimmed distressed flared denim jeans", 9888),
    ("GU × Undercover distressed denim jeans", 200),
    ("Maison Margiela gradient knit sweater", 4000),
])
def test_positive_price_and_design_contrasts_do_not_need_us_fmv(title, price):
    listing = item(title, price)
    assert _post_detail_decision(listing, evaluate_listing(listing)).route == "buyouts"


def test_true_negative_piece_sales_cannot_become_normal_archive_push():
    listing = item("Undercover 05SS But Beautiful 2 patchwork studded jacket", 100000)
    comps = [Comp("ebay_sold", listing.title_original, p, 1, 10,
                  source_listing_id=f"verified-{i}", price_evidence="realized",
                  match_evidence="verified_piece", evidence_note="synthetic independently verified transaction")
             for i, p in enumerate((20000, 23000, 25000))]
    evaluation = evaluate_listing(listing, comps)
    assert evaluation.economics.comp_confidence == "medium"
    assert _post_detail_decision(listing, evaluation).rejection_reason == "verified_negative_economics"
    better_sales = [replace(c, price_jpy=c.price_jpy*30) for c in comps]
    assert _post_detail_decision(listing, evaluate_listing(listing, better_sales)).route == "buyouts"


def test_dg_card_separates_observed_conversion_from_estimated_total():
    listing = item("Dolce&Gabbana leather-trimmed distressed flared denim jeans size46", 9888)
    listing.raw["stage_b"]["observed"]["raw"] = {
        "labels": [["Buyout Price", "9,888 YEN (Tax 0 yen) (US$67.54)"]]}
    payload = build_listing_alert_payload(listing, "buyouts", "leather trim / flared distressed denim",
                                           40, evaluate_listing(listing))
    text = json.dumps(payload)
    assert "$67.54 item only (Buyee display)" in text
    assert "estimated incl. shipping/fees" in text
    assert "U.S. resale unverified" in text
    listing.buyout_price = listing.current_price = 8888
    text = json.dumps(build_listing_alert_payload(listing, "buyouts", "test", 40, evaluate_listing(listing)))
    assert "$67.54" not in text  # Stale conversion never follows a changed price.


def test_conflicting_size_is_visible_not_a_silent_recommendation():
    listing = item("Balenciaga distressed denim jeans size34", 6800)
    listing.raw["stage_b"]["observed"]["size_original"] = "Size XS"
    text = json.dumps(build_listing_alert_payload(listing, "buyouts", "test", 40, evaluate_listing(listing)))
    assert "title 34 / metadata XS" in text and "conflict" in text


def test_substitutes_are_persisted_delivery_context_not_global_dedupe(tmp_path):
    listing = item("Maison Margiela AIDS graphic T-shirt Sサイズ black")
    evaluation = evaluate_listing(listing)
    context = offer_comparison_context(listing, evaluation)
    assert context is not None
    path = tmp_path / "alerts.db"
    save_alert_state(listing.source, "other-listing", "first", "buyouts", path,
                     offer_context_json=json.dumps(context), delivery="normal")
    ordinary = assess_attention(evaluation, "buyouts")
    assert assess_substitute_attention(listing, evaluation, ordinary, path).delivery == "quiet"
    # Meaningful new size/color/condition/price remains normal; unknown isn't equal.
    for changes in [{"size": "M"}, {"colors": ["red"]}, {"condition": "unworn"},
                    {"price_jpy": 2500}, {"size": None}, {"colors": []}]:
        assert not compare_recent_offers({**context, **changes}, [context])
    expired = {**context, "comparison_expires_at": (datetime.now(UTC)-timedelta(hours=1)).isoformat()}
    assert not compare_recent_offers(context, [expired])
    assert not compare_recent_offers(context, [{**context, "comparison_expires_at": None}])


def test_substitute_model_does_not_erase_sleeve_or_special_edition():
    title = "Maison Margiela AIDS graphic T-shirt Size M black"
    short = item(title + " short sleeve", 2000)
    long = item(title + " long sleeve", 2000)
    short_context = offer_comparison_context(short, evaluate_listing(short))
    long_context = offer_comparison_context(long, evaluate_listing(long))
    assert short_context and long_context
    assert not compare_recent_offers(long_context, [short_context])
    for variant in ("20th anniversary", "reversible", "限定"):
        listing = item(title + " " + variant, 2000)
        assert offer_comparison_context(listing, evaluate_listing(listing)) is None


def test_chrome_hair_hardware_does_not_lose_auth_handling_after_subtype_repair():
    listing = item("Chrome Hearts クロスボール ヘアゴム シルバー925", 8393)
    listing.raw.update(seller_feedback_percent=100, seller_feedback_count=1000,
                       detail_photo_count=10)
    evaluation = evaluate_listing(listing)
    assert evaluation.profile.item_subtype.value == "hair_accessory"
    assert evaluation.auth_risk.level == "high"
    assert _post_detail_decision(listing, evaluation).route in {None, "review"}


def test_collaboration_can_qualify_on_true_exact_economics_not_unverified_asks():
    listing = item("GU × Undercover distressed denim jeans", 30000)
    comps = [Comp("ebay_sold", listing.title_original, price, 1, 10,
                  source_listing_id=f"verified-collab-{i}", price_evidence="realized",
                  match_evidence="verified_piece", evidence_note="synthetic independent exact transaction")
             for i, price in enumerate((150000, 180000, 200000))]
    assert _post_detail_decision(listing, evaluate_listing(listing, comps)).route == "buyouts"
    context = [replace(comp, price_evidence="visible_sold_price") for comp in comps]
    assert _post_detail_decision(listing, evaluate_listing(listing, context)).route is None


def test_decimal_and_sizing_systems_are_not_interchangeable_substitutes():
    title = "Takahiromiyashita TheSoloist. OOFOS flip flop sandals black "
    contexts = []
    for size in ("US8", "US8.5", "Size8"):
        listing = item(title + size, 3000)
        evaluation = evaluate_listing(listing)
        contexts.append(offer_comparison_context(listing, evaluation))
        if size == "US8.5":
            payload = build_listing_alert_payload(listing, "buyouts", "test", 40, evaluation)
            assert "US 8.5" in json.dumps(payload)
    assert all(contexts)
    assert not compare_recent_offers(contexts[1], [contexts[0]])
    assert not compare_recent_offers(contexts[2], [contexts[0]])


def test_weak_but_actionable_buyout_reject_is_not_labeled_immature_bid():
    listing = item("Maison Margiela AIDS graphic T-shirt", 7980)
    listing.current_price = 1000
    evaluation = evaluate_listing(listing)
    assert evaluation.auction_maturity.price_actionable
    assert _post_detail_decision(listing, evaluation).rejection_reason != "immature_auction"
