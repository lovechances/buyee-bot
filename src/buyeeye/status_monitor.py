from __future__ import annotations

import json
import logging
import multiprocessing
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .db import DB_PATH, StatusIncidentState, get_session, init_db
from .discord_alerts import send_status_event

OUTAGE_MESSAGE = "BuyEye source acquisition is degraded after sustained failed scans. Recovery is running."
RECOVERY_MESSAGE = "BuyEye has resumed successful scans. Some searches may still need retries."
AUTH_REQUIRED_MESSAGE = "BuyEye needs its Buyee login refreshed."
MAJOR_UPDATE_MESSAGES = {
    "alert-quality-recall-v2": (
        "BuyEye update: alert quality and opportunity finding have been improved."
    ),
    "status-monitoring-v1": "BuyEye update: status monitoring is now live.",
    "ultra-refinement-v11": (
        "BuyEye now separates listing prices from estimated costs and handles misleading product descriptions more carefully. "
        "Inspection has also been adjusted so overlooked candidates can get a closer look."
    ),
}
RELEASE_MAX_ATTEMPTS = 3
RELEASE_RETRY_SECONDS = 60
RELEASE_TOTAL_TIMEOUT = 15


@dataclass(frozen=True)
class StatusEventResult:
    emitted: bool
    outcome: str
    message_id: str | None = None
    channel_id: str | None = None


def _transition(event: str, result: StatusEventResult, **evidence: object) -> StatusEventResult:
    """Persist sanitized incident decisions in the existing rotating runtime log."""
    logging.getLogger("buyeeye.runtime").info(
        "status_transition %s",
        json.dumps(
            {"event": event, "outcome": result.outcome, "emitted": result.emitted, **evidence},
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return result


def _state(path: str | Path) -> StatusIncidentState:
    init_db(path)
    with get_session(path) as session:
        state = session.get(StatusIncidentState, 1)
        if state is None:
            state = StatusIncidentState()
            session.add(state)
            session.commit()
            session.refresh(state)
        session.expunge(state)
        return state


def _release_post(url: str, message: str, pipe: Any) -> None:
    """One POST only: a timeout after sending cannot safely be retried blindly."""
    try:
        response = httpx.post(
            str(httpx.URL(url).copy_set_param("wait", "true")),
            json={"content": message, "allowed_mentions": {"parse": []}},
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
        )
        if response.is_success:
            try:
                data = response.json()
            except ValueError:
                data = {}
            if isinstance(data, dict) and str(data.get("id", "")).isdigit() and str(data.get("channel_id", "")).isdigit():
                pipe.send({"state": "sent", "result": str(response.status_code),
                           "message_id": str(data["id"]), "channel_id": str(data["channel_id"])})
            else:
                pipe.send({"state": "ambiguous", "result": "accepted_without_message_evidence"})
        elif response.status_code >= 500:
            pipe.send({"state": "ambiguous", "result": f"http_{response.status_code}"})
        else:
            try:
                retry = float(response.headers.get("Retry-After", RELEASE_RETRY_SECONDS))
            except ValueError:
                retry = RELEASE_RETRY_SECONDS
            pipe.send({"state": "pending", "result": f"http_{response.status_code}",
                       "retry_seconds": min(3600, max(RELEASE_RETRY_SECONDS, retry))})
    except (httpx.ConnectError, httpx.ConnectTimeout):
        pipe.send({"state": "pending", "result": "connection_failed_before_send"})
    except httpx.InvalidURL:
        pipe.send({"state": "pending", "result": "invalid_webhook_configuration"})
    except (httpx.HTTPError, TimeoutError):
        pipe.send({"state": "ambiguous", "result": "network_outcome_unconfirmed"})


def send_release_summary(message: str) -> dict[str, Any]:
    """Use only the existing status webhook, with bounded wait=true confirmation."""
    url = os.environ.get("DISCORD_WEBHOOK_STATUS", "").strip()
    if not url:
        return {"state": "pending", "result": "status_unconfigured"}
    parent, child = multiprocessing.Pipe(duplex=False)
    process = multiprocessing.Process(target=_release_post, args=(url, message, child))
    try:
        process.start()
        process.join(RELEASE_TOTAL_TIMEOUT)
        if process.is_alive():
            process.terminate()
            process.join(2)
            return {"state": "ambiguous", "result": "delivery_timeout"}
        if parent.poll():
            return parent.recv()
        return {"state": "ambiguous", "result": "worker_outcome_unconfirmed"}
    finally:
        parent.close()
        child.close()


def _release_history(value: str | None) -> dict[str, Any]:
    try:
        events = json.loads(value or "{}")
    except (TypeError, ValueError) as exc:
        raise ValueError("release_event_history_invalid; do not resend until reconciled") from exc
    if not isinstance(events, dict) or any(not isinstance(event, dict) for event in events.values()):
        raise ValueError("release_event_history_invalid; do not resend until reconciled")
    return events


def release_event_status(event_key: str, path: str | Path = DB_PATH) -> dict[str, Any]:
    """Read-only even for old or absent databases; never registers or sends."""
    if event_key not in MAJOR_UPDATE_MESSAGES:
        raise ValueError(f"Unknown major status event: {event_key}")
    base = {"event_key": event_key, "message": MAJOR_UPDATE_MESSAGES[event_key],
            "status": "not_registered", "attempts": 0, "read_only": True}
    if not Path(path).exists():
        return base
    with sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='statusincidentstate'").fetchone():
            return base
        row = connection.execute("SELECT * FROM statusincidentstate WHERE id=1").fetchone()
        if row is None:
            return base
        record = dict(row)
        event = _release_history(record.get("release_events_json")).get(event_key)
        if event:
            return {**base, **event}
        if event_key in json.loads(row["release_keys_json"] or "[]"):
            return {**base, "status": "sent_legacy", "message_id": None}
    return base


def announce_major_update(
    event_key: str,
    path: str | Path = DB_PATH,
    *,
    now: datetime | None = None,
) -> StatusEventResult:
    """Explicit release invocation only; normal daemon starts never call this.

    Persist a claim before the network operation. Definite failures allow at
    most three explicitly invoked attempts with cooldown; ambiguous delivery
    or a crash with an outstanding claim requires human reconciliation.
    """
    message = MAJOR_UPDATE_MESSAGES.get(event_key)
    if message is None:
        raise ValueError(f"Unknown major status event: {event_key}")
    current_time = now or datetime.now(UTC)
    current_time = current_time.replace(tzinfo=current_time.tzinfo or UTC)
    _state(path)
    with sqlite3.connect(path, timeout=5) as connection:
        connection.execute("BEGIN IMMEDIATE")
        encoded, keys_json = connection.execute(
            "SELECT release_events_json,release_keys_json FROM statusincidentstate WHERE id=1"
        ).fetchone()
        events = _release_history(encoded)
        keys = set(json.loads(keys_json or "[]"))
        event = events.get(event_key, {})
        if event_key in keys or event.get("status") == "sent":
            return StatusEventResult(False, "deduped", event.get("message_id"), event.get("channel_id"))
        if event.get("status") in {"attempting", "ambiguous"}:
            return StatusEventResult(False, "delivery_unconfirmed_do_not_retry")
        if int(event.get("attempts", 0)) >= RELEASE_MAX_ATTEMPTS:
            return StatusEventResult(False, "retry_limit_reached")
        if event.get("next_retry_at") and current_time < datetime.fromisoformat(event["next_retry_at"]):
            return StatusEventResult(False, "retry_cooldown")
        if event.get("message") and event["message"] != message:
            return StatusEventResult(False, "registered_message_changed")
        event = {**event, "event_key": event_key, "message": message,
                 "created_at": event.get("created_at", current_time.isoformat()),
                 "status": "pending", "attempts": int(event.get("attempts", 0))}
        configured = bool(os.environ.get("DISCORD_WEBHOOK_STATUS", "").strip())
        if configured:
            event.update(status="attempting", attempts=event["attempts"] + 1,
                         last_attempt_at=current_time.isoformat(), next_retry_at=None)
        else:
            event["last_result"] = "status_unconfigured"
        events[event_key] = event
        connection.execute("UPDATE statusincidentstate SET release_events_json=? WHERE id=1",
                           (json.dumps(events, sort_keys=True),))
    if not configured:
        return StatusEventResult(False, "delivery_failed:status_unconfigured")

    delivery = send_release_summary(message)
    with sqlite3.connect(path, timeout=5) as connection:
        connection.execute("BEGIN IMMEDIATE")
        encoded, keys_json = connection.execute(
            "SELECT release_events_json,release_keys_json FROM statusincidentstate WHERE id=1"
        ).fetchone()
        events = _release_history(encoded)
        event = events[event_key]
        event.update(status=delivery["state"], last_result=delivery["result"])
        if delivery["state"] == "sent":
            event.update(sent_at=current_time.isoformat(), message_id=delivery["message_id"], channel_id=delivery["channel_id"])
            keys = set(json.loads(keys_json or "[]"))
            keys.add(event_key)
            connection.execute("UPDATE statusincidentstate SET release_keys_json=? WHERE id=1",
                               (json.dumps(sorted(keys)),))
        elif delivery["state"] == "pending":
            event["next_retry_at"] = (current_time + timedelta(
                seconds=delivery.get("retry_seconds", RELEASE_RETRY_SECONDS)
            )).isoformat()
        connection.execute("UPDATE statusincidentstate SET release_events_json=? WHERE id=1",
                           (json.dumps(events, sort_keys=True),))
    if delivery["state"] == "sent":
        return StatusEventResult(True, "emitted", delivery["message_id"], delivery["channel_id"])
    return StatusEventResult(False, "delivery_unconfirmed_do_not_retry" if delivery["state"] == "ambiguous"
                             else f"delivery_failed:{delivery['result']}")


def record_source_failure(
    consecutive_failures: int,
    path: str | Path = DB_PATH,
    *,
    outage_threshold: int = 6,
    outage_min_duration_minutes: float = 15,
    notification_cooldown_minutes: float = 360,
    now: datetime | None = None,
    query: str | None = None,
    query_scope: str | None = None,
    error_type: str | None = None,
) -> StatusEventResult:
    """Announce only a prolonged source outage, once and at a bounded cadence."""
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    state = _state(path)
    outage_was_announced = state.outage_announced
    previous_outage_at = state.last_outage_announced_at
    with get_session(path) as session:
        persisted = session.get(StatusIncidentState, 1)
        assert persisted is not None
        if persisted.source_failure_started_at is None or consecutive_failures <= 1:
            persisted.source_failure_started_at = current_time
        persisted.recovery_success_streak = 0
        session.commit()
        failure_started = persisted.source_failure_started_at or current_time
    if failure_started.tzinfo is None:
        failure_started = failure_started.replace(tzinfo=UTC)

    def result(emitted: bool, outcome: str) -> StatusEventResult:
        return _transition(
            "source_failure",
            StatusEventResult(emitted, outcome),
            query=query,
            query_scope=query_scope,
            error_type=error_type,
            consecutive_failures=consecutive_failures,
            failure_started_at=failure_started.isoformat(),
            failure_duration_seconds=max(0, (current_time - failure_started).total_seconds()),
            outage_previously_announced=outage_was_announced,
            last_outage_announced_at=(
                previous_outage_at.isoformat() if previous_outage_at else None
            ),
        )

    if consecutive_failures < outage_threshold:
        return result(False, "transient_degraded")
    if current_time - failure_started < timedelta(minutes=max(0.0, outage_min_duration_minutes)):
        return result(False, "not_prolonged")
    if state.outage_announced:
        return result(False, "incident_already_open")
    last_outage = state.last_outage_announced_at
    if last_outage is not None:
        if last_outage.tzinfo is None:
            last_outage = last_outage.replace(tzinfo=UTC)
        if current_time - last_outage < timedelta(minutes=max(0.0, notification_cooldown_minutes)):
            return result(False, "notification_rate_limited")
    sent, status, _ = send_status_event(OUTAGE_MESSAGE, "error")
    if not sent:
        return result(False, f"delivery_failed:{status}")
    with get_session(path) as session:
        state = session.get(StatusIncidentState, 1)
        assert state is not None
        state.outage_announced = True
        state.last_outage_announced_at = current_time
        session.commit()
    return result(True, "emitted")


def record_auth_failure(
    path: str | Path = DB_PATH,
    *,
    confirmation_threshold: int = 2,
    query: str | None = None,
    query_scope: str | None = None,
) -> StatusEventResult:
    """Require repeated authenticated-source failures before asking for login."""
    _state(path)
    with get_session(path) as session:
        state = session.get(StatusIncidentState, 1)
        assert state is not None
        state.auth_failure_streak += 1
        streak = state.auth_failure_streak
        announced = state.auth_required_announced
        session.commit()

    def result(emitted: bool, outcome: str) -> StatusEventResult:
        return _transition(
            "auth_failure",
            StatusEventResult(emitted, outcome),
            query=query,
            query_scope=query_scope,
            auth_failure_streak=streak,
            auth_previously_announced=announced,
        )

    if streak < confirmation_threshold:
        return result(False, "not_confirmed")
    if announced:
        return result(False, "incident_already_open")
    sent, status, _ = send_status_event(AUTH_REQUIRED_MESSAGE, "warning")
    if not sent:
        return result(False, f"delivery_failed:{status}")
    with get_session(path) as session:
        state = session.get(StatusIncidentState, 1)
        assert state is not None
        state.auth_required_announced = True
        session.commit()
    return result(True, "emitted")


def record_scanning_success(
    path: str | Path = DB_PATH,
    *,
    recovery_confirmation_scans: int = 2,
    now: datetime | None = None,
    query: str | None = None,
    query_scope: str | None = None,
    cards_seen: int | None = None,
) -> StatusEventResult:
    """Close auth quietly and confirm recovery only for an announced outage."""
    state = _state(path)
    outage_announced = state.outage_announced
    recovery_streak = (state.recovery_success_streak or 0) + 1 if outage_announced else 0
    with get_session(path) as session:
        current = session.get(StatusIncidentState, 1)
        assert current is not None
        current.auth_failure_streak = 0
        current.auth_required_announced = False
        current.source_failure_started_at = None
        current.recovery_success_streak = recovery_streak
        session.commit()

    def result(emitted: bool, outcome: str) -> StatusEventResult:
        # Ordinary healthy scans already have a full scan log. Only an actual
        # incident/reset needs a second line; this keeps long-run logging bounded.
        event_result = StatusEventResult(emitted, outcome)
        if not (
            outage_announced
            or state.source_failure_started_at
            or state.auth_failure_streak
            or state.auth_required_announced
        ):
            return event_result
        return _transition(
            "scanning_success",
            event_result,
            query=query,
            query_scope=query_scope,
            cards_seen=cards_seen,
            validated_empty=cards_seen == 0,
            recovery_success_streak=recovery_streak,
            outage_previously_announced=outage_announced,
            prior_failure_started_at=(
                state.source_failure_started_at.isoformat()
                if state.source_failure_started_at
                else None
            ),
        )

    if not outage_announced:
        return result(False, "healthy_no_incident")
    if recovery_streak < max(1, int(recovery_confirmation_scans)):
        return result(False, "recovery_unconfirmed")
    sent, status, _ = send_status_event(RECOVERY_MESSAGE, "recovery")
    if not sent:
        return result(False, f"delivery_failed:{status}")
    with get_session(path) as session:
        current = session.get(StatusIncidentState, 1)
        assert current is not None
        current.outage_announced = False
        current.last_recovery_announced_at = now or datetime.now(UTC)
        current.recovery_success_streak = 0
        session.commit()
    return result(True, "emitted")
