from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from buyeeye.attention import assess_attention
from buyeeye.discord_alerts import build_listing_alert_payload
from buyeeye.evaluation import evaluate_listing
from buyeeye.live_sources import LiveListing
from buyeeye.pipeline import _post_detail_decision
from buyeeye.runtime_state import mark_listing_seen, record_listing_traces


def listing(title: str, price: int = 98_000) -> LiveListing:
    return LiveListing(
        source="buyee", source_listing_id="attention-fixture",
        url="https://buyee.jp/item/jdirectitems/auction/attention-fixture",
        title_original=title, title_normalized=title,
        brand=None, category="menswear", current_price=price, buyout_price=price,
        starting_price=price, condition="Used", time_remaining="5 day(s)", seller_name="seller",
        raw={"stage_b": {"status": "enriched", "observed": {
            "category_hierarchy": ["Fashion", "Men's Fashion"]}},
            "detail_photo_count": 6, "seller_feedback_count": 300},
    )


def test_expensive_collection_pattern_keeps_archive_route_but_quiet_delivery():
    item = listing("セリーヌ 2021SS ニット CELINE THE DANCING KID KNIT HOODIE 総柄 パーカー")
    item.condition = "Close to unused"
    evaluation = evaluate_listing(item)
    route = _post_detail_decision(item, evaluation).route
    assert route == "grails"
    assert evaluation.directional.thesis is None
    decision = assess_attention(evaluation, route)
    assert decision.delivery == "quiet"
    item.raw["diagnostic"] = {"attention": decision.observation()}
    card = build_listing_alert_payload(item, route, "named collection / all-over pattern", 62, evaluation)
    assert card["flags"] == 4096
    assert "ARCHIVE DISCOVERY · QUIET" in card["embeds"][0]["description"]
    assert "U.S. resale unverified" in str(card)
    assert "est." not in str(card)


def test_attention_toggle_does_not_change_route_or_valuation():
    item = listing("セリーヌ 2021SS ニット CELINE THE DANCING KID KNIT HOODIE 総柄 パーカー")
    evaluation = evaluate_listing(item)
    before = _post_detail_decision(item, evaluation)
    assert assess_attention(evaluation, "grails", {"alert": {
        "quiet_unverified_high_ticket_archive": False}}).delivery == "normal"
    assert _post_detail_decision(item, evaluation) == before
    assert evaluation.comps.median_jpy is None


@pytest.mark.parametrize("route", ["buyouts", "ending", "silver", "review"])
def test_primary_actionable_and_review_routes_never_quieted(route):
    evaluation = evaluate_listing(listing("CELINE The Dancing Kid 総柄 パーカー"))
    assert assess_attention(evaluation, route).delivery == "normal"


def test_cheap_specific_tee_can_alert_without_fmv_or_archive_name():
    item = listing("Maison Margiela graphic T-shirt", 1_000)
    evaluation = evaluate_listing(item)
    route = _post_detail_decision(item, evaluation).route
    assert route == "buyouts"
    assert assess_attention(evaluation, route).delivery == "normal"
    assert evaluation.directional.micro_asymmetry
    assert evaluation.comps.median_jpy is None


def test_named_ordinary_piece_is_not_rescued_by_attention():
    item = listing("CELINE The Dancing Kid cotton plain shirt")
    evaluation = evaluate_listing(item)
    assert _post_detail_decision(item, evaluation).route is None


def test_two_independent_designs_preserve_high_cost_grail_push():
    item = listing("CELINE The Dancing Kid patchwork studded jacket")
    evaluation = evaluate_listing(item)
    assert assess_attention(evaluation, "grails").delivery == "normal"


def test_bilingual_design_aliases_do_not_manufacture_independent_evidence():
    item = listing("CELINE The Dancing Kid 総柄 all-over all over hoodie")
    evaluation = evaluate_listing(item)
    assert assess_attention(evaluation, "grails").delivery == "quiet"


def test_credible_economics_retains_normal_attention_without_new_fmv():
    item = listing("CELINE The Dancing Kid 総柄 hoodie")
    evaluation = evaluate_listing(item)
    evaluation = replace(evaluation, economics=replace(
        evaluation.economics, opportunity_score=90, comp_confidence="high"))
    assert assess_attention(evaluation, "grails").delivery == "normal"


def test_early_bid_cannot_become_attention_value():
    item = listing("Maison Margiela graphic T-shirt", 1_000)
    item.buyout_price = None
    evaluation = evaluate_listing(item)
    assert evaluation.auction_maturity.current_bid_provisional
    assert evaluation.economics.multiple is None
    assert _post_detail_decision(item, evaluation).route is None


def test_attention_survives_bounded_fallback_and_later_revisit(tmp_path):
    db_path = tmp_path / "attention.db"
    mark_listing_seen("buyee", "archive", "same-material", "Celine", db_path)
    record_listing_traces({("buyee", "archive"): {
        "stage_a": {"selected": True, "value_inspection_candidate": True},
        "detail_status": "enriched", "route": "grails",
        "attention": {"role": "archive_discovery", "delivery": "quiet"},
        "alert_outcome": "sent", "alert_event": "initial_alert",
        "unexpected_large_payload": "x" * 9000,
    }}, db_path)
    with sqlite3.connect(db_path) as connection:
        encoded, history = connection.execute(
            "select latest_trace_json,event_history_json from seenlisting"
        ).fetchone()
    assert len(encoded.encode()) <= 3000
    assert json.loads(encoded)["attention"]["delivery"] == "quiet"
    assert json.loads(encoded)["stage_a"]["value_inspection_candidate"] is True
    assert json.loads(history)[-1]["attention_role"] == "archive_discovery"
    assert json.loads(history)[-1]["alert_outcome"] == "sent"
    record_listing_traces({("buyee", "archive"): {
        "stage_a": {"selected": False}, "decision": "stage_a:candidate_budget",
    }}, db_path)
    with sqlite3.connect(db_path) as connection:
        history = connection.execute("select event_history_json from seenlisting").fetchone()[0]
    assert json.loads(history)[-1]["attention_delivery"] == "quiet"
