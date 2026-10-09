import json
from pathlib import Path

import pytest

from buyeeye.discovery import QuerySpec
from buyeeye.evaluation import evaluate_listing
from buyeeye.fashion import enrich_title
from buyeeye.live_sources import LiveListing
from buyeeye.pipeline import _post_detail_decision
from buyeeye.stage_a import assess_stage_a

CASES = json.loads((Path(__file__).parent / "fixtures/rescue_scope.json").read_text())


def item(title, price=200, *, observed=None, hint=None, remaining="5 day(s)"):
    return LiveListing(
        source="buyee", source_listing_id="scope-contrast",
        url="https://buyee.jp/item/jdirectitems/auction/scope-contrast",
        title_original=title, title_normalized=title, brand=hint, category="menswear",
        current_price=price, buyout_price=price, starting_price=price,
        seller_name="fixture", condition="Used", time_remaining=remaining,
        raw={"stage_b": {"status": "enriched", "observed": observed or {}},
             "detail_photo_count": 8, "seller_feedback_count": 1000,
             "seller_feedback_percent": 100},
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_retained_rescue_scope_evidence(case):
    profile = enrich_title(case["title"], brand_observed=case.get("brand_observed"))
    assert profile.product_scope.offer_kind == case["expected_offer"]
    if "expected_role" in case:
        assert profile.product_scope.target_role == case["expected_role"]
    if case.get("brand_unknown"):
        assert profile.brand is None
    if "expected_material" in case:
        assert profile.material.value == case["expected_material"]
        listing = item(case["title"], case["price"])
        assert _post_detail_decision(listing, evaluate_listing(listing)).route != "silver"
    else:
        listing = item(case["title"], case["price"])
        evaluation = evaluate_listing(listing)
        assert _post_detail_decision(listing, evaluation).route is None
        assessment = assess_stage_a(
            listing, evaluation,
            QuerySpec(query="brand", family="chrome_hearts", provenance="fixture", priority=80), {},
        )
        assert not assessment.eligible
        if case["expected_offer"] != "product" or case.get("expected_role") == "contradicted":
            assert assessment.hard_veto


@pytest.mark.parametrize("denial", [
    "This item is not Chrome Hearts", "Not made by Chrome Hearts",
    "本品はクロムハーツではありません", "クロムハーツの製品ではない",
])
@pytest.mark.parametrize("in_description", [False, True])
@pytest.mark.parametrize("remaining", ["5 day(s)", "45 min(s)"])
def test_explicit_denial_precedes_all_routes(denial, in_description, remaining):
    title = "Chrome Hearts double dagger sterling silver 925 pendant"
    listing = item(title if in_description else title + ". " + denial,
                   observed={"description_original": denial} if in_description else {},
                   remaining=remaining)
    evaluation = evaluate_listing(listing)
    assert evaluation.profile.product_scope.target_role == "contradicted"
    assert evaluation.economics.landed_cost_jpy is None
    assert _post_detail_decision(listing, evaluation).rejection_reason == "target_identity_mismatch"


@pytest.mark.parametrize("title", [
    "GU × Undercover distressed denim jeans",
    "Stussy × mastermind skull graphic tee",
    "Alexander McQueen × PUMA leather sneakers",
    "Number Nine distressed denim shorts",
    "Dolce&Gabbana leather-trimmed distressed flared denim jeans",
    "Chrome Hearts double dagger pendant. Not made by Gaboratory",
])
def test_unrelated_denial_and_cheap_collaborations_retain_maker_scope(title):
    profile = enrich_title(title)
    assert profile.product_scope.acquisition_attributable
    assert profile.brand


def test_cheap_gu_and_owner_consideration_positive_keep_buyout_routes():
    for title, price in [
        ("GU × Undercover distressed denim jeans", 200),
        ("Dolce&Gabbana leather-trimmed distressed flared denim jeans", 9888),
    ]:
        listing = item(title, price)
        assert _post_detail_decision(listing, evaluate_listing(listing)).route == "buyouts"


@pytest.mark.parametrize("title", [
    "Chrome Hearts complete leather wallet with dagger zipper pull",
    "Chrome Hearts 財布 ファスナーチャーム付き",
    "Number Nine denim jacket riri zipper",
    "Chrome Hearts zipper pull graphic T-shirt",
    "D&G leather shopper bag leopard print",
    "D&G leather shop bag leopard print",
    "D&G shirt with paper bag",
    "D&G シャツ 紙袋付き",
    "Saint Laurent lipstick print T-shirt",
    "Saint Laurent Book graphic T-shirt",
    "Undercover jacket with official book",
    "Undercover motorcycle leather jacket",
    "Undercover engine under cover graphic T-shirt",
])
def test_complete_goods_and_included_accessories_survive(title):
    assert enrich_title(title).product_scope.acquisition_attributable


def test_chrome_wallet_chain_retains_jewelry_auth_risk():
    listing = item("Chrome Hearts double dagger wallet chain silver 925", 8393)
    evaluation = evaluate_listing(listing)
    assert evaluation.profile.item_type.value == "jewelry"
    assert evaluation.profile.product_scope.acquisition_attributable
    assert evaluation.auth_risk.level == "high"
    assert _post_detail_decision(listing, evaluation).route in {None, "review"}


@pytest.mark.parametrize("title,material", [
    ("Number Nine brass silver bracelet", "brass"),
    ("Number Nine 真鍮 シルバー ブレスレット", "brass"),
    ("Number Nine silver-color bracelet", None),
    ("Number Nine シルバーカラー ブレスレット", None),
    ("Number Nine silver plated bracelet", None),
    ("Gaboratory sterling silver 925 bracelet", "silver"),
    ("Gaboratory sterling silver and brass bracelet", "mixed_metal"),
    ("Junya Watanabe silver leather sneakers", "leather"),
])
def test_material_claim_is_separate_from_color(title, material):
    profile = enrich_title(title)
    assert (profile.material.value if profile.material else None) == material
    assert profile.product_scope.acquisition_attributable


@pytest.mark.parametrize("title,kind", [
    ("Roberto Cavalli boot-cut denim", "denim"),
    ("Roberto Cavalli ブーツカット デニム", "denim"),
    ("Roberto Cavalli ブーツカット パンツ", "trousers"),
    ("Roberto Cavalli bootcut jeans", "denim"),
    ("Roberto Cavalli denim boots", "footwear"),
])
def test_bootcut_is_silhouette_not_footwear(title, kind):
    assert enrich_title(title).item_type.value == kind


def test_performer_name_does_not_borrow_house_or_query_manufacturer():
    assert enrich_title("Celine Dion tour tee", hinted_brand="Celine").brand is None
    assert enrich_title("Celine Dion tour tee", brand_observed="Celine Dion").brand is None
    assert enrich_title("CELINE HOMME jacket", brand_observed="CELINE").brand.value == "Celine"
    assert enrich_title("CELINE jacket with Celine Dion graphic").brand.value == "Celine"


@pytest.mark.parametrize("size", ["27.5", "9.5", "42.50", "27", "9", "XL"])
def test_decimal_size_token_is_preserved_without_inventing_unit(size):
    profile = enrich_title(f"Alexander McQueen × Puma leather sneakers Size{size}")
    assert profile.size and profile.size.value == size
