from __future__ import annotations

from pathlib import Path

from sqlmodel import Field, Session, SQLModel, create_engine

DB_PATH = Path("data") / "buyeeye.db"


class Listing(SQLModel, table=True):
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
    recommended_max_bid_jpy: int | None = None
    taste_score: int | None = None
    overall_score: int | None = None
    decision: str | None = None
    is_alerted: bool = False


def init_db(path: str | Path = DB_PATH) -> None:
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{database_path}")
    SQLModel.metadata.create_all(engine)


def get_session(path: str | Path = DB_PATH) -> Session:
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{database_path}")
    return Session(engine)
