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
    last_alert_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
) -> None:
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
            )
            session.add(state)
        else:
            state.signature = signature
            state.route = route
            state.last_alert_at = datetime.now(UTC)
        session.commit()
