from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from buyeeye.browser_session import (
    load_session_cookies,
    restore_session_cookies,
    restore_session_cookies_async,
)
from buyeeye.cli import _health_readiness
from buyeeye.db import StatusIncidentState, get_session
from buyeeye.live_sources import (
    BuyeeAuthenticatedSource,
    auth_observation_script,
    confirmed_page_auth,
)
from buyeeye.status_monitor import (
    AUTH_RECOVERY_MESSAGE,
    AUTH_REQUIRED_MESSAGE,
    record_auth_failure,
    record_scanning_success,
    record_source_failure,
)


def test_auth_page_fixture_distinguishes_public_empty_login_and_authenticated():
    cases = json.loads((Path(__file__).parent / "fixtures/buyee_auth_observations.json").read_text())
    for case in cases:
        assert confirmed_page_auth(case["evidence"]) is case["authenticated"], case["name"]
        assert BuyeeAuthenticatedSource._empty_page_state(case["body"]) == case["empty_state"]


def test_auth_script_executes_against_offline_visible_controls_fixture():
    from playwright.sync_api import sync_playwright

    html = (Path(__file__).parent / "fixtures/buyee_auth_controls.html").read_text()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.set_content(html)
        assert confirmed_page_auth(page.evaluate(auth_observation_script())) is False
        page.locator('a[href="/login"]').evaluate("el => el.hidden = true")
        page.locator('a[href="/logout"]').evaluate("el => el.hidden = false")
        assert confirmed_page_auth(page.evaluate(auth_observation_script())) is True
        page.locator('a[href="/logout"]').evaluate("el => el.hidden = true")
        assert confirmed_page_auth(page.evaluate(auth_observation_script())) is None
        browser.close()


def test_auth_incident_survives_public_empty_unknown_scans_and_reopen(monkeypatch, tmp_path):
    messages = []
    monkeypatch.setattr("buyeeye.status_monitor.send_status_event",
                        lambda message, level: (messages.append(message) or True, 204, 0))
    path = tmp_path / "incident.db"
    now = datetime(2026, 9, 15, tzinfo=UTC)
    record_auth_failure(path, now=now)
    assert record_auth_failure(path, now=now).emitted
    for hours, auth, cards in [(1, False, 50), (2, None, 100), (3, True, 0), (8, False, 0)]:
        assert record_scanning_success(str(path), auth_confirmed=auth, cards_seen=cards).outcome == "auth_recovery_unconfirmed"
        assert record_auth_failure(str(path), now=now + timedelta(hours=hours)).outcome == "incident_already_open"
    assert messages == [AUTH_REQUIRED_MESSAGE]
    assert record_scanning_success(path, auth_confirmed=True, cards_seen=10).outcome == "auth_recovery_unconfirmed"
    record_source_failure(1, path)
    assert record_scanning_success(path, auth_confirmed=True, cards_seen=10).outcome == "auth_recovery_unconfirmed"
    assert record_scanning_success(str(path), auth_confirmed=True, cards_seen=5).emitted
    assert messages == [AUTH_REQUIRED_MESSAGE, AUTH_RECOVERY_MESSAGE]
    assert not record_scanning_success(path, auth_confirmed=True, cards_seen=20).emitted
    with get_session(path) as session:
        state = session.get(StatusIncidentState, 1)
        assert not state.auth_required_announced and state.auth_failure_streak == 0
    record_auth_failure(path, now=now + timedelta(hours=9))
    assert record_auth_failure(path, now=now + timedelta(hours=9)).emitted
    assert messages[-1] == AUTH_REQUIRED_MESSAGE


def test_auth_flapping_and_failed_delivery_are_persistently_rate_limited(monkeypatch, tmp_path):
    attempts = []
    monkeypatch.setattr("buyeeye.status_monitor.send_status_event",
                        lambda message, level: (attempts.append(message) and False, 500, 0))
    path = tmp_path / "incident.db"
    now = datetime(2026, 9, 15, tzinfo=UTC)
    record_auth_failure(path, now=now)
    assert record_auth_failure(path, now=now).outcome == "delivery_failed:500"
    assert record_auth_failure(str(path), now=now + timedelta(hours=1)).outcome == "notification_rate_limited"
    assert len(attempts) == 1


def test_auth_diagnostic_never_logs_page_secrets_or_treats_errors_as_recovery(caplog):
    class Page:
        async def evaluate(self, script):
            return {"logout_control": True, "cookie": "secret-value", "account": "private"}

    source = BuyeeAuthenticatedSource()
    with caplog.at_level("INFO", logger="buyeeye.runtime"):
        asyncio.run(source._observe_auth(Page()))
    assert source.auth_confirmed is True
    assert "secret-value" not in caplog.text and "private" not in caplog.text

    class ClosedPage:
        async def evaluate(self, script):
            raise RuntimeError("secret-value")

    asyncio.run(source._observe_auth(ClosedPage()))
    assert source.auth_confirmed is None
    assert "secret-value" not in caplog.text


def test_public_acquisition_does_not_make_expired_auth_health_ready():
    now = datetime.now(UTC)
    runtime = SimpleNamespace(last_successful_scan=now, consecutive_source_failures=0,
                              last_auth_confirmed=False)
    assert _health_readiness({"database_ready": True, "session_ready": True}, runtime)[0] == "degraded"


def test_expired_portable_cookie_cannot_delete_newer_profile_cookie(monkeypatch, tmp_path):
    monkeypatch.setattr("buyeeye.browser_session.time.time", lambda: 2000)
    cookies = [{"name": name, "value": "fixture", "domain": ".buyee.jp", "expires": expiry}
               for name, expiry in [("expired", 1000), ("current", 3000), ("session", -1)]]
    state = tmp_path / "state.json"
    original = json.dumps({"cookies": cookies})
    state.write_text(original)

    class Context:
        def __init__(self):
            self.names = []

        def add_cookies(self, values):
            self.names.extend(c["name"] for c in values)

    class AsyncContext(Context):
        async def add_cookies(self, values):
            super().add_cookies(values)

    sync, asynchronous = Context(), AsyncContext()
    restore_session_cookies(sync, state)
    asyncio.run(restore_session_cookies_async(asynchronous, state))
    assert sync.names == asynchronous.names == ["current", "session"]
    assert set(load_session_cookies(state)) == {"current", "session"}
    assert state.read_text() == original
