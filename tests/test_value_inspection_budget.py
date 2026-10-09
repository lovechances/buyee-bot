from dataclasses import replace

import pytest

from buyeeye.discovery import MarketplaceSearchFilters, QuerySpec
from buyeeye.evaluation import evaluate_listing
from buyeeye.live_sources import LiveListing
from buyeeye.stage_a import assess_stage_a, select_stage_a_candidates


def _spec(*, market=False, family="margiela"):
    return QuerySpec(
        query="Maison Margiela",
        family=family,
        provenance="test",
        priority=80,
        query_layer="market_structure" if market else "broad",
        strategy_id="margiela.test" if market else None,
        strategy_type="market_buyout" if market else None,
        search_filters=MarketplaceSearchFilters(
            category_group="mens_fashion",
            category_id="23176",
            min_buyout_price_jpy=300,
            max_buyout_price_jpy=25000,
            buy_now_only=True,
            item_type="auction",
        )
        if market
        else None,
    )


def _assess(
    title="Maison Margiela 05SS striped ringer t-shirt",
    *,
    price=5999,
    bid=None,
    remaining="6 day(s)",
    market=False,
    config=None,
    family="margiela",
    dedupe=None,
):
    listing = LiveListing(
        source="buyee",
        source_listing_id="test-candidate",
        url="https://example.test/item",
        title_original=title,
        title_normalized=title.casefold(),
        brand="Maison Margiela",
        category="menswear",
        current_price=bid if bid is not None else price,
        buyout_price=price,
        starting_price=bid if bid is not None else price,
        time_remaining=remaining,
        seller_name=None,
        condition=None,
        raw={"dedupe": dedupe or {}},
    )
    settings = {"stage_a": config or {}}
    return assess_stage_a(
        listing,
        evaluate_listing(listing, settings=settings),
        _spec(market=market, family=family),
        settings,
    )


def _ranked_candidates():
    cheap = replace(_assess(), listing_id="cheap", inspection_score=90)
    pricey_same_path = replace(
        _assess(price=20000),
        listing_id="pricey-closet",
        inspection_score=110,
        primary_path="semantic_closet_inspection",
        eligible_paths=("semantic_closet_inspection",),
    )
    archive = replace(
        pricey_same_path,
        listing_id="archive",
        inspection_score=150,
        primary_path="semantic_archive",
        eligible_paths=("semantic_archive",),
    )
    return cheap, pricey_same_path, archive


def test_value_inspection_replaces_one_same_path_card_without_extra_fetches():
    cheap, pricey, archive = _ranked_candidates()
    settings = {"stage_a": {"max_candidates_per_query": 2}}
    selected, capped = select_stage_a_candidates([cheap, pricey, archive], _spec(), settings)
    assert [s.listing_id for s in selected] == ["archive", "cheap"]
    assert [s.listing_id for s in capped] == ["pricey-closet"]
    assert cheap.primary_path == "semantic_closet_inspection"


def test_reservation_can_be_disabled_without_changing_eligibility():
    cheap, pricey, archive = _ranked_candidates()
    selected, capped = select_stage_a_candidates(
        [cheap, pricey, archive],
        _spec(),
        {"stage_a": {"max_candidates_per_query": 2, "value_inspection_reserved_slots": 0}},
    )
    assert [s.listing_id for s in selected] == ["archive", "pricey-closet"]
    assert cheap in capped and cheap.eligible


def test_already_selected_cheap_candidate_leaves_normal_rank_selection_unchanged():
    cheap, pricey, archive = _ranked_candidates()
    cheap = replace(cheap, inspection_score=160)
    settings = {"stage_a": {"max_candidates_per_query": 2}}
    reserved = select_stage_a_candidates([cheap, pricey, archive], _spec(), settings)
    normal = select_stage_a_candidates(
        [cheap, pricey, archive],
        _spec(),
        {"stage_a": {"max_candidates_per_query": 2, "value_inspection_reserved_slots": 0}},
    )
    assert reserved == normal


def test_disabled_path_cannot_be_resurrected_by_value_reservation():
    cheap, pricey, archive = _ranked_candidates()
    selected, capped = select_stage_a_candidates(
        [cheap, pricey, archive],
        _spec(),
        {"stage_a": {"path_caps": {"semantic_closet_inspection": 0}}},
    )
    assert selected == [archive]
    assert cheap in capped and pricey in capped


def test_market_rate_cap_and_single_reservation_bound_are_preserved():
    cheap = replace(_assess(market=True), listing_id="cheap", inspection_score=70)
    expensive = [
        replace(
            _assess(price=20000, market=True), listing_id=f"expensive-{n}", inspection_score=100 + n
        )
        for n in range(5)
    ]
    selected, capped = select_stage_a_candidates(
        [*expensive, cheap],
        _spec(market=True),
        {"stage_a": {"max_market_candidate_rate": 0.03, "value_inspection_reserved_slots": 99}},
    )
    assert selected == [cheap]
    assert len(capped) == 5


@pytest.mark.parametrize(
    "title",
    [
        "Maison Margiela 05SS basic plain t-shirt",
        "Maison Margiela 05SS striped wallet",
        "Maison Margiela 05SS silver ring",
        "Maison Margiela unknown model",
        "Maison Margiela replica printed t-shirt",
        "Chrome Hearts silver graphic t-shirt",
        "Rick Owens printed tee Maison Margiela style",
    ],
)
def test_generic_incomplete_sensitive_or_neighbor_cards_do_not_reserve(title):
    assert not _assess(title).value_inspection_candidate


def test_early_current_bid_cannot_reserve_but_actual_buyout_can():
    early = _assess(price=None, bid=1000)
    buyout = _assess(price=4000, bid=1000)
    mature = _assess(price=None, bid=1000, remaining="45 minute(s)")
    assert not early.value_inspection_candidate
    assert buyout.value_inspection_candidate
    assert mature.value_inspection_candidate


def test_value_inspection_price_configuration_is_only_a_priority_boundary():
    default = _assess(price=10001)
    configured = _assess(price=10001, config={"value_inspection_max_actionable_jpy": 11000})
    assert default.eligible and configured.eligible
    assert not default.value_inspection_candidate
    assert configured.value_inspection_candidate
    assert default.eligible_paths == configured.eligible_paths
    assert default.inspection_score == configured.inspection_score


def test_cheap_footwear_can_reserve_without_an_accessory_slot():
    boots = _assess("Maison Margiela leather split toe boots", price=9000)
    assert boots.item_type == "footwear"
    assert boots.value_inspection_candidate


def test_plain_business_shoes_cannot_take_the_reserved_clothing_slot():
    plain = _assess("Saint Laurent leather dress shoes", price=6000, family="saint_laurent")
    distinctive = _assess(
        "Saint Laurent western harness chelsea boots", price=6000, family="saint_laurent"
    )
    assert plain.eligible and distinctive.eligible
    assert plain.item_type == distinctive.item_type == "footwear"
    assert not plain.value_inspection_candidate
    assert distinctive.value_inspection_candidate


def _coverage_cards():
    base = _assess(market=True)
    return [
        replace(base, listing_id="known-a", inspection_score=98, inspection_attempted=True),
        replace(base, listing_id="known-failed", inspection_score=96, inspection_attempted=True),
        replace(base, listing_id="new-higher", inspection_score=92, inspection_attempted=False),
        replace(base, listing_id="new-lower", inspection_score=86, inspection_attempted=False),
    ]


def _coverage_settings(**updates):
    return {"stage_a": {"max_candidates_per_query": 2, "max_market_candidate_rate": 1, **updates}}


def test_existing_value_slot_rotates_to_highest_ranked_uninspected_card():
    cards = _coverage_cards()
    selected, capped = select_stage_a_candidates(cards, _spec(market=True), _coverage_settings())
    assert [item.listing_id for item in selected] == ["known-a", "new-higher"]
    assert {item.listing_id for item in capped} == {"known-failed", "new-lower"}
    assert len(selected) == 2  # No increased fetch budget or benchmark-ID special case.


def test_repeatedly_capped_candidate_can_displace_fresh_uninspected_card():
    fresh = replace(_assess(market=True), listing_id="fresh", inspection_score=100,
                    inspection_attempted=False)
    waiting = replace(fresh, listing_id="waiting", inspection_score=78,
                      uninspected_age_hours=30)
    selected, capped = select_stage_a_candidates([fresh, waiting], _spec(market=True),
        {"stage_a": {"max_candidates_per_query": 1, "max_market_candidate_rate": 1}})
    assert selected == [waiting]
    assert capped == [fresh]
    # No expanded budget, instant rotation, inspection of hard rejects, or ID rule.
    waiting = replace(waiting, uninspected_age_hours=1)
    assert select_stage_a_candidates([fresh, waiting], _spec(market=True),
        {"stage_a": {"max_candidates_per_query": 1}})[0] == [fresh]


def test_waiting_candidate_does_not_steal_an_overdue_detail_retry():
    retry = replace(_assess(), listing_id="retry", inspection_score=100,
                    inspection_attempted=True, detail_retry_overdue=True)
    waiting = replace(retry, listing_id="waiting", inspection_score=78,
                      inspection_attempted=False, detail_retry_overdue=False,
                      uninspected_age_hours=100)
    assert select_stage_a_candidates([retry, waiting], _spec(),
        {"stage_a": {"max_candidates_per_query": 1}})[0] == [retry]


def test_eligible_hat_can_share_existing_value_inspection_reservation():
    hat = _assess("Vetements embroidered baseball cap", price=5000, family="vetements")
    assert hat.eligible and hat.value_inspection_candidate


def test_rotation_does_not_reward_repeated_low_rank_before_higher_uninspected_cards():
    cards = _coverage_cards()
    first, _ = select_stage_a_candidates(cards, _spec(market=True), _coverage_settings())
    attempted = {item.listing_id for item in first}
    next_cards = [
        replace(item, inspection_attempted=True) if item.listing_id in attempted else item
        for item in cards
    ]
    second, _ = select_stage_a_candidates(next_cards, _spec(market=True), _coverage_settings())
    assert [item.listing_id for item in second] == ["known-a", "new-lower"]


def test_failed_inspection_can_still_win_the_ordinary_slot():
    cards = _coverage_cards()
    cards[1] = replace(cards[1], inspection_score=110)
    selected, _ = select_stage_a_candidates(cards, _spec(market=True), _coverage_settings())
    assert [item.listing_id for item in selected] == ["known-failed", "new-higher"]
    # Attempted is not successful, ineligible, or an authentication verdict.
    assert selected[0].eligible


def test_coverage_priority_does_nothing_when_an_uninspected_value_card_already_wins():
    cards = _coverage_cards()
    cards[0] = replace(cards[0], inspection_attempted=False)
    selected, _ = select_stage_a_candidates(cards, _spec(market=True), _coverage_settings())
    assert [item.listing_id for item in selected] == ["known-a", "known-failed"]


def test_unknown_history_is_not_a_proven_repeat_and_does_not_force_replacement():
    cards = [replace(item, inspection_attempted=None) if item.inspection_attempted else item
             for item in _coverage_cards()]
    selected, _ = select_stage_a_candidates(cards, _spec(market=True), _coverage_settings())
    assert [item.listing_id for item in selected] == ["known-a", "known-failed"]


@pytest.mark.parametrize("disable", [
    {"value_inspection_reserved_slots": 0},
    {"path_caps": {"market_buyout_inspection": 0}},
])
def test_coverage_priority_obeys_disabled_reserve_and_paths(disable):
    selected, _ = select_stage_a_candidates(
        _coverage_cards(), _spec(market=True), _coverage_settings(**disable)
    )
    assert not any(item.listing_id.startswith("new-") for item in selected)


def test_coverage_priority_preserves_market_rate_and_ignores_ineligible_cards():
    cards = _coverage_cards()
    cards[2] = replace(cards[2], eligible_paths=(), hard_veto=True)
    selected, _ = select_stage_a_candidates(
        cards, _spec(market=True), _coverage_settings(max_market_candidate_rate=0.03)
    )
    assert [item.listing_id for item in selected] == ["new-lower"]


def test_coverage_priority_replaces_within_a_saturated_path():
    cards = _coverage_cards()
    cards[0] = replace(cards[0], primary_path="semantic_archive")
    selected, _ = select_stage_a_candidates(
        cards, _spec(market=True),
        _coverage_settings(path_caps={"market_buyout_inspection": 1}),
    )
    assert [item.listing_id for item in selected] == ["known-a", "new-higher"]


def test_inspection_metadata_does_not_change_eligibility_heat_or_priority():
    unknown = _assess()
    unseen = _assess(dedupe={"inspection_attempted": False})
    attempted = _assess(dedupe={"inspection_attempted": True})
    assert unknown.inspection_attempted is None
    assert unseen.observation(selected=False)["inspection_attempted"] is False
    assert attempted.observation(selected=False)["inspection_attempted"] is True
    assert unknown.eligible_paths == unseen.eligible_paths == attempted.eligible_paths
    assert unknown.inspection_score == unseen.inspection_score == attempted.inspection_score


@pytest.mark.parametrize("buyout", [None, 40_000])
def test_low_bid_does_not_stand_in_for_missing_or_over_band_buyout(buyout):
    candidate = _assess(price=buyout, bid=1000, market=True)
    assert "market_buyout_inspection" not in candidate.eligible_paths
    assert "unknown_interesting" not in candidate.eligible_paths
    assert not candidate.value_inspection_candidate
    if buyout is None:
        assert candidate.discovery_price_fraction is None
        assert candidate.actionable_buyout_fraction is None


@pytest.mark.parametrize("cheap_retry", [True, False])
def test_one_slot_overdue_failed_retry_cannot_be_stolen_by_either_reservation(cheap_retry):
    cards = _coverage_cards()
    retry = replace(
        cards[0], detail_retry_overdue=True, value_inspection_candidate=cheap_retry
    )
    selected, _ = select_stage_a_candidates(
        [retry, cards[2]], _spec(market=True),
        _coverage_settings(max_candidates_per_query=1),
    )
    assert selected == [retry]


def test_recent_failed_attempt_can_yield_one_slot_but_is_not_ineligible():
    cards = _coverage_cards()
    retry = replace(cards[0], detail_retry_overdue=False)
    selected, capped = select_stage_a_candidates(
        [retry, cards[2]], _spec(market=True),
        _coverage_settings(max_candidates_per_query=1),
    )
    assert selected == [cards[2]]
    assert retry in capped and retry.eligible


def test_overdue_retry_metadata_is_explicit_and_does_not_change_eligibility():
    regular = _assess()
    retry = _assess(dedupe={"inspection_attempted": True, "detail_retry_overdue": True})
    assert retry.observation(selected=True)["detail_retry_overdue"] is True
    assert retry.eligible_paths == regular.eligible_paths
    assert retry.inspection_score == regular.inspection_score


def test_low_rank_failed_gu_inspection_can_use_existing_reservation():
    # Retained October 9 card context: 171 sightings, one September 17 failed
    # detail attempt, no successful detail, still actionable at 1,990 JPY.
    retry = replace(
        _assess(
            "GU x UNDERCOVER Denim Pants Jeans Zip Pocket Collaboration Men's Gray S",
            price=1990, bid=1890, market=True, family="undercover",
            dedupe={"inspection_attempted": True, "detail_retry_overdue": True,
                    "detail_retry_age_hours": 527},
        ),
        listing_id="failed-collaboration", inspection_score=66.2,
    )
    fresh = replace(
        _assess(market=True), listing_id="fresh", inspection_attempted=False,
        inspection_score=100,
    )
    assert retry.eligible and retry.value_inspection_candidate
    selected, capped = select_stage_a_candidates(
        [fresh, retry], _spec(market=True, family="undercover"),
        _coverage_settings(max_candidates_per_query=1),
    )
    assert selected == [retry] and capped == [fresh]
    assert retry.observation(selected=True)["detail_retry_age_hours"] == 527
    # A failed retry resets the existing retry cooldown; it cannot reserve on
    # every encounter, while successful enrichment also removes retry priority.
    for after_attempt in (
        replace(retry, detail_retry_overdue=False, detail_retry_age_hours=0),
        replace(retry, detail_retry_overdue=False, detail_retry_age_hours=0,
                inspection_score=65),
    ):
        assert select_stage_a_candidates(
            [fresh, after_attempt], _spec(market=True),
            _coverage_settings(max_candidates_per_query=1),
        )[0] == [fresh]


def test_overdue_retries_and_never_attempted_candidates_share_wait_order():
    fresh = replace(_assess(market=True), listing_id="fresh", inspection_score=110,
                    inspection_attempted=False)
    waiting = replace(fresh, listing_id="waiting", inspection_score=90,
                      uninspected_age_hours=100)
    retry = replace(fresh, listing_id="retry", inspection_score=70,
                    inspection_attempted=True, detail_retry_overdue=True,
                    detail_retry_age_hours=200)
    settings = _coverage_settings(max_candidates_per_query=1)
    assert select_stage_a_candidates([fresh, waiting, retry], _spec(market=True), settings)[0] == [retry]
    waiting = replace(waiting, uninspected_age_hours=300)
    assert select_stage_a_candidates([fresh, waiting, retry], _spec(market=True), settings)[0] == [waiting]


@pytest.mark.parametrize("change", [
    {"eligible_paths": (), "hard_veto": True},
    {"value_inspection_candidate": False},
    {"inspection_attempted": False},
    {"detail_retry_overdue": False},
])
def test_retry_priority_requires_eligible_value_and_confirmed_overdue_failure(change):
    fresh = replace(_assess(market=True), listing_id="fresh", inspection_score=100,
                    inspection_attempted=False)
    retry = replace(fresh, listing_id="retry", inspection_score=66,
                    inspection_attempted=True, detail_retry_overdue=True,
                    detail_retry_age_hours=527)
    retry = replace(retry, **change)
    assert select_stage_a_candidates(
        [fresh, retry], _spec(market=True), _coverage_settings(max_candidates_per_query=1),
    )[0] == [fresh]


def test_promoted_retry_obeys_disabled_path_reservation_and_market_rate():
    fresh = replace(_assess(market=True), listing_id="fresh", inspection_score=100,
                    inspection_attempted=False)
    retry = replace(fresh, listing_id="retry", inspection_score=66,
                    inspection_attempted=True, detail_retry_overdue=True,
                    detail_retry_age_hours=527)
    for config in (
        {"value_inspection_reserved_slots": 0},
        {"path_caps": {"market_buyout_inspection": 0}},
    ):
        selected, _ = select_stage_a_candidates(
            [fresh, retry], _spec(market=True),
            _coverage_settings(max_candidates_per_query=1, **config),
        )
        assert retry not in selected
    extra = [replace(fresh, listing_id=f"ordinary-{i}") for i in range(50)]
    selected, capped = select_stage_a_candidates(
        [fresh, retry, *extra], _spec(market=True),
        _coverage_settings(max_market_candidate_rate=0.03),
    )
    assert selected == [retry] and len(capped) == 51
