from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
}


@dataclass(frozen=True)
class StatusEventResult:
    emitted: bool
    outcome: str


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


def _release_keys(state: StatusIncidentState) -> set[str]:
    try:
        values = json.loads(state.release_keys_json or "[]")
    except (TypeError, json.JSONDecodeError):
        return set()
    return {str(value) for value in values} if isinstance(values, list) else set()


def announce_major_update(
    event_key: str,
    path: str | Path = DB_PATH,
) -> StatusEventResult:
    """Emit an explicitly registered user-facing release event once."""
    message = MAJOR_UPDATE_MESSAGES.get(event_key)
    if message is None:
        raise ValueError(f"Unknown major status event: {event_key}")
    state = _state(path)
    keys = _release_keys(state)
    if event_key in keys:
        return StatusEventResult(False, "deduped")
    sent, status, _ = send_status_event(message)
    if not sent:
        return StatusEventResult(False, f"delivery_failed:{status}")
    keys.add(event_key)
    with get_session(path) as session:
        current = session.get(StatusIncidentState, 1)
        assert current is not None
        current.release_keys_json = json.dumps(sorted(keys))
        session.commit()
    return StatusEventResult(True, "emitted")


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
