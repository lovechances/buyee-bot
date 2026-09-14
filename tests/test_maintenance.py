from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import select

from buyeeye.db import Listing, get_session, save_listings
from buyeeye.discord_alerts import send_listing_alert, send_status_event
from buyeeye.discovery import build_query_universe
from buyeeye.live_sources import LiveListing
from buyeeye.settings import CONFIG_DIR, load_yaml
from buyeeye.status_monitor import (
    AUTH_REQUIRED_MESSAGE,
    OUTAGE_MESSAGE,
    RECOVERY_MESSAGE,
    announce_major_update,
    record_auth_failure,
    record_scanning_success,
    record_source_failure,
)

REMOVED_TARGETS = {
    "Julius",
    "Blackmeans",
    "General Research",
    "20471120",
    "beauty:beast",
    "Neighborhood",
    "Bounty Hunter",
    "Wacko Maria",
    "Ann Demeulemeester",
    "Carol Christian Poell",
    "Marithé François Girbaud",
    "Plein Sud",
    "Gabor",
    "Goro's",
}


def _configs():
    brands = load_yaml(CONFIG_DIR / "brands.yaml")
    searches = load_yaml(CONFIG_DIR / "searches.yaml")
    taxonomy = load_yaml(CONFIG_DIR / "fashion_taxonomy.yaml")
    market = load_yaml(CONFIG_DIR / "market_structure.yaml")
    return brands, searches, taxonomy, market


def _universe():
    _, searches, taxonomy, market = _configs()
    return build_query_universe(searches, taxonomy, market)


def test_removed_targets_generate_no_active_semantic_or_market_strategies() -> None:
    brands, _, _, _ = _configs()
    active = {str(value).casefold() for value in brands["brands"]}
    assert not ({value.casefold() for value in REMOVED_TARGETS} & active)

    queries = [spec.query.casefold() for spec in _universe()]
    for removed in REMOVED_TARGETS:
        term = removed.casefold()
        assert not any(query == term or query.startswith(f"{term} ") for query in queries)


def test_gabor_is_removed_while_gaboratory_remains_active() -> None:
    universe = _universe()

    assert not any(spec.family == "gabor" or spec.query.casefold() == "gabor" for spec in universe)
    assert any(
        spec.family == "gaboratory" and spec.query == "Gaboratory"
        for spec in universe
    )
    assert any(
        spec.family == "gaboratory" and spec.query_layer == "market_structure"
        for spec in universe
    )


def test_target_configuration_has_no_duplicate_or_alias_families() -> None:
    brands, _, _, _ = _configs()
    active = brands["brands"]
    aliases = brands["alias_map"]

    assert len(active) == len(set(active)) == 43
    assert "Alexander McQueen" in active
    assert "Celine" in active
    assert "Christian Louboutin" in active
    assert "Vintage band tees" in active
    assert active.count("Needles") == 1
    assert "Number Nine" not in active
    assert aliases["NUMBER NINE"] == "Number (N)ine"
    assert "Le Grand Bleu" not in active
    assert aliases["Le Grand Bleu"] == "L.G.B."
    assert active.count("Maison Martin Margiela") == 1
    assert aliases["Maison Margiela"] == "Maison Martin Margiela"
    assert aliases["CÉLINE"] == "Celine"
    assert aliases["Louboutin"] == "Christian Louboutin"

    universe = _universe()
    assert {spec.family for spec in universe if spec.query == "Number Nine"} == {
        "number_nine"
    }
    assert {
        spec.family
        for spec in universe
        if spec.query in {"Maison Martin Margiela", "マルタンマルジェラ"}
    } == {"margiela"}
    assert {spec.family for spec in universe if spec.query == "Rebuild"} == {"needles"}


def test_celine_and_louboutin_are_full_menswear_families() -> None:
    universe = _universe()

    celine = [spec for spec in universe if spec.family == "celine"]
    louboutin = [spec for spec in universe if spec.family == "christian_louboutin"]
    assert any(spec.query_layer == "broad" and spec.deep_search for spec in celine)
    assert any(spec.query_layer == "medium" for spec in celine)
    assert any(spec.query_layer == "surgical" for spec in celine)
    assert {
        spec.search_filters.category_group
        for spec in celine
        if spec.query_layer == "market_structure" and spec.search_filters
    } == {"mens_fashion", "mens_shoes"}
    assert any(spec.query_layer == "broad" and spec.deep_search for spec in louboutin)
    assert any(spec.query_layer == "medium" for spec in louboutin)
    assert any(spec.query_layer == "surgical" for spec in louboutin)
    assert {
        spec.search_filters.category_group
        for spec in louboutin
        if spec.query_layer == "market_structure" and spec.search_filters
    } == {"mens_fashion_goods", "mens_shoes"}
    assert not any("women" in spec.query.casefold() for spec in louboutin)


def test_configuration_cleanup_does_not_delete_historical_listings(tmp_path) -> None:
    db_path = tmp_path / "history.db"
    historical = LiveListing(
        source="buyee",
        source_listing_id="historical-julius",
        url="https://buyee.jp/item/historical-julius",
        title_original="Julius historical listing",
        title_normalized="julius historical listing",
        brand="Julius",
        category="menswear",
        current_price=10_000,
        buyout_price=None,
        starting_price=10_000,
        seller_name=None,
        condition="Used",
    )
    save_listings([historical], db_path)

    _universe()

    with get_session(db_path) as session:
        rows = session.exec(select(Listing)).all()
    assert [(row.source_listing_id, row.brand) for row in rows] == [
        ("historical-julius", "Julius")
    ]


def test_major_update_sends_once_and_dedupes_persistently(monkeypatch, tmp_path) -> None:
    messages: list[str] = []
    monkeypatch.setenv("DISCORD_WEBHOOK_STATUS", "https://discord.com/api/webhooks/fixture/token")
    monkeypatch.setattr(
        "buyeeye.status_monitor.send_release_summary",
        lambda message: (messages.append(message) or {
            "state": "sent", "result": "200", "message_id": "123", "channel_id": "456",
        }),
    )
    db_path = tmp_path / "status.db"

    first = announce_major_update("status-monitoring-v1", db_path)
    repeated = announce_major_update("status-monitoring-v1", db_path)

    assert first.emitted is True
    assert repeated.outcome == "deduped"
    assert messages == ["BuyEye update: status monitoring is now live."]


def test_transient_failures_and_routine_health_send_nothing(monkeypatch, tmp_path) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        "buyeeye.status_monitor.send_status_event",
        lambda message, level="info": (messages.append(message) or True, 204, 0.01),
    )
    db_path = tmp_path / "transient.db"

    assert record_source_failure(1, db_path).outcome == "transient_degraded"
    assert record_source_failure(2, db_path).outcome == "transient_degraded"
    assert record_scanning_success(db_path).outcome == "healthy_no_incident"
    assert messages == []


def test_sustained_outage_and_recovery_each_send_once(monkeypatch, tmp_path) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        "buyeeye.status_monitor.send_status_event",
        lambda message, level="info": (messages.append(message) or True, 204, 0.01),
    )
    db_path = tmp_path / "outage.db"
    started = datetime(2026, 9, 8, 12, tzinfo=UTC)

    assert record_source_failure(1, db_path, now=started).outcome == "transient_degraded"
    assert (
        record_source_failure(6, db_path, now=started + timedelta(minutes=10)).outcome
        == "not_prolonged"
    )
    assert record_source_failure(6, db_path, now=started + timedelta(minutes=16)).emitted is True
    assert (
        record_source_failure(7, db_path, now=started + timedelta(minutes=20)).outcome
        == "incident_already_open"
    )
    assert record_scanning_success(db_path).outcome == "recovery_unconfirmed"
    assert record_scanning_success(db_path).emitted is True
    assert record_scanning_success(db_path).outcome == "healthy_no_incident"
    assert (
        record_source_failure(1, db_path, now=started + timedelta(minutes=30)).outcome
        == "transient_degraded"
    )
    assert (
        record_source_failure(6, db_path, now=started + timedelta(minutes=46)).outcome
        == "notification_rate_limited"
    )
    assert record_scanning_success(db_path).outcome == "healthy_no_incident"
    assert (
        record_source_failure(1, db_path, now=started + timedelta(minutes=400)).outcome
        == "transient_degraded"
    )
    assert record_source_failure(
        6,
        db_path,
        now=started + timedelta(minutes=416),
    ).emitted is True
    assert messages == [OUTAGE_MESSAGE, RECOVERY_MESSAGE, OUTAGE_MESSAGE]


def test_confirmed_auth_required_sends_once(monkeypatch, tmp_path) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        "buyeeye.status_monitor.send_status_event",
        lambda message, level="info": (messages.append(message) or True, 204, 0.01),
    )
    db_path = tmp_path / "auth.db"

    assert record_auth_failure(db_path).outcome == "not_confirmed"
    assert record_auth_failure(db_path).emitted is True
    assert record_auth_failure(db_path).outcome == "incident_already_open"
    assert messages == [AUTH_REQUIRED_MESSAGE]


def test_status_payload_is_plain_and_listing_delivery_is_independent(monkeypatch, tmp_path) -> None:
    deliveries: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "buyeeye.discord_alerts._webhooks",
        lambda: {"status": "status-url", "buyouts": "listing-url"},
    )

    def capture(url: str, payload: dict):
        deliveries.append((url, payload))
        return True, 204, 0.01

    monkeypatch.setattr("buyeeye.discord_alerts._send_webhook", capture)
    assert send_status_event("BuyEye update: status monitoring is now live.")[0]
    listing = LiveListing(
        source="buyee",
        source_listing_id="independent-listing",
        url="https://buyee.jp/item/independent-listing",
        title_original="Roen patterned denim jacket",
        title_normalized="roen patterned denim jacket",
        brand="Roen",
        category="menswear",
        current_price=3_990,
        buyout_price=3_990,
        starting_price=3_990,
        seller_name=None,
        condition="Used",
    )
    assert send_listing_alert(listing, "buyouts", "actionable fixed price", 80)[0]

    assert deliveries[0] == (
        "status-url",
        {"content": "BuyEye update: status monitoring is now live."},
    )
    assert deliveries[1][0] == "listing-url"
    assert deliveries[1][1]["content"] == "BuyEye live listing [buyouts]"
