from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .db import DB_PATH, StatusIncidentState, get_session, init_db
from .discord_alerts import send_status_event

OUTAGE_MESSAGE = "BuyEye is temporarily down and not scanning."
RECOVERY_MESSAGE = "BuyEye recovered and is scanning normally again."
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
) -> StatusEventResult:
    """Announce only a prolonged source outage, once and at a bounded cadence."""
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    state = _state(path)
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
    if consecutive_failures < outage_threshold:
        return StatusEventResult(False, "transient_degraded")
    if current_time - failure_started < timedelta(
        minutes=max(0.0, outage_min_duration_minutes)
    ):
        return StatusEventResult(False, "not_prolonged")
    if state.outage_announced:
        return StatusEventResult(False, "incident_already_open")
    last_outage = state.last_outage_announced_at
    if last_outage is not None:
        if last_outage.tzinfo is None:
            last_outage = last_outage.replace(tzinfo=UTC)
        if current_time - last_outage < timedelta(
            minutes=max(0.0, notification_cooldown_minutes)
        ):
            return StatusEventResult(False, "notification_rate_limited")
    sent, status, _ = send_status_event(OUTAGE_MESSAGE, "error")
    if not sent:
        return StatusEventResult(False, f"delivery_failed:{status}")
    with get_session(path) as session:
        state = session.get(StatusIncidentState, 1)
        assert state is not None
        state.outage_announced = True
        state.last_outage_announced_at = current_time
        session.commit()
    return StatusEventResult(True, "emitted")


def record_auth_failure(
    path: str | Path = DB_PATH,
    *,
    confirmation_threshold: int = 2,
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
    if streak < confirmation_threshold:
        return StatusEventResult(False, "not_confirmed")
    if announced:
        return StatusEventResult(False, "incident_already_open")
    sent, status, _ = send_status_event(AUTH_REQUIRED_MESSAGE, "warning")
    if not sent:
        return StatusEventResult(False, f"delivery_failed:{status}")
    with get_session(path) as session:
        state = session.get(StatusIncidentState, 1)
        assert state is not None
        state.auth_required_announced = True
        session.commit()
    return StatusEventResult(True, "emitted")


def record_scanning_success(
    path: str | Path = DB_PATH,
    *,
    recovery_confirmation_scans: int = 2,
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
    if not outage_announced:
        return StatusEventResult(False, "healthy_no_incident")
    if recovery_streak < max(1, int(recovery_confirmation_scans)):
        return StatusEventResult(False, "recovery_unconfirmed")
    sent, status, _ = send_status_event(RECOVERY_MESSAGE, "recovery")
    if not sent:
        return StatusEventResult(False, f"delivery_failed:{status}")
    with get_session(path) as session:
        current = session.get(StatusIncidentState, 1)
        assert current is not None
        current.outage_announced = False
        current.last_recovery_announced_at = datetime.now(UTC)
        current.recovery_success_streak = 0
        session.commit()
    return StatusEventResult(True, "emitted")
