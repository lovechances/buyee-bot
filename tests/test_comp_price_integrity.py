from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from buyeeye.comp_cache import CompRepository
from buyeeye.comp_sources import ObservedUSSoldSource, YahooEndedSource
from buyeeye.evaluation import evaluate_comps, evaluate_economics
from buyeeye.fashion import enrich_title
from buyeeye.models import Comp
from buyeeye.settings import load_settings
from buyeeye.valuation import estimate_landed_cost_jpy, listing_price_usd


def transaction(title: str, listing_id: str = "sold-1", **kwargs) -> Comp:
    """Synthetic verified transaction, not a claim about a production record."""
    return Comp("local_sold", title, 40_000, 1.0, 30,
                source_listing_id=listing_id, price_evidence="realized", **kwargs)


def test_frozen_dg_observation_separates_actual_price_from_old_cost_scenario():
    fixture = json.loads((Path(__file__).parent / "fixtures/post_v10_price_evidence.json").read_text())
    price = fixture["listing"]["buyout_price"]
    old = fixture["legacy_pricing"]
    # Historical implementation omitted its configured domestic shipping.
    original_total = price * 1.26 + 350 + 1500 + 300
    assert round(original_total * old["fx_jpy_to_usd"], 2) == 98.61
    assert listing_price_usd(price, {"pricing": old}) == 66.74
    assert "US$67.54" in fixture["listing"]["raw_price_label"]
    assert fixture["listing"]["user_outcome"] == "consideration_positive_not_purchased"


def test_cost_components_include_domestic_once_and_do_not_duplicate_optional_insurance():
    estimate = estimate_landed_cost_jpy(9888, load_settings())
    names = ("fee_jpy", "plan_jpy", "domestic_shipping_jpy", "shipping_jpy",
             "packaging_jpy", "insurance_jpy", "duty_jpy", "reserve_jpy")
    assert estimate["landed_jpy"] == pytest.approx(9888 + sum(estimate[n] for n in names))
    assert estimate["domestic_shipping_jpy"] == 800
    assert estimate["fee_jpy"] == 500
    assert estimate["insurance_jpy"] == 0
    assert estimate["listing_only_usd"] == 66.74
    assert estimate["estimated_landed_usd"] > estimate["listing_only_usd"]
    assert "not a live quote" in estimate["fx_basis"]
    assert any("optional" in note for note in estimate["assumptions"])
    assert listing_price_usd(None) is None
    assert listing_price_usd(100, {"pricing": {"fx_jpy_to_usd": 0}}) is None


def test_configurable_legacy_fee_override_is_not_added_to_default_fixed_fee():
    estimate = estimate_landed_cost_jpy(10_000, {"pricing": {"buyee_fee_percent": .11}})
    assert estimate["fee_jpy"] == 1100


def test_default_comp_amount_is_unknown_and_cannot_create_fake_multiple():
    title = "Dior Homme Strip distressed wax denim jeans"
    comp = Comp("grailed_sold", title, 80_000, 1, 30)
    result = evaluate_comps(enrich_title(title), [comp], evidence_text=title)
    assert comp.is_sold and comp.price_evidence == "unknown"
    assert result.median_jpy is None and len(result.context_matches) == 1
    economics = evaluate_economics(1000, result)
    assert economics.multiple is None and economics.net_margin_jpy is None
    assert economics.cost_estimate["listing_only_usd"] == 6.75


def test_unknown_accepted_offer_keeps_visible_amount_as_context():
    title = "Maison Margiela AIDS graphic T-shirt"
    comps = [replace(transaction(title, str(i)), price_evidence="visible_sold_price") for i in range(3)]
    result = evaluate_comps(enrich_title(title), comps, evidence_text=title)
    assert result.confidence == "none"
    assert len(result.context_matches) == 3
    assert all(comp.price_jpy == 40_000 for comp in result.context_matches)


def test_realized_specific_piece_transactions_still_support_economics():
    title = "Dior Homme Strip distressed wax denim jeans"
    result = evaluate_comps(enrich_title(title), [transaction(title, str(i)) for i in range(3)], evidence_text=title)
    assert result.confidence == "medium"
    economics = evaluate_economics(1000, result)
    assert economics.multiple and economics.multiple > 4
    assert economics.net_margin_jpy and economics.net_margin_jpy > 0


@pytest.mark.parametrize("target,other", [
    ("Maison Margiela AIDS graphic T-shirt", "Maison Margiela plain cotton T-shirt"),
    ("D&G distressed flared leather insert denim jeans", "D&G skinny plain denim jeans"),
    ("Number (N)ine SS01 Time Migration Montreal bullet T-shirt", "Number (N)ine SS01 Time Migration plain logo T-shirt"),
    ("Number (N)ine SS01 Time Migration Montreal bullet T-shirt", "Number (N)ine SS03 Touch Me Im Sick T-shirt"),
    ("Dior Homme Strip denim shorts", "Dior Homme Strip denim jeans"),
])
def test_brand_season_or_category_alone_cannot_supply_exact_piece_value(target, other):
    result = evaluate_comps(enrich_title(target), [transaction(other, str(i)) for i in range(3)], evidence_text=target)
    assert result.median_jpy is None
    assert result.confidence == "none"


def test_same_model_with_material_or_condition_conflict_is_only_context():
    title = "Number (N)ine A Closed Feeling mohair distressed knit"
    wrong_material = "Number (N)ine A Closed Feeling cotton distressed knit"
    result = evaluate_comps(enrich_title(title), [transaction(wrong_material)], evidence_text=title)
    assert result.median_jpy is None
    worn = evaluate_comps(enrich_title(title), [transaction(title, condition="New")],
                          evidence_text=title, condition="heavily damaged")
    assert worn.median_jpy is None


def test_mirrored_or_duplicate_records_do_not_inflate_transaction_count():
    title = "Dior Homme Strip distressed wax denim jeans"
    comp = transaction(title, stock_identity="verified-shared-stock-1")
    mirror = replace(comp, source="ebay_sold", source_listing_id="different-url")
    result = evaluate_comps(enrich_title(title), [comp, comp, mirror], evidence_text=title)
    assert len(result.matches) == 1
    assert result.confidence == "low"
    uncertain_first = replace(comp, price_evidence="unknown")
    stronger = evaluate_comps(enrich_title(title), [uncertain_first, comp], evidence_text=title)
    assert len(stronger.matches) == 1
    assert stronger.matches[0].price_evidence == "realized"


def test_manual_piece_verification_needs_provenance_and_cannot_override_contradictions():
    title = "Dior Homme patterned silk necktie"
    sale = replace(transaction(title), match_evidence="verified_piece")
    assert evaluate_comps(enrich_title(title), [sale]).median_jpy is None
    sale = replace(sale, evidence_note="Synthetic independently matched exact product")
    assert evaluate_comps(enrich_title(title), [sale]).median_jpy == 40_000
    wrong = replace(sale, title="Dior Homme patterned cotton shirt")
    assert evaluate_comps(enrich_title(title), [wrong]).median_jpy is None


def test_large_ordinary_piece_can_qualify_from_true_verified_economics_not_cheapness():
    title = "Dior Homme patterned silk necktie"
    sales = [replace(transaction(title, str(i)), price_jpy=150_000,
                     match_evidence="verified_piece", evidence_note="Synthetic exact product match")
             for i in range(3)]
    result = evaluate_comps(enrich_title(title), sales)
    assert result.confidence == "medium"
    assert evaluate_economics(30_000, result).opportunity_score >= 70


def test_price_evidence_and_provenance_survive_existing_cache_without_schema_change(tmp_path):
    repository = CompRepository(tmp_path / "cache.db")
    observed = datetime(2026, 9, 13, 12, tzinfo=UTC)
    comp = replace(transaction("Maison Margiela AIDS graphic T-shirt"),
                   price_evidence="visible_sold_price", evidence_observed_at=observed,
                   evidence_note="Unknown accepted offer", stock_identity="known-stock")
    repository.put(comp.source, "query", [comp], observed)
    cached = repository.get_fresh(comp.source, "query", observed)[0]
    assert cached.price_evidence == "visible_sold_price"
    assert cached.evidence_observed_at == observed
    assert cached.evidence_note == "Unknown accepted offer"
    assert cached.stock_identity == "known-stock"
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute("update soldcompcache set matching_features_json='{}'")
    legacy = repository.get_fresh(comp.source, "query", observed)[0]
    assert legacy.price_evidence == "unknown"


def test_existing_grailed_observations_are_preserved_but_not_recertified():
    records = asyncio.run(ObservedUSSoldSource().fetch("Number (N)ine Time Migration t shirt"))
    assert sorted(comp.original_price for comp in records) == [70, 120, 154]
    assert all(comp.is_sold and comp.price_evidence == "visible_sold_price" for comp in records)
    result = evaluate_comps(enrich_title("Number (N)ine SS01 Time Migration Montreal bullet T-shirt"), records)
    assert result.median_jpy is None


def test_yahoo_winning_amount_explicitly_records_realized_evidence():
    html = '<ul><li><a href="https://auctions.yahoo.co.jp/jp/auction/a123">Dior Homme Strip jeans</a><span>落札 54,900 円</span></li></ul>'
    record = YahooEndedSource.parse(html, "Dior", datetime(2026, 9, 13, tzinfo=UTC))[0]
    assert record.price_evidence == "realized"
    assert record.original_currency == "JPY"
    assert record.evidence_observed_at == datetime(2026, 9, 13, tzinfo=UTC)
