from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .normalize import normalize_text


@dataclass(frozen=True)
class QuerySpec:
    query: str
    provenance: str
    family: str
    priority: int
    cooldown_minutes: int = 180
    discovery_mode: str = "direct_piece"


def _discovery_mode(provenance: str) -> str:
    if ".collection" in provenance or ".designer" in provenance:
        return "collection_era"
    if ".strong_signal" in provenance:
        return "hidden_mislabeled"
    return "direct_piece"


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


def build_query_universe(
    search_config: dict[str, Any], taxonomy: dict[str, Any]
) -> list[QuerySpec]:
    """Build a controlled discovery set; deliberately avoids Cartesian combinations."""
    candidates: list[QuerySpec] = []
    for family in get_search_families(search_config):
        brand = normalize_text(str(family.get("brand") or "unknown"))
        cooldown = int(family.get("cooldown_minutes", 180))
        priority = int(family.get("priority", 70))
        aliases = [normalize_text(str(value)) for value in family.get("aliases", []) if value]
        terms = [normalize_text(str(value)) for value in family.get("terms", []) if value]
        for alias in aliases[:4]:
            candidates.append(
                QuerySpec(alias, "searches.alias", brand, priority, cooldown, "direct_piece")
            )
        anchor = aliases[0] if aliases else brand
        for term in terms[:10]:
            candidates.append(
                QuerySpec(
                    normalize_text(f"{anchor} {term}"),
                    "searches.curated_term",
                    brand,
                    priority + 5,
                    cooldown,
                    "direct_piece",
                )
            )

    for key, brand in taxonomy.get("brands", {}).items():
        canonical = normalize_text(str(brand.get("canonical") or key))
        aliases = [normalize_text(str(value)) for value in brand.get("aliases", []) if value]
        provenance = f"taxonomy.brands.{key}.canonical"
        candidates.append(
            QuerySpec(canonical, provenance, key, 80, 240, _discovery_mode(provenance))
        )
        for alias in aliases[:4]:
            if alias.casefold() != canonical.casefold():
                priority = 78 if any(ord(char) > 127 for char in alias) else 65
                candidates.append(
                    QuerySpec(
                        alias,
                        f"taxonomy.brands.{key}.alias",
                        key,
                        priority,
                        240,
                        "direct_piece",
                    )
                )
        designer = brand.get("designer")
        if designer:
            candidates.append(
                QuerySpec(
                    normalize_text(f"{canonical} {designer}"),
                    f"taxonomy.brands.{key}.designer",
                    key,
                    72,
                    360,
                    "collection_era",
                )
            )
        for collection in brand.get("collections", [])[:8]:
            name = normalize_text(str(collection.get("name") or ""))
            if name:
                candidates.append(
                    QuerySpec(
                        normalize_text(f"{canonical} {name}"),
                        f"taxonomy.brands.{key}.collection",
                        key,
                        90,
                        360,
                        "collection_era",
                    )
                )
            japanese_term = next(
                (
                    normalize_text(str(term))
                    for term in collection.get("terms", [])
                    if any(ord(char) > 127 for char in str(term))
                ),
                None,
            )
            if japanese_term:
                candidates.append(
                    QuerySpec(
                        normalize_text(f"{canonical} {japanese_term}"),
                        f"taxonomy.brands.{key}.collection_alias",
                        key,
                        88,
                        360,
                        "collection_era",
                    )
                )
        for signal in brand.get("strong_signals", [])[:5]:
            candidates.append(
                QuerySpec(
                    normalize_text(f"{canonical} {signal}"),
                    f"taxonomy.brands.{key}.strong_signal",
                    key,
                    75,
                    300,
                    "hidden_mislabeled",
                )
            )

    best: dict[str, QuerySpec] = {}
    for spec in candidates:
        key = spec.query.casefold()
        if spec.query and (key not in best or spec.priority > best[key].priority):
            best[key] = spec
    return sorted(best.values(), key=lambda spec: (-spec.priority, spec.family, spec.query))


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
