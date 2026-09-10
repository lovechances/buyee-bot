from __future__ import annotations

from datetime import UTC, datetime, timedelta

from buyeeye.db import (
    AlertState,
    backfill_alert_material_state,
    get_alert_state,
    save_alert_state,
)
from buyeeye.live_sources import LiveListing
from buyeeye.pipeline import AlertMaterialSnapshot, _alert_event_decision, _signature

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _listing(**changes) -> LiveListing:
    values = {
        "source": "buyee",
        "source_listing_id": "same-marketplace-id",
        "url": "https://buyee.jp/item/same-marketplace-id",
        "title_original": "Number (N)ine 05AW High Street turtleneck cutsew",
        "title_normalized": "number nine high street turtleneck cutsew",
        "brand": "Number (N)ine",
        "category": "menswear",
        "current_price": 8_000,
        "buyout_price": None,
        "starting_price": 1_000,
        "seller_name": "seller",
        "condition": "Used",
        "image_url": "https://img.example/one.jpg",
        "time_remaining": "6 hour(s)",
        "bid_count": 2,
        "watch_count": 9,
        "opportunity_score": 90,
        "raw": {"discovery_strategy": "semantic_broad"},
    }
    values.update(changes)
    return LiveListing(**values)


def _snapshot(**changes) -> AlertMaterialSnapshot:
    values = {
        "route": "grails",
        "thesis": "asymmetric_value",
        "price_jpy": 8_000,
        "price_kind": "current_bid",
        "maturity_state": "mature",
        "identity": "number (n)ine|core|t_shirt|high street|-|",
        "identity_confidence": "high",
        "risk_state": "low",
        "evidence_state": "us_resale_unverified",
        "verified_median_jpy": None,
    }
    values.update(changes)
    return AlertMaterialSnapshot(**values)


def _state(**changes) -> AlertState:
    values = {
        "source": "buyee",
        "source_listing_id": "same-marketplace-id",
        "signature": "legacy-display-hash",
        "route": "grails",
        "thesis": "asymmetric_value",
        "material_state_version": 1,
        "last_material_price_jpy": 8_000,
        "last_material_price_kind": "current_bid",
        "last_material_maturity_state": "mature",
        "last_material_identity": "number (n)ine|core|t_shirt|high street|-|",
        "last_material_risk_state": "low",
        "last_material_evidence_state": "us_resale_unverified",
        "last_verified_median_jpy": None,
        "last_alert_event": "initial_alert",
        "last_alert_at": NOW - timedelta(hours=1),
    }
    values.update(changes)
    return AlertState(**values)


def test_countdown_image_watch_and_score_drift_do_not_change_observation_signature() -> None:
    first = _listing(time_remaining="6 hour(s)")
    drifted = _listing(
        time_remaining="5 hour(s)",
        image_url="https://img.example/two.jpg",
        watch_count=99,
        seller_name="seller renamed",
        opportunity_score=91,
        auth_risk="medium",
        comp_confidence="high",
        raw={"discovery_strategy": "market_buyout"},
    )

    assert _signature(first) == _signature(drifted)


def test_observation_signature_changes_once_at_ending_soon_boundary() -> None:
    assert _signature(_listing(time_remaining="2 hour(s)")) != _signature(
        _listing(time_remaining="45 minute(s)")
    )


def test_same_material_listing_and_route_drift_do_not_realert() -> None:
    unchanged = _alert_event_decision(_state(), _snapshot(), now=NOW)
    route_only = _alert_event_decision(
        _state(), _snapshot(route="buyouts"), now=NOW
    )

    assert unchanged.should_alert is False
    assert unchanged.event == "unchanged_material_state"
    assert route_only.should_alert is False


def test_known_type_normalization_change_is_not_a_new_listing_event():
    previous = _state(last_alert_at=NOW - timedelta(days=2),
                      last_material_identity="number (n)ine|core|denim|-|-|")
    current = _snapshot(identity="number (n)ine|core|trousers|-|-|")
    assert not _alert_event_decision(previous, current, now=NOW).should_alert


def test_newly_resolved_identity_evidence_still_can_realert():
    previous = _state(last_alert_at=NOW - timedelta(days=2),
                      last_material_identity="number (n)ine|core|t_shirt|-|-|")
    result = _alert_event_decision(previous, _snapshot(), now=NOW)
    assert result.should_alert
    assert result.event == "identity_materially_changed"


def test_same_fixed_price_listing_unchanged_after_one_hour_does_not_realert() -> None:
    previous = _state(
        route="buyouts",
        last_material_price_kind="buyout",
        last_material_maturity_state="fixed_buyout",
    )
    current = _snapshot(
        route="buyouts",
        price_kind="buyout",
        maturity_state="fixed_buyout",
    )

    decision = _alert_event_decision(previous, current, now=NOW)

    assert decision.should_alert is False
    assert decision.event == "unchanged_material_state"


def test_ending_soon_transition_is_one_explicit_cooldown_bypass() -> None:
    first = _alert_event_decision(
        _state(),
        _snapshot(route="ending", maturity_state="ending"),
        now=NOW,
    )
    repeated = _alert_event_decision(
        _state(last_material_maturity_state="ending", route="ending"),
        _snapshot(route="ending", maturity_state="ending"),
        now=NOW,
    )

    assert first.should_alert is True
    assert first.event == "entered_ending_soon"
    assert repeated.should_alert is False


def test_material_price_drop_respects_same_listing_cooldown() -> None:
    dropped = _snapshot(price_jpy=6_500)
    blocked = _alert_event_decision(_state(), dropped, now=NOW)
    allowed = _alert_event_decision(
        _state(last_alert_at=NOW - timedelta(hours=13)), dropped, now=NOW
    )

    assert blocked.should_alert is False
    assert blocked.event == "cooldown:material_price_drop"
    assert allowed.should_alert is True
    assert allowed.event == "material_price_drop"


def test_new_buyout_is_material_but_still_uses_normal_cooldown() -> None:
    buyout = _snapshot(
        route="buyouts",
        price_jpy=8_000,
        price_kind="buyout",
        maturity_state="fixed_buyout",
    )
    blocked = _alert_event_decision(_state(), buyout, now=NOW)
    allowed = _alert_event_decision(
        _state(last_alert_at=NOW - timedelta(hours=13)), buyout, now=NOW
    )

    assert blocked.should_alert is False
    assert blocked.event == "cooldown:buyout_became_available"
    assert allowed.should_alert is True
    assert allowed.event == "buyout_became_available"


def test_genuine_verified_evidence_change_can_realert_after_cooldown() -> None:
    decision = _alert_event_decision(
        _state(last_alert_at=NOW - timedelta(hours=13)),
        _snapshot(
            thesis="verified_arbitrage",
            evidence_state="verified_us_sold",
            verified_median_jpy=80_000,
        ),
        now=NOW,
    )

    assert decision.should_alert is True
    assert decision.event == "verified_us_evidence_obtained"


def test_legacy_alert_state_is_backfilled_without_resending_or_changing_time(tmp_path) -> None:
    db_path = tmp_path / "legacy-alert.db"
    save_alert_state(
        "buyee", "same-marketplace-id", "old-volatile-hash", "buyouts", db_path
    )
    legacy = get_alert_state("buyee", "same-marketplace-id", db_path)
    assert legacy and legacy.material_state_version is None
    before = legacy.last_alert_at

    decision = _alert_event_decision(legacy, _snapshot(), now=NOW)
    assert decision.should_alert is False
    assert decision.legacy_backfill is True
    assert backfill_alert_material_state(
        "buyee",
        "same-marketplace-id",
        db_path,
        thesis="asymmetric_value",
        material_price_jpy=8_000,
        material_price_kind="current_bid",
        material_maturity_state="mature",
        material_identity="number (n)ine|core|t_shirt|high street|-|",
        material_risk_state="low",
        material_evidence_state="us_resale_unverified",
        verified_median_jpy=None,
    )
    migrated = get_alert_state("buyee", "same-marketplace-id", db_path)
    assert migrated and migrated.material_state_version == 1
    assert migrated.last_alert_event == "legacy_state_backfill"
    assert migrated.last_alert_at == before
