from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup

from buyeeye.cli import _record_source_attempt_failure
from buyeeye.db import StatusIncidentState, get_session, init_db
from buyeeye.live_sources import (
    ParserAnomalyError,
    _log_parser_failure_shape,
    _parser_failure_shape,
)
from buyeeye.runtime_state import get_health, update_health
from buyeeye.status_monitor import (
    OUTAGE_MESSAGE,
    RECOVERY_MESSAGE,
    record_scanning_success,
    record_source_failure,
)


def test_sustained_runtime_incident_resets_at_intervening_success_and_survives_reopen(
    monkeypatch, tmp_path, caplog
) -> None:
    """Reproduce the observed nine failures, not an imagined short status flap."""
    messages: list[str] = []
    monkeypatch.setattr(
        "buyeeye.status_monitor.send_status_event",
        lambda message, level="info": (messages.append(message) or True, 204, 0.01),
    )
    caplog.set_level(logging.INFO, logger="buyeeye.runtime")
    path = tmp_path / "incident.db"
    day = datetime(2026, 9, 9, tzinfo=UTC)

    def at(hours: int, minutes: int, seconds: int) -> datetime:
        return day + timedelta(hours=hours, minutes=minutes, seconds=seconds)

    record_source_failure(1, path, now=at(11, 17, 26), query_scope="broad")
    assert (
        record_scanning_success(
            path, now=at(11, 18, 18), query_scope="market_structure", cards_seen=5
        ).outcome
        == "healthy_no_incident"
    )
    with get_session(path) as session:
        assert session.get(StatusIncidentState, 1).source_failure_started_at is None

    times = [(21, 37), (23, 37), (27, 8), (30, 7), (35, 9), (39, 8), (45, 16), (51, 51), (58, 56)]
    for streak, (minutes, seconds) in enumerate(times, 1):
        # Each call reloads state through a new SQLite connection, as after a
        # daemon restart; incident/cooldown state is not kept in memory.
        outcome = record_source_failure(
            streak,
            str(path),
            now=at(11, minutes, seconds),
            query=f"Broad family {streak}",
            query_scope="broad",
            error_type="ParserAnomalyError",
        )
        assert outcome.emitted is (streak == 6)
    assert messages == [OUTAGE_MESSAGE]
    assert (
        record_scanning_success(
            path, now=at(12, 3, 42), query_scope="market_structure", cards_seen=16
        ).outcome
        == "recovery_unconfirmed"
    )
    assert record_scanning_success(
        str(path), now=at(12, 4, 20), query_scope="market_structure", cards_seen=0
    ).emitted
    assert messages == [OUTAGE_MESSAGE, RECOVERY_MESSAGE]
    with get_session(path) as session:
        state = session.get(StatusIncidentState, 1)
        assert not state.outage_announced
        assert state.source_failure_started_at is None
        assert state.last_outage_announced_at == at(11, 39, 8).replace(tzinfo=None)
        assert state.last_recovery_announced_at == at(12, 4, 20).replace(tzinfo=None)

    record_source_failure(1, path, now=at(12, 10, 0))
    assert record_source_failure(6, str(path), now=at(12, 30, 0)).outcome == (
        "notification_rate_limited"
    )
    assert messages == [OUTAGE_MESSAGE, RECOVERY_MESSAGE]
    logs = [
        json.loads(record.message.split("status_transition ", 1)[1])
        for record in caplog.records
        if "status_transition " in record.message
    ]
    opened = next(row for row in logs if row["event"] == "source_failure" and row["emitted"])
    assert opened["consecutive_failures"] == 6
    assert opened["failure_duration_seconds"] == 17 * 60 + 31
    assert opened["outage_previously_announced"] is False
    assert opened["error_type"] == "ParserAnomalyError"
    recovered = next(row for row in logs if row["event"] == "scanning_success" and row["emitted"])
    assert recovered["validated_empty"] is True
    assert recovered["recovery_success_streak"] == 2


def test_actual_parser_failure_updates_health_without_relabeling_valid_empty_scans(tmp_path):
    path = tmp_path / "parser-health.db"
    init_db(path)
    old = datetime(2026, 9, 1, tzinfo=UTC)
    update_health(path, last_parser_anomaly=old)
    _record_source_attempt_failure(path, RuntimeError("transport failure"))
    assert get_health(path).last_parser_anomaly == old.replace(tzinfo=None)
    _record_source_attempt_failure(path, ParserAnomalyError("no primary cards"))
    health = get_health(path)
    assert health.last_parser_anomaly == health.last_failure_at
    assert health.last_parser_anomaly > old.replace(tzinfo=None)
    assert health.browser_restart_count == 2
    recorded = health.last_parser_anomaly
    for _ in range(3):
        record_scanning_success(path, cards_seen=0)
    assert get_health(path).last_parser_anomaly == recorded


def test_parser_failure_shape_fixture_exposes_module_counts_but_no_page_content(caplog):
    html = (Path(__file__).parent / "fixtures" / "buyee_search_page.html").read_text()
    soup = BeautifulSoup(html, "lxml")

    class Page:
        url = "https://user:password@buyee.jp/item/search/query/secret/category/23176?token=hidden"

        async def evaluate(self, script, selector):
            assert "innerText" not in script and "innerHTML" not in script
            return {
                "ready_state": "complete",
                "primary_containers": len(soup.select(".g-main")),
                "primary_lists": len(soup.select(".g-main > ul.auctionSearchResult")),
                "primary_cards": len(soup.select(selector)),
                "primary_auction_links": len(
                    soup.select(selector + ' a[href*="/item/jdirectitems/auction/"]')
                ),
                "all_auction_links": len(soup.select('a[href*="/item/jdirectitems/auction/"]')),
            }

    caplog.set_level(logging.WARNING, logger="buyeeye.runtime")
    asyncio.run(_log_parser_failure_shape(Page(), SimpleNamespace(status=200), "fixture_check"))
    evidence = json.loads(caplog.records[-1].message.split("parser_failure_shape ", 1)[1])
    assert evidence["primary_cards"] == 2
    assert evidence["all_auction_links"] > evidence["primary_auction_links"]
    assert evidence["http_status"] == 200
    assert evidence["url_path"] == "/item/search/query/:query/category/:category"
    assert not any(secret in caplog.text for secret in ["password", "secret", "hidden", "token"])
    assert (
        _parser_failure_shape("https://buyee.jp/auth/private-token", 403, {})["url_path"]
        == "/other"
    )


def test_parser_diagnostic_failure_does_not_mask_original_safe_failure(caplog):
    class ClosedPage:
        async def evaluate(self, *_args):
            raise RuntimeError("private page content must never be logged")

    asyncio.run(_log_parser_failure_shape(ClosedPage(), None, "no_primary_cards_after_reload"))
    assert "parser_failure_shape_unavailable error=RuntimeError" in caplog.text
    assert "private page content" not in caplog.text
