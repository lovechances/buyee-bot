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

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "product_completeness.json").read_text()
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_offer_completeness_from_title_and_structured_category(case: dict) -> None:
    profile = enrich_title(case["title"], category_hierarchy=tuple(case["categories"]))
    scope = profile.product_scope
    expected = case["expected"]
    if expected in {"component_only", "protective_only"}:
        assert scope.product_completeness == expected
        assert scope.offer_kind == "non_fashion"
    else:
        assert scope.offer_kind == expected
    assert scope.acquisition_attributable is (expected == "product")


@pytest.mark.parametrize(
    "case", [case for case in CASES if case["expected"] != "product"],
    ids=lambda case: case["name"],
)
def test_incomplete_fashion_offer_never_earns_value_or_detail_budget(case: dict) -> None:
    listing = LiveListing(
        source="buyee", source_listing_id="scope-fixture",
        url="https://buyee.jp/item/scope-fixture", title_original=case["title"],
        title_normalized=case["title"].casefold(), brand=None, category="accessory",
        current_price=1_000, buyout_price=1_000, starting_price=1_000,
        seller_name="fixture", condition="Unused", time_remaining="7 day(s)",
        raw={"stage_b": {"status": "enriched", "observed": {
            "category_hierarchy": case["categories"],
        }}, "detail_photo_count": 6, "seller_feedback_count": 200},
    )
    evaluation = evaluate_listing(listing)
    assessment = assess_stage_a(
        listing, evaluation,
        QuerySpec(query="brand", family="oakley", provenance="test", priority=80),
        {},
    )
    assert not assessment.eligible
    assert assessment.hard_veto
    assert evaluation.directional.thesis is None
    assert evaluation.economics.landed_cost_jpy is None
    assert _post_detail_decision(listing, evaluation).route is None


def test_case_in_description_does_not_replace_complete_title_offer() -> None:
    profile = enrich_title(
        "Oakley sunglasses; supplied with original sunglasses case and spare lens",
        scope_title="Oakley Square Wire complete sunglasses",
        category_hierarchy=("Fashion", "Sunglasses"),
    )
    assert profile.product_scope.acquisition_attributable
    assert profile.product_scope.product_completeness == "unspecified"


@pytest.mark.parametrize(
    ("title", "categories", "expected"),
    (
        ("Prada Banana FlamesTシャツ", (), "t_shirt"),
        ("Comme des Garcons ヘムボーダーTシャツ", (), "t_shirt"),
        ("Number (N)ine long sleeve shirt", ("Fashion", "T-Shirts", "Other Sleeves"), "t_shirt"),
        ("Prada white dress shirt", ("Fashion", "Shirts"), "shirt"),
        ("Maison Margiela STEREOTYPE shirt", ("Fashion", "Shirts"), "shirt"),
        ("Prada pinstripe shirt jacket", ("Fashion", "Jackets"), "jacket"),
    ),
)
def test_mixed_script_tee_and_explicit_category_preserve_real_shirt_types(
    title: str, categories: tuple[str, ...], expected: str,
) -> None:
    profile = enrich_title(title, category_hierarchy=categories)
    assert profile.item_type and profile.item_type.value == expected
