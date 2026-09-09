from __future__ import annotations

import json
from pathlib import Path

import pytest

from buyeeye.calibration import CalibrationError, run_calibration

ROOT = Path(__file__).resolve().parents[1]
COHORT = ROOT / "config" / "calibration_cohort.yaml"


@pytest.fixture(scope="module")
def replay() -> dict:
    return run_calibration(COHORT)


def _case(replay: dict, key: str) -> dict:
    return next(case for case in replay["cases"] if case["key"] == key)


def test_calibration_replay_is_fixed_deterministic_and_source_attributed(replay) -> None:
    second = run_calibration(COHORT)

    assert replay == second
    assert replay["method"] == "fixed observations -> evaluate_listing -> post-detail routing"
    assert replay["summary"]["cases"] >= 21
    assert replay["summary"]["cohorts"]["positive"] >= 5
    assert replay["summary"]["cohorts"]["negative"] >= 6
    assert replay["summary"]["alignment"].get("misaligned", 0) == 0
    assert replay["summary"]["known_prior_misroutes_now_suppressed"] == 9
    assert replay["summary"]["purchased_now_surfaced"] == 2
    assert all(case["prior_observation"].get("source") for case in replay["cases"])
    # The result remains directly serializable for a CLI/report without custom encoders.
    json.dumps(replay, sort_keys=True)


def test_user_positive_low_downside_pieces_survive_current_routing(replay) -> None:
    keys = {
        "gu_undercover_distressed_denim",
        "vintage_ysl_open_collar_stripe",
        "roen_velour_rhinestone_zip",
        "needles_patterned_shorts",
        "number_nine_fleece_anorak",
    }

    assert replay["summary"]["surfaced_positive"] == len(keys)
    assert {_case(replay, key)["current"]["route"] for key in keys} <= {
        "buyouts",
        "grails",
    }
    gu = _case(replay, "gu_undercover_distressed_denim")["current"]
    assert gu["relationship"] == "collaboration"
    assert gu["relationship_provenance"].endswith(".collaboration_low_priority")
    assert gu["route"] == "buyouts"


def test_semantic_negative_cases_are_suppressed_before_any_alert_route(replay) -> None:
    negative_cases = [case for case in replay["cases"] if case["cohort"] == "negative"]

    assert replay["summary"]["suppressed_negative"] == len(negative_cases)
    assert all(case["current"]["route"] is None for case in negative_cases)

    style = _case(replay, "chrome_hearts_style_ring")["current"]
    film = _case(replay, "devil_wears_prada_avv_tee")["current"]
    packaging = _case(replay, "mcqueen_packaging_only_case")["current"]
    media = _case(replay, "number_nine_collection_dvd")["current"]
    kids = _case(replay, "undercover_kids_polo")["current"]
    partial = _case(replay, "number_nine_partial_sale")["current"]
    margiela_brown = _case(
        replay, "margiela_artisanal_patchwork_trousers_brown"
    )["current"]
    margiela_beige = _case(
        replay, "margiela_artisanal_patchwork_trousers_beige"
    )["current"]

    assert style["product_scope"]["target_role"] in {"style_reference", "contradicted"}
    assert not style["product_scope"]["acquisition_attributable"]
    assert film["product_scope"]["target_role"] == "contradicted"
    assert packaging["product_scope"]["offer_kind"] == "packaging_only"
    assert media["product_scope"]["offer_kind"] == "media_ephemera"
    assert kids["product_scope"]["audience"] == "kids"
    assert partial["product_scope"]["offer_clarity"] != "clear"
    assert margiela_brown["rejection_reason"] == "unverified_high_ticket_not_iconic"
    assert margiela_beige["rejection_reason"] == "unverified_high_ticket_not_iconic"


def test_replay_preserves_auction_maturity_and_verified_comp_semantics(replay) -> None:
    immature = _case(replay, "immature_margiela_auction")["current"]
    mature = _case(replay, "mature_margiela_ending")["current"]
    verified = _case(replay, "verified_time_migration_buyout")["current"]

    assert immature["auction_maturity"] == "low"
    assert immature["current_bid_provisional"] is True
    assert immature["price_actionable"] is False
    assert immature["landed_cost_jpy"] is None
    assert immature["multiple"] is None
    assert immature["route"] is None

    assert mature["auction_maturity"] == "high"
    assert mature["price_actionable"] is True
    assert mature["route"] == "ending"

    assert verified["exact_valuation_confidence"] == "medium"
    assert verified["us_valuation_state"] == "verified_us_sold"
    assert verified["verified_us_median_jpy"] == 45_000
    assert verified["route"] == "buyouts"


def test_invalid_calibration_cohort_is_rejected(tmp_path) -> None:
    empty = tmp_path / "empty.yaml"
    duplicate = tmp_path / "duplicate.yaml"
    empty.write_text("version: 1\ncases: []\n", encoding="utf-8")
    duplicate.write_text(
        "cases:\n  - {key: same, listing: {title_original: one}}\n"
        "  - {key: same, listing: {title_original: two}}\n",
        encoding="utf-8",
    )

    with pytest.raises(CalibrationError, match="non-empty"):
        run_calibration(empty)
    with pytest.raises(CalibrationError, match="unique"):
        run_calibration(duplicate)
