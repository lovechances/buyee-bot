from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml

from .normalize import normalize_text
from .settings import CONFIG_DIR

TAXONOMY_PATH = CONFIG_DIR / "fashion_taxonomy.yaml"


@dataclass(frozen=True)
class Evidence:
    value: str
    confidence: float
    provenance: str
    matched_term: str


@dataclass
class FashionProfile:
    brand: Evidence | None = None
    designer: Evidence | None = None
    era: Evidence | None = None
    collection: Evidence | None = None
    item_type: Evidence | None = None
    category: Evidence | None = None
    material: Evidence | None = None
    season: Evidence | None = None
    size: Evidence | None = None
    color: Evidence | None = None
    collaboration: Evidence | None = None
    positive_signals: list[Evidence] = field(default_factory=list)
    negative_signals: list[Evidence] = field(default_factory=list)
    auth_sensitive: bool = False


@lru_cache(maxsize=1)
def load_taxonomy() -> dict[str, Any]:
    return yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8")) or {}


def _first_match(text: str, mapping: dict[str, list[str]], provenance: str) -> Evidence | None:
    for value, terms in mapping.items():
        for term in terms:
            if str(term).lower() in text:
                return Evidence(value, 0.9, provenance, str(term))
    return None


def enrich_title(title: str, hinted_brand: str | None = None) -> FashionProfile:
    taxonomy = load_taxonomy()
    text = normalize_text(f"{title} {hinted_brand or ''}").lower()
    profile = FashionProfile()
    matched_brand: dict[str, Any] | None = None

    for brand_key, record in taxonomy.get("brands", {}).items():
        match = next((str(alias) for alias in record.get("aliases", []) if str(alias).lower() in text), None)
        if not match:
            continue
        profile.brand = Evidence(record["canonical"], 0.98, f"taxonomy.brands.{brand_key}", match)
        matched_brand = record
        if record.get("designer"):
            profile.designer = Evidence(record["designer"], 0.9, f"taxonomy.brands.{brand_key}", match)
        profile.auth_sensitive = bool(record.get("auth_sensitive", False))
        for era, terms in record.get("eras", {}).items():
            era_match = next((str(term) for term in terms if str(term).lower() in text), None)
            if era_match:
                profile.era = Evidence(era, 0.8, f"taxonomy.brands.{brand_key}.eras", era_match)
                break
        collection_matches: list[tuple[bool, int, dict[str, Any], str]] = []
        for collection in record.get("collections", []):
            for term in collection.get("terms", []):
                term_text = str(term)
                if term_text.lower() in text:
                    is_season_only = bool(re.fullmatch(r"(?i)(?:aw|fw|ss)\d{2}|\d{2}(?:aw|fw|ss)", term_text))
                    collection_matches.append((not is_season_only, len(term_text), collection, term_text))
        if collection_matches:
            _, _, collection, collection_match = max(collection_matches, key=lambda item: (item[0], item[1]))
            profile.collection = Evidence(
                collection["name"], 0.9, f"taxonomy.brands.{brand_key}.collections", collection_match
            )
        for term in record.get("strong_signals", []):
            if str(term).lower() in text:
                profile.positive_signals.append(
                    Evidence(str(term), 0.85, f"taxonomy.brands.{brand_key}.strong_signals", str(term))
                )
        for term in record.get("generic_signals", []):
            if str(term).lower() in text:
                profile.negative_signals.append(
                    Evidence(str(term), 0.85, f"taxonomy.brands.{brand_key}.generic_signals", str(term))
                )
        break

    profile.item_type = _first_match(text, taxonomy.get("item_types", {}), "taxonomy.item_types")
    profile.material = _first_match(text, taxonomy.get("materials", {}), "taxonomy.materials")
    season_match = re.search(r"(?i)\b((?:aw|fw|ss)\s?\d{2}|\d{2}\s?(?:aw|fw|ss))\b", text)
    if season_match:
        profile.season = Evidence(season_match.group(1).upper().replace(" ", ""), 0.95, "title.regex.season", season_match.group(0))
    size_match = re.search(r"(?i)(?:size|サイズ)\s*[:：]?\s*(xs|s|m|l|xl|xxl|[0-5]|\d{2})\b", text)
    if size_match:
        profile.size = Evidence(size_match.group(1).upper(), 0.85, "title.regex.size", size_match.group(0))
    colors = {
        "black": ["black", "ブラック", "黒"],
        "white": ["white", "ホワイト", "白"],
        "red": ["red", "レッド", "赤"],
        "blue": ["blue", "ブルー", "青"],
        "brown": ["brown", "ブラウン", "茶"],
    }
    profile.color = _first_match(text, colors, "title.colors")
    collaboration_match = re.search(r"(?i)\s(?:x|×)\s|コラボ", text)
    if collaboration_match:
        profile.collaboration = Evidence("collaboration", 0.8, "title.collaboration", collaboration_match.group(0).strip())
    if profile.item_type:
        category = "jewelry" if profile.item_type.value == "jewelry" else (
            "accessory" if profile.item_type.value in {"accessory", "footwear"} else "menswear"
        )
        profile.category = Evidence(category, 0.9, "derived.item_type", profile.item_type.value)

    for group, terms in taxonomy.get("negative_terms", {}).items():
        for term in terms:
            if str(term).lower() in text:
                profile.negative_signals.append(
                    Evidence(f"{group}:{term}", 0.95, f"taxonomy.negative_terms.{group}", str(term))
                )
    if matched_brand is None and hinted_brand and hinted_brand != "unknown":
        profile.brand = Evidence(hinted_brand, 0.55, "marketplace_hint", hinted_brand)
    return profile


def expand_discovery_terms(brand_key: str) -> list[dict[str, str]]:
    record = load_taxonomy().get("brands", {}).get(brand_key, {})
    terms: list[dict[str, str]] = []
    for alias in record.get("aliases", []):
        terms.append({"term": str(alias), "kind": "alias", "provenance": f"taxonomy.brands.{brand_key}"})
    for collection in record.get("collections", []):
        for term in collection.get("terms", []):
            terms.append({"term": str(term), "kind": "collection", "provenance": f"taxonomy.brands.{brand_key}.collections"})
    for term in record.get("strong_signals", []):
        terms.append({"term": str(term), "kind": "signal", "provenance": f"taxonomy.brands.{brand_key}.strong_signals"})
    return terms
