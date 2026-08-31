from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import Field, SQLModel, UniqueConstraint, select

from .comp_sources import SoldCompSource, build_comp_queries, query_key
from .db import DB_PATH, get_session, init_db
from .fashion import FashionProfile
from .models import Comp


class CompSearchCache(SQLModel, table=True):
    key: str = Field(primary_key=True)
    source: str
    query: str
    retrieved_at: datetime
    expires_at: datetime


class SoldCompCache(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("query_key", "source", "source_listing_id", name="uq_comp_query_source_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    query_key: str = Field(index=True)
    source: str
    source_listing_id: str
    query: str
    title: str
    url: str | None = None
    original_price: float | None = None
    original_currency: str | None = None
    shipping_original: float | None = None
    normalized_price_jpy: int
    sold_at: datetime | None = None
    condition: str | None = None
    size: str | None = None
    matching_features_json: str = "{}"
    retrieved_at: datetime


def _as_comp(row: SoldCompCache, now: datetime) -> Comp:
    sold_at = row.sold_at
    if sold_at and sold_at.tzinfo is None:
        sold_at = sold_at.replace(tzinfo=UTC)
    recency = max(0, (now - sold_at).days) if sold_at else 180
    return Comp(
        source=row.source,
        title=row.title,
        price_jpy=row.normalized_price_jpy,
        similarity=1.0,
        recency_days=recency,
        is_sold=True,
        provenance=f"cache:{row.source}:{row.query}",
        source_listing_id=row.source_listing_id,
        url=row.url,
        query=row.query,
        sold_at=sold_at,
        original_price=row.original_price,
        original_currency=row.original_currency,
        shipping_original=row.shipping_original,
        condition=row.condition,
        size=row.size,
    )


class CompRepository:
    def __init__(self, db_path: str | Path = DB_PATH, ttl_hours: int = 72):
        self.db_path = Path(db_path)
        self.ttl = timedelta(hours=ttl_hours)

    def get_fresh(self, source: str, query: str, now: datetime | None = None) -> list[Comp] | None:
        init_db(self.db_path)
        current = now or datetime.now(UTC)
        key = query_key(source, query)
        with get_session(self.db_path) as session:
            search = session.get(CompSearchCache, key)
            if search is None:
                return None
            expires = search.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires <= current:
                return None
            rows = session.exec(select(SoldCompCache).where(SoldCompCache.query_key == key)).all()
            return [_as_comp(row, current) for row in rows]

    def put(self, source: str, query: str, comps: list[Comp], now: datetime | None = None) -> None:
        init_db(self.db_path)
        current = now or datetime.now(UTC)
        key = query_key(source, query)
        with get_session(self.db_path) as session:
            search = session.get(CompSearchCache, key)
            if search is None:
                session.add(CompSearchCache(key=key, source=source, query=query, retrieved_at=current, expires_at=current + self.ttl))
            else:
                search.retrieved_at = current
                search.expires_at = current + self.ttl
            for comp in comps:
                if not comp.source_listing_id or not comp.price_jpy:
                    continue
                existing = session.exec(
                    select(SoldCompCache).where(
                        SoldCompCache.query_key == key,
                        SoldCompCache.source == source,
                        SoldCompCache.source_listing_id == comp.source_listing_id,
                    )
                ).first()
                values = {
                    "query": query,
                    "title": comp.title,
                    "url": comp.url,
                    "original_price": comp.original_price,
                    "original_currency": comp.original_currency,
                    "shipping_original": comp.shipping_original,
                    "normalized_price_jpy": comp.price_jpy,
                    "sold_at": comp.sold_at,
                    "condition": comp.condition,
                    "size": comp.size,
                    "matching_features_json": json.dumps({"query": query}),
                    "retrieved_at": current,
                }
                if existing is None:
                    session.add(SoldCompCache(query_key=key, source=source, source_listing_id=comp.source_listing_id, **values))
                else:
                    for field_name, value in values.items():
                        setattr(existing, field_name, value)
            session.commit()


class CompService:
    def __init__(self, sources: list[SoldCompSource], repository: CompRepository):
        self.sources = sources
        self.repository = repository
        self.cache_hits = 0
        self.cache_misses = 0

    async def retrieve(self, profile: FashionProfile, minimum_candidates: int = 5) -> list[Comp]:
        collected: list[Comp] = []
        seen: set[tuple[str, str | None]] = set()
        for query in build_comp_queries(profile):
            for source in self.sources:
                records = self.repository.get_fresh(source.name, query)
                if records is None:
                    self.cache_misses += 1
                    records = await source.fetch(query)
                    self.repository.put(source.name, query, records)
                else:
                    self.cache_hits += 1
                for comp in records:
                    identity = (comp.source, comp.source_listing_id)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    collected.append(comp)
            if len(collected) >= minimum_candidates:
                break
        return collected
