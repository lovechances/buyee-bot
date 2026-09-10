from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Field, Session, SQLModel, UniqueConstraint, create_engine, select

from .settings import runtime_state_path

DB_PATH = runtime_state_path("BUYEYE_DB_PATH", "buyeeye.db")


class Listing(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("source", "source_listing_id", name="uq_listing_source_id"),)

    id: int | None = Field(default=None, primary_key=True)
    source: str
    source_listing_id: str | None = None
    url: str | None = None
    title_original: str | None = None
    title_normalized: str | None = None
    brand: str | None = None
    category: str | None = None
    current_price: int | None = None
    buyout_price: int | None = None
    starting_price: int | None = None
    time_remaining: str | None = None
    buyout_indicator: str | None = None
    bid_count: int | None = None
    watch_count: int | None = None
    image_url: str | None = None
    recommended_max_bid_jpy: int | None = None
    taste_score: int | None = None
    overall_score: int | None = None
    decision: str | None = None
    is_alerted: bool = False
    desirability_score: int | None = None
    opportunity_score: int | None = None
    auth_risk: str | None = None
    comp_confidence: str | None = None


class AlertState(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("source", "source_listing_id", name="uq_alert_source_id"),)

    id: int | None = Field(default=None, primary_key=True)
    source: str
    source_listing_id: str
    signature: str
    route: str
    thesis: str | None = None
    material_state_version: int | None = None
    last_material_price_jpy: int | None = None
    last_material_price_kind: str | None = None
    last_material_maturity_state: str | None = None
    last_material_identity: str | None = None
    last_material_risk_state: str | None = None
    last_material_evidence_state: str | None = None
    last_verified_median_jpy: int | None = None
    last_alert_event: str | None = None
    last_alert_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AlertSendLedger(SQLModel, table=True):
    """Compact durable evidence of successful sends, not a second listing payload."""

    id: int | None = Field(default=None, primary_key=True)
    source: str
    source_listing_id: str = Field(index=True)
    sent_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    signature: str
    route: str
    thesis: str | None = None
    event: str | None = None
    delivery: str | None = None
    price_jpy: int | None = None
    price_kind: str | None = None
    material_identity: str | None = None
    material_risk: str | None = None
    material_evidence: str | None = None


class ListingProtection(SQLModel, table=True):
    """Explicit human evidence is durable; it never overrides live eligibility."""

    __table_args__ = (UniqueConstraint("source", "source_listing_id"),)
    key: str = Field(primary_key=True)
    source: str
    source_listing_id: str = Field(index=True)
    pinned_at: datetime | None = None
    pin_reason: str | None = None
    feedback_label: str | None = None
    feedback_at: datetime | None = None
    feedback_provenance: str | None = None
    feedback_history_json: str | None = None
    purchased_at: datetime | None = None


class StatusIncidentState(SQLModel, table=True):
    """Persistent, single-row dedupe state for quiet operational notices."""

    id: int = Field(default=1, primary_key=True)
    outage_announced: bool = False
    auth_required_announced: bool = False
    auth_failure_streak: int = 0
    release_keys_json: str = "[]"
    # Nullable additive incident timing keeps short self-healing source stalls
    # internal while allowing a genuinely prolonged outage to survive restarts.
    source_failure_started_at: datetime | None = None
    last_outage_announced_at: datetime | None = None
    last_recovery_announced_at: datetime | None = None
    recovery_success_streak: int | None = 0


def init_db(path: str | Path = DB_PATH) -> None:
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        SQLModel.metadata.create_all(engine)
    except OperationalError as exc:
        if "already exists" not in str(exc).lower():
            raise
    with engine.begin() as connection:
        existing_tables = set(inspect(connection).get_table_names())
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_columns = {
                column["name"] for column in inspect(connection).get_columns(table.name)
            }
            for column in table.columns:
                if column.name in existing_columns or column.primary_key:
                    continue
                column_type = column.type.compile(dialect=engine.dialect)
                connection.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}')
                )


def get_session(path: str | Path = DB_PATH) -> Session:
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{database_path}")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def save_listings(listings: list[Any], path: str | Path = DB_PATH) -> tuple[int, int]:
    if not listings:
        return 0, 0

    init_db(path)
    session = get_session(path)
    created = 0
    updated = 0
    try:
        for row in listings:
            if not hasattr(row, "source_listing_id"):
                continue
            source = getattr(row, "source", "unknown")
            source_listing_id = getattr(row, "source_listing_id", None)
            if not source or not source_listing_id:
                continue
            existing = session.exec(
                select(Listing).where(
                    Listing.source == source, Listing.source_listing_id == source_listing_id
                )
            ).first()
            values = {
                "url": getattr(row, "url", None),
                "title_original": getattr(row, "title_original", None),
                "title_normalized": getattr(row, "title_normalized", None),
                "brand": getattr(row, "brand", None),
                "category": getattr(row, "category", None),
                "current_price": getattr(row, "current_price", None),
                "buyout_price": getattr(row, "buyout_price", None),
                "starting_price": getattr(row, "starting_price", None),
                "time_remaining": getattr(row, "time_remaining", None),
                "buyout_indicator": getattr(row, "buyout_indicator", None),
                "bid_count": getattr(row, "bid_count", None),
                "watch_count": getattr(row, "watch_count", None),
                "image_url": getattr(row, "image_url", None),
                "desirability_score": getattr(row, "desirability_score", None),
                "opportunity_score": getattr(row, "opportunity_score", None),
                "auth_risk": getattr(row, "auth_risk", None),
                "comp_confidence": getattr(row, "comp_confidence", None),
            }
            if existing is None:
                session.add(Listing(source=source, source_listing_id=source_listing_id, **values))
                created += 1
            else:
                for field_name, value in values.items():
                    if field_name == "image_url" and value is None and (
                        (getattr(row, "raw", None) or {}).get("image_integrity") or {}
                    ).get("clear_persisted") is True:
                        # Explicit acquisition integrity decision, not a generic
                        # partial update: do not retain a rejected old thumbnail.
                        existing.image_url = None
                        continue
                    if value is not None and (
                        not isinstance(value, str) or bool(value.strip())
                    ):
                        setattr(existing, field_name, value)
                updated += 1
        session.commit()
    finally:
        session.close()
    return created, updated


def get_alert_state(
    source: str, source_listing_id: str, path: str | Path = DB_PATH
) -> AlertState | None:
    with get_session(path) as session:
        return session.exec(
            select(AlertState).where(
                AlertState.source == source,
                AlertState.source_listing_id == source_listing_id,
            )
        ).first()


def save_alert_state(
    source: str,
    source_listing_id: str,
    signature: str,
    route: str,
    path: str | Path = DB_PATH,
    *,
    thesis: str | None = None,
    material_price_jpy: int | None = None,
    material_price_kind: str | None = None,
    material_maturity_state: str | None = None,
    material_identity: str | None = None,
    material_risk_state: str | None = None,
    material_evidence_state: str | None = None,
    verified_median_jpy: int | None = None,
    alert_event: str | None = None,
    delivery: str | None = None,
) -> None:
    material_supplied = material_identity is not None
    with get_session(path) as session:
        state = session.exec(
            select(AlertState).where(
                AlertState.source == source,
                AlertState.source_listing_id == source_listing_id,
            )
        ).first()
        if state is None:
            state = AlertState(
                source=source,
                source_listing_id=source_listing_id,
                signature=signature,
                route=route,
                thesis=thesis if material_supplied else None,
                material_state_version=1 if material_supplied else None,
                last_material_price_jpy=material_price_jpy,
                last_material_price_kind=material_price_kind,
                last_material_maturity_state=material_maturity_state,
                last_material_identity=material_identity,
                last_material_risk_state=material_risk_state,
                last_material_evidence_state=material_evidence_state,
                last_verified_median_jpy=verified_median_jpy,
                last_alert_event=alert_event,
            )
            session.add(state)
        else:
            state.signature = signature
            state.route = route
            if material_supplied:
                state.thesis = thesis
                state.material_state_version = 1
                state.last_material_price_jpy = material_price_jpy
                state.last_material_price_kind = material_price_kind
                state.last_material_maturity_state = material_maturity_state
                state.last_material_identity = material_identity
                state.last_material_risk_state = material_risk_state
                state.last_material_evidence_state = material_evidence_state
                state.last_verified_median_jpy = verified_median_jpy
                state.last_alert_event = alert_event
            state.last_alert_at = datetime.now(UTC)
        session.add(AlertSendLedger(
            source=source,
            source_listing_id=source_listing_id,
            signature=signature,
            route=route,
            thesis=thesis,
            event=alert_event,
            delivery=delivery,
            price_jpy=material_price_jpy,
            price_kind=material_price_kind,
            material_identity=material_identity,
            material_risk=material_risk_state,
            material_evidence=material_evidence_state,
        ))
        session.commit()


def backfill_alert_material_state(
    source: str,
    source_listing_id: str,
    path: str | Path = DB_PATH,
    *,
    thesis: str,
    material_price_jpy: int | None,
    material_price_kind: str | None,
    material_maturity_state: str,
    material_identity: str,
    material_risk_state: str,
    material_evidence_state: str,
    verified_median_jpy: int | None,
) -> bool:
    """Acknowledge legacy alerts at their current state without changing alert time."""
    with get_session(path) as session:
        state = session.exec(
            select(AlertState).where(
                AlertState.source == source,
                AlertState.source_listing_id == source_listing_id,
            )
        ).first()
        if state is None or state.material_state_version is not None:
            return False
        state.thesis = thesis
        state.material_state_version = 1
        state.last_material_price_jpy = material_price_jpy
        state.last_material_price_kind = material_price_kind
        state.last_material_maturity_state = material_maturity_state
        state.last_material_identity = material_identity
        state.last_material_risk_state = material_risk_state
        state.last_material_evidence_state = material_evidence_state
        state.last_verified_median_jpy = verified_median_jpy
        state.last_alert_event = "legacy_state_backfill"
        session.commit()
        return True
