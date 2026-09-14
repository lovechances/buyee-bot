from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from buyeeye.cli import main
from buyeeye.db import init_db, save_alert_state
from buyeeye.status_monitor import (
    MAJOR_UPDATE_MESSAGES,
    _release_post,
    announce_major_update,
    record_scanning_success,
    release_event_status,
)

EVENT = "ultra-refinement-v11"
NOW = datetime(2026, 9, 14, 1, tzinfo=UTC)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_STATUS", "https://discord.com/api/webhooks/123/fixture")


def test_release_claim_precedes_send_and_success_is_durable(tmp_path, monkeypatch, configured):
    path = tmp_path / "release.db"
    calls = []

    def send(message):
        calls.append(message)
        claim = release_event_status(EVENT, path)
        assert claim["status"] == "attempting"
        assert claim["attempts"] == 1
        assert claim["last_attempt_at"] == NOW.isoformat()
        # An overlapping explicit invocation cannot race into a second POST.
        assert announce_major_update(EVENT, path, now=NOW).outcome == "delivery_unconfirmed_do_not_retry"
        return {"state": "sent", "result": "200", "message_id": "1234", "channel_id": "4567"}

    monkeypatch.setattr("buyeeye.status_monitor.send_release_summary", send)
    result = announce_major_update(EVENT, path, now=NOW)
    assert result.emitted and result.message_id == "1234"
    assert announce_major_update(EVENT, path, now=NOW + timedelta(days=1)).outcome == "deduped"
    persisted = release_event_status(EVENT, path)
    assert persisted["status"] == "sent"
    assert persisted["channel_id"] == "4567"
    assert persisted["message"] == MAJOR_UPDATE_MESSAGES[EVENT]
    assert calls == [MAJOR_UPDATE_MESSAGES[EVENT]]
    # Healthy acquisition/ordinary startup does not initiate a release event.
    record_scanning_success(path)
    assert len(calls) == 1


def test_unconfigured_release_remains_pending_without_consuming_attempt(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_STATUS", raising=False)
    path = tmp_path / "unconfigured.db"
    result = announce_major_update(EVENT, path, now=NOW)
    assert result.outcome == "delivery_failed:status_unconfigured"
    state = release_event_status(EVENT, path)
    assert state["status"] == "pending" and state["attempts"] == 0


def test_definite_failures_have_persistent_cooldown_and_bounded_explicit_retry(tmp_path, monkeypatch, configured):
    path = tmp_path / "retry.db"
    calls = []
    monkeypatch.setattr("buyeeye.status_monitor.send_release_summary", lambda message: (
        calls.append(message) or {"state": "pending", "result": "http_429", "retry_seconds": 60}
    ))
    assert announce_major_update(EVENT, path, now=NOW).outcome == "delivery_failed:http_429"
    assert announce_major_update(EVENT, path, now=NOW + timedelta(seconds=59)).outcome == "retry_cooldown"
    for seconds in (60, 120):
        assert not announce_major_update(EVENT, path, now=NOW + timedelta(seconds=seconds)).emitted
    assert announce_major_update(EVENT, path, now=NOW + timedelta(days=1)).outcome == "retry_limit_reached"
    assert len(calls) == 3
    assert release_event_status(EVENT, path)["attempts"] == 3


@pytest.mark.parametrize("failure", ["delivery_timeout", "http_502", "accepted_without_message_evidence"])
def test_ambiguous_release_is_never_blindly_retried(tmp_path, monkeypatch, configured, failure):
    path = tmp_path / "ambiguous.db"
    calls = []
    monkeypatch.setattr("buyeeye.status_monitor.send_release_summary", lambda message: (
        calls.append(message) or {"state": "ambiguous", "result": failure}
    ))
    assert announce_major_update(EVENT, path, now=NOW).outcome == "delivery_unconfirmed_do_not_retry"
    assert announce_major_update(EVENT, path, now=NOW + timedelta(days=1)).outcome == "delivery_unconfirmed_do_not_retry"
    assert len(calls) == 1
    assert release_event_status(EVENT, path)["status"] == "ambiguous"


def test_legacy_keys_and_incident_state_survive_release_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    init_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO statusincidentstate(id,outage_announced,auth_required_announced,auth_failure_streak,release_keys_json) "
            "VALUES(1,1,0,0,?)", (json.dumps(["status-monitoring-v1"]),)
        )
    monkeypatch.setattr("buyeeye.status_monitor.send_release_summary", lambda _: pytest.fail("legacy release resent"))
    assert announce_major_update("status-monitoring-v1", path).outcome == "deduped"
    assert release_event_status("status-monitoring-v1", path)["status"] == "sent_legacy"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT outage_announced FROM statusincidentstate").fetchone()[0] == 1


def test_release_status_is_read_only_on_absent_and_legacy_database(tmp_path, monkeypatch, capsys):
    path = tmp_path / "old.db"
    assert release_event_status(EVENT, path)["status"] == "not_registered"
    assert not path.exists()
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE statusincidentstate(id INTEGER PRIMARY KEY,release_keys_json TEXT)")
        connection.execute("INSERT INTO statusincidentstate VALUES(1,'[]')")
    before = path.read_bytes()
    monkeypatch.setenv("BUYEYE_DB_PATH", str(path))
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    assert main(["status-event", EVENT, "--status"]) == 0
    assert json.loads(capsys.readouterr().out)["read_only"] is True
    assert path.read_bytes() == before


def test_crashed_attempt_or_corrupt_history_does_not_replay(tmp_path, configured):
    path = tmp_path / "crash.db"
    init_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO statusincidentstate(id,outage_announced,auth_required_announced,auth_failure_streak,release_keys_json,release_events_json) "
            "VALUES(1,0,0,0,'[]',?)", (json.dumps({EVENT: {"status": "attempting", "attempts": 1}}),)
        )
    assert announce_major_update(EVENT, path).outcome == "delivery_unconfirmed_do_not_retry"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE statusincidentstate SET release_events_json='broken'")
    with pytest.raises(ValueError, match="do not resend"):
        announce_major_update(EVENT, path)


class Pipe:
    def __init__(self):
        self.results = []

    def send(self, value):
        self.results.append(value)


def test_release_transport_requests_message_confirmation_without_mentions(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={"id": "123", "channel_id": "456"})

    monkeypatch.setattr("buyeeye.status_monitor.httpx.post", post)
    pipe = Pipe()
    _release_post("https://discord.com/api/webhooks/123/fixture?wait=false", "Short update", pipe)
    assert len(calls) == 1
    assert httpx.URL(calls[0][0]).params["wait"] == "true"
    assert calls[0][1]["json"] == {"content": "Short update", "allowed_mentions": {"parse": []}}
    assert pipe.results == [{"state": "sent", "result": "200", "message_id": "123", "channel_id": "456"}]


@pytest.mark.parametrize("status,body,expected", [
    (204, None, "ambiguous"), (200, {"id": "123"}, "ambiguous"),
    (502, {}, "ambiguous"), (429, {}, "pending"), (403, {}, "pending"),
])
def test_release_transport_does_not_retry_ambiguous_or_failed_http(monkeypatch, status, body, expected):
    calls = []
    monkeypatch.setattr("buyeeye.status_monitor.httpx.post", lambda *args, **kwargs: (
        calls.append(True) or httpx.Response(status, json=body)
    ))
    pipe = Pipe()
    _release_post("https://discord.com/api/webhooks/123/fixture", "Short update", pipe)
    assert len(calls) == 1
    assert pipe.results[0]["state"] == expected


def test_release_network_error_does_not_leak_secret_url(monkeypatch):
    def failing(*args, **kwargs):
        raise httpx.ReadTimeout("private-webhook-token")

    monkeypatch.setattr("buyeeye.status_monitor.httpx.post", failing)
    pipe = Pipe()
    _release_post("https://discord.com/api/webhooks/123/fixture", "Short update", pipe)
    assert pipe.results == [{"state": "ambiguous", "result": "network_outcome_unconfirmed"}]


def test_offer_context_is_additive_and_existing_send_survives(tmp_path):
    path = tmp_path / "ledger.db"
    init_db(path)
    context = json.dumps({"model": "aids_t", "size": "M"})
    save_alert_state("buyee", "real123", "signature", "buyouts", path,
                     offer_context_json=context)
    init_db(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT offer_context_json FROM alertsendledger").fetchone()[0] == context
        assert connection.execute("SELECT COUNT(*) FROM alertstate").fetchone()[0] == 1
        indexes = [row[1] for row in connection.execute("PRAGMA index_list(alertsendledger)")]
        assert "ix_alertsendledger_sent_at" in indexes
