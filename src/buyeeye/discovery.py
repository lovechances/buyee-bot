from __future__ import annotations

from typing import Any

from .normalize import normalize_text


def get_search_families(config: dict[str, Any]) -> list[dict[str, Any]]:
    if not config:
        return []
    families = config.get("search_families", [])
    return [dict(item) for item in families]


def generate_search_queries(config: dict[str, Any]) -> list[str]:
    queries: list[str] = []
    for family in get_search_families(config):
        brand = family.get("brand", "")
        aliases = family.get("aliases", [])
        terms = family.get("terms", [])
        for alias in aliases:
            queries.append(f"{alias}")
            for term in terms:
                queries.append(f"{alias} {term}")
        if brand:
            for term in terms:
                queries.append(f"{brand} {term}")
    return sorted({normalize_text(q) for q in queries if q})


def dedupe_listings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        key = item.get("source_listing_id") or item.get("url") or item.get("title_original")
        if not key:
            continue
        k = str(key).strip().lower()
        if k in seen:
            continue
        seen.add(k)
        unique.append(item)
    return unique
