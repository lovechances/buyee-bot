from __future__ import annotations

from pathlib import Path
from typing import Any

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


def save_listings(listings: list[Any], path: str | Path = DB_PATH) -> int:
    if not listings:
        return 0

    session = get_session(path)
    saved = 0
    try:
        for row in listings:
            if hasattr(row, "source_listing_id"):
                source = getattr(row, "source", "unknown")
                source_listing_id = getattr(row, "source_listing_id", None)
                url = getattr(row, "url", None)
                title_original = getattr(row, "title_original", None)
                title_normalized = getattr(row, "title_normalized", None)
                brand = getattr(row, "brand", None)
                category = getattr(row, "category", None)
                current_price = getattr(row, "current_price", None)
                buyout_price = getattr(row, "buyout_price", None)
                starting_price = getattr(row, "starting_price", None)
                raw = getattr(row, "raw", None)
                session.add(
                    Listing(
                        source=source,
                        source_listing_id=source_listing_id,
                        url=url,
                        title_original=title_original,
                        title_normalized=title_normalized,
                        brand=brand,
                        category=category,
                        current_price=current_price,
                        buyout_price=buyout_price,
                        starting_price=starting_price,
                    )
                )
                saved += 1
        session.commit()
    finally:
        session.close()
    return saved
