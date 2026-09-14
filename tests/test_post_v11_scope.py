from __future__ import annotations

import json
from pathlib import Path

import pytest

from buyeeye.discovery import QuerySpec
from buyeeye.evaluation import evaluate_listing
from buyeeye.fashion import enrich_title
from buyeeye.live_sources import LiveListing
from buyeeye.pipeline import _post_detail_decision
from buyeeye.stage_a import assess_stage_a

CASES = json.loads((Path(__file__).parent / "fixtures/post_v11_scope.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_frozen_offered_object_and_maker_evidence(case: dict) -> None:
    profile = enrich_title(case["title"], brand_observed=case.get("brand_observed"))
    assert profile.product_scope.offer_kind == case["scope"]
    for name in ("type", "subtype"):
        if name in case:
            evidence = getattr(profile, f"item_{name}")
            assert evidence and evidence.value == case[name]
    if "target_role" in case:
        assert profile.product_scope.target_role == case["target_role"]
    if case.get("brand_unknown"):
        assert profile.brand is None
        assert profile.designer is None
        assert not profile.positive_signals
    if "brand" in case:
        assert profile.brand and profile.brand.value == case["brand"]
        assert not profile.product_scope.hard_non_target


@pytest.mark.parametrize(
    "title",
    (
        "Balenciaga velvet shirt hanger set 3 pieces",
        "Balenciaga ベルベット シャツ用ハンガー 3本セット",
        "Undercover invitation postcard depicting gradient knitwear",
        "Undercover graphic knit sweater postcard invitation flyer",
        "Saint Laurent sunglasses nose pads",
        "Chrome Hearts earring backs",
    ),
)
@pytest.mark.parametrize("remaining", ("6 day(s)", "45 min(s)"))
def test_wrong_objects_cannot_gain_economics_or_secondary_route(title: str, remaining: str) -> None:
    listing = LiveListing(
        source="buyee", source_listing_id="scope-contrast", url="https://buyee.jp/item/scope-contrast",
        title_original=title, title_normalized=title.casefold(), brand=None, category="menswear",
        current_price=200, buyout_price=200, starting_price=200, seller_name="fixture",
        condition="Unused", time_remaining=remaining,
        raw={"stage_b": {"status": "enriched", "observed": {}}, "detail_photo_count": 8},
    )
    evaluation = evaluate_listing(listing)
    assessment = assess_stage_a(
        listing, evaluation, QuerySpec(query="brand", family="balenciaga", provenance="fixture", priority=80), {},
    )
    assert assessment.hard_veto
    assert not assessment.eligible
    assert evaluation.economics.landed_cost_jpy is None
    assert evaluation.directional.thesis is None
    assert _post_detail_decision(listing, evaluation).route is None


@pytest.mark.parametrize(
    "title",
    (
        "Balenciaga velvet shirt with hanger",
        "Number Nine ジャケット ハンガー付き",
        "Number Nine pants with pants hanger",
        "Number Nine パンツ パンツハンガー付き",
        "Balenciaga hanger graphic tee",
        "Saint Laurent ジャケット ハンガー・ガーメント付",
        "Undercover knit sweater with invitation postcard",
        "Undercover postcard print T-shirt",
        "Undercover ニット ポストカード付属",
        "Saint Laurent complete sunglasses with spare nose pads",
        "Saint Laurent サングラス 鼻パッド交換済み",
        "Chrome Hearts complete earrings with earring backs",
        "Chrome Hearts ピアス キャッチ付",
        "Gaboratory ウォレットハンガー キーチェーン",
        "Oakley complete watch with spare links",
        "Undercover DM leather boots",
    ),
)
def test_actual_wearable_inclusions_are_not_component_or_ephemera_offers(title: str) -> None:
    scope = enrich_title(title).product_scope
    assert scope.offer_kind == "product"
    assert scope.acquisition_attributable


def test_generic_label_chain_does_not_gain_brand_from_query_or_longest_alias() -> None:
    title = "Japanese Label fur jacket goa ifsixwasnine lgb share spirit"
    profile = enrich_title(title, hinted_brand="IF SIX WAS NINE")
    assert profile.brand is None
    assert profile.relationship is None
    # Stronger product-bound maker evidence can resolve an otherwise messy title.
    profile = enrich_title(title, brand_observed="IF SIX WAS NINE")
    assert profile.brand and profile.brand.value == "IF SIX WAS NINE"
    assert profile.product_scope.acquisition_attributable
    profile = enrich_title(
        "Needles sheer shirt. Store also stocks Japanese Label ifsixwasnine lgb",
        scope_title="Needles sheer shirt",
    )
    assert profile.brand and profile.brand.value == "Needles"
    profile = enrich_title(title, brand_observed="L.G.B.")
    assert profile.brand and profile.brand.value == "L.G.B."
    assert profile.product_scope.acquisition_attributable


@pytest.mark.parametrize("term", ("08AW PORTLAND", "08AW ポーランド期", "03AW カート期", "2003 Kurt"))
def test_number_nine_season_does_not_imply_a_conflicting_collection(term: str) -> None:
    profile = enrich_title(f"Number Nine {term} cardigan")
    assert profile.collection is None


@pytest.mark.parametrize("name", ("A Closed Feeling", "The High Streets", "Time Migration"))
def test_explicit_collection_name_remains_seller_evidence(name: str) -> None:
    profile = enrich_title(f"Number Nine {name} graphic shirt")
    assert profile.collection and profile.collection.value == name


def test_structured_maker_resolves_actual_multiple_house_title_without_blanket_rewrite() -> None:
    title = "25SS OOFOS x soloist. clog. US8 レオパード リカバリーサンダル ソロイスト アーカイブ ナンバーナイン number nine"
    profile = enrich_title(title, brand_observed="TAKAHIRO MIYASHITA The SoloIst.")
    assert profile.brand and profile.brand.value == "Takahiromiyashita TheSoloist."
    assert profile.relationship and profile.relationship.value == "collaboration"
    assert profile.product_scope.acquisition_attributable
    card = enrich_title(title)
    assert card.brand and card.brand.value == "Takahiromiyashita TheSoloist."
    contradictory = enrich_title("Number Nine graphic tee", brand_observed="Undercover")
    assert contradictory.product_scope.target_role == "contradicted"


def test_gu_waffle_explicit_japanese_collaboration_does_not_become_undercover_mainline() -> None:
    title = "大人気完売★!! GU アンダーカバー ドライワッフルTシャツ (5分袖) UNDERCOVER アンダーカバーコラボ M ホワイト"
    profile = enrich_title(title, brand_observed="ジーユー")
    assert profile.relationship and profile.relationship.value == "collaboration"
    assert profile.relationship.provenance.endswith(".collaboration_low_priority")
    assert profile.relationship.participants == ("gu", "undercover")
    assert profile.product_scope.acquisition_attributable
    reference = enrich_title("GU shirt for Undercover fans", brand_observed="ジーユー")
    assert not reference.product_scope.acquisition_attributable


@pytest.mark.parametrize(
    ("title", "observed", "expected"),
    (
        ("DIOR HOMME ディオールオム 11PJ500A0682 ジャガード ロゴ ストライプ 長袖シャツ", "Christian Dior", "Dior Homme"),
        ("ジュンヤワタナベ コムデギャルソン × リーボック シルバー スニーカー", "COMME des GARCONS", "Junya Watanabe"),
    ),
)
def test_observed_parent_house_preserves_named_specific_title_identity(title: str, observed: str, expected: str) -> None:
    profile = enrich_title(title, brand_observed=observed)
    assert profile.brand and profile.brand.value == expected
    assert profile.product_scope.acquisition_attributable
    unrelated = enrich_title(title, brand_observed="Maison Margiela")
    assert unrelated.product_scope.target_role == "contradicted"


@pytest.mark.parametrize("material", ("faux leather", "fake leather", "synthetic leather", "フェイクレザー", "合成皮革"))
def test_synthetic_material_is_not_genuine_leather_or_counterfeit_maker(material: str) -> None:
    profile = enrich_title(f"Oakley {material} jacket")
    assert profile.material and profile.material.value == "faux_leather"
    assert not any(signal.value.startswith("counterfeit:") for signal in profile.negative_signals)
    replica = enrich_title(f"fake Chrome Hearts {material} jacket")
    assert any(signal.value == "counterfeit:fake" for signal in replica.negative_signals)


def test_observed_hair_tie_offer_is_not_a_silver_bracelet() -> None:
    title = "クロムハーツ ヘアゴム 3個セット baby band ch chrome fat hair mini tiny クロス コンチョ スター ダガー ブレスレット ヘアバンド 新品"
    profile = enrich_title(title, brand_observed="CHROME HEARTS")
    assert profile.item_type and profile.item_type.value == "accessory"
    assert profile.item_subtype and profile.item_subtype.value == "hair_accessory"
    assert profile.material is None
    assert profile.product_scope.acquisition_attributable
    component = enrich_title("Chrome Hearts hair tie silver 925 concho bracelet")
    assert component.material and component.material.value == "silver_component"
    bracelet = enrich_title("Chrome Hearts silver bracelet with matching hair tie")
    assert bracelet.item_type and bracelet.item_type.value == "jewelry"
    assert bracelet.material and bracelet.material.value == "silver"


def test_pumps_are_scoped_without_a_blanket_heel_size_or_brand_ban() -> None:
    pumps = enrich_title("S良品miumiu【ミュウミュウ】リアルパイソン バックストラップ パンプス", category_hierarchy=("Fashion", "Miu Miu", "Shoes"))
    assert pumps.product_scope.audience == "womens"
    for title, hierarchy in (
        ("Prada Linea Rossa sandals 24.0cm メンズ", ("Fashion", "Shoes", "Men's")),
        ("Celine Jacno 60 heeled boots メンズ", ("Fashion", "Shoes")),
        ("Men's runway pumps", ("Fashion", "Shoes", "Men's")),
        ("Miu Miu glitter ankle-strap sandals heel damage size36", ("Fashion", "Miu Miu", "Shoes")),
    ):
        assert enrich_title(title, category_hierarchy=hierarchy).product_scope.audience != "womens"


@pytest.mark.parametrize(
    "title",
    (
        "ジュンヤワタナベ コムデギャルソン × リーボック シルバー スニーカー 26cm",
        "ミュウミュウ シルバーラメグリッター アンクルストラップサンダル",
        "Celine silver Jacno boots",
    ),
)
def test_silver_colored_footwear_is_not_a_precious_material_claim(title: str) -> None:
    profile = enrich_title(title)
    assert profile.material is None
    assert profile.color and profile.color.value == "silver"


def test_silver_color_preserves_independent_material_and_actual_metal_claims() -> None:
    leather = enrich_title("Junya Watanabe silver leather sneakers")
    assert leather.material and leather.material.value == "leather"
    jewelry = enrich_title("Gaboratory sterling silver 925 bracelet")
    assert jewelry.material and jewelry.material.value == "silver"
    hardware = enrich_title("Chrome Hearts hair tie sterling silver 925 concho")
    assert hardware.material and hardware.material.value == "silver_component"
