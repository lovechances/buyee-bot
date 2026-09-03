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


@dataclass(frozen=True)
class PieceRelationship:
    """Inferred relationship between the observed piece and the matched fashion house."""

    value: str
    confidence: float
    provenance: str
    matched_term: str
    participants: tuple[str, ...] = ()


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
    relationship: PieceRelationship | None = None
    positive_signals: list[Evidence] = field(default_factory=list)
    negative_signals: list[Evidence] = field(default_factory=list)
    auth_sensitive: bool = False


@lru_cache(maxsize=1)
def load_taxonomy() -> dict[str, Any]:
    return yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8")) or {}


def _term_matches(text: str, term: str) -> bool:
    """Match Latin tokens/phrases on boundaries while preserving Japanese substring matching."""
    needle = normalize_text(str(term)).casefold()
    if not needle:
        return False
    # Taxonomy season shorthands intentionally overlap forms such as 2001SS:
    # both the era year `2001` and collection shorthand `01ss` are evidence.
    if re.fullmatch(r"(?:\d{4}|\d{2}(?:ss|aw|fw)|(?:ss|aw|fw)\d{2})", needle):
        return needle in text
    if any(character.isascii() and character.isalnum() for character in needle):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
                text,
                flags=re.IGNORECASE,
            )
        )
    return needle in text


def _first_match(text: str, mapping: dict[str, list[str]], provenance: str) -> Evidence | None:
    matches = [
        (len(normalize_text(str(term))), value, str(term))
        for value, terms in mapping.items()
        for term in terms
        if _term_matches(text, str(term))
    ]
    if not matches:
        return None
    _, value, term = max(matches, key=lambda match: match[0])
    return Evidence(value, 0.9, provenance, term)


def _longest_term_match(text: str, terms: list[str]) -> str | None:
    matches = [str(term) for term in terms if _term_matches(text, str(term))]
    return max(matches, key=lambda term: len(normalize_text(term)), default=None)


def _collaboration_participants(term: str) -> tuple[str, ...]:
    """Normalize an explicit two-party collaboration without guessing partners."""
    normalized = normalize_text(term).casefold()
    if "×" in normalized:
        parts = re.split(r"\s*×\s*", normalized, maxsplit=1)
    else:
        parts = re.split(r"\s+x\s+", normalized, maxsplit=1)
    cleaned = tuple(
        part
        for value in parts
        if (part := re.sub(r"^[\W_]+|[\W_]+$", "", normalize_text(value)))
    )
    if len(cleaned) != 2 or cleaned[0] == cleaned[1]:
        return ()
    return tuple(sorted(cleaned))


def _find_brand_collaboration_match(
    text: str, record: dict[str, Any]
) -> re.Match[str] | None:
    """Return only an explicit collaboration that actually names the matched house.

    Marketplace titles commonly contain dimensions and variants such as
    ``48 x black``.  Treating every token around an ``x`` as a collaboration
    corrupts relationship ranking and comp matching, so one participant must
    share a meaningful token with a configured brand alias.
    """
    alias_tokens = {
        token
        for alias in (*record.get("aliases", []), *record.get("recognition_aliases", []))
        for token in re.findall(r"[^\W_]+", normalize_text(str(alias)).casefold())
        if len(token) >= 2
    }
    pattern = re.compile(
        r"(?i)(?:[^\s×]+\s+(?:x|×)\s+[^\s×]+|[^\s×]+×[^\s×]+)"
    )
    for match in pattern.finditer(text):
        participants = _collaboration_participants(match.group(0))
        if not participants:
            continue
        participant_tokens = {
            token
            for participant in participants
            for token in re.findall(r"[^\W_]+", participant.casefold())
            if len(token) >= 2
        }
        if alias_tokens & participant_tokens:
            return match
    return None


def _classify_relationship(
    text: str,
    *,
    brand_key: str,
    record: dict[str, Any],
    matched_term: str,
    matched_core_alias: bool,
    collaboration_match: re.Match[str] | None,
) -> PieceRelationship:
    """Classify only relationships evidenced by title/detail text or taxonomy."""
    global_relationships = load_taxonomy().get("relationship_terms", {})
    homage_match = _longest_term_match(
        text,
        [str(term) for term in global_relationships.get("homage_reference", [])],
    )
    if homage_match:
        return PieceRelationship(
            "homage_reference",
            0.95,
            "taxonomy.relationship_terms.homage_reference",
            homage_match,
        )

    relationships = record.get("relationships", {})
    diffusion_match = _longest_term_match(
        text,
        [str(term) for term in relationships.get("diffusion", [])],
    )
    if diffusion_match:
        return PieceRelationship(
            "diffusion",
            0.95,
            f"taxonomy.brands.{brand_key}.relationships.diffusion",
            diffusion_match,
        )

    low_priority_collaboration_match = _longest_term_match(
        text,
        [str(term) for term in relationships.get("collaboration_low_priority", [])],
    )
    if low_priority_collaboration_match:
        return PieceRelationship(
            "collaboration",
            0.9,
            f"taxonomy.brands.{brand_key}.relationships.collaboration_low_priority",
            low_priority_collaboration_match,
            _collaboration_participants(low_priority_collaboration_match),
        )
    if collaboration_match:
        matched_collaboration = collaboration_match.group(0).strip()
        return PieceRelationship(
            "collaboration",
            0.75,
            "title.relationship.collaboration",
            matched_collaboration,
            _collaboration_participants(matched_collaboration),
        )
    if brand_key == "vintage_band_tees":
        # A recognized band subject is not a mainline fashion-house
        # relationship. Production/tag/era evidence must establish the value
        # of the specific tee later in evaluation.
        return PieceRelationship(
            "uncertain",
            0.7,
            "taxonomy.brands.vintage_band_tees.subject_identity",
            matched_term,
        )
    if matched_core_alias:
        return PieceRelationship(
            "core",
            0.9,
            f"taxonomy.brands.{brand_key}.aliases",
            matched_term,
        )
    return PieceRelationship(
        "uncertain",
        0.6,
        f"taxonomy.brands.{brand_key}.recognition_aliases",
        matched_term,
    )


def is_generic_signal(
    signal: Evidence,
    relationship: PieceRelationship | None = None,
) -> bool:
    """Recognize global and brand-specific generic evidence consistently.

    Some older taxonomy entries also name a relationship marker as a generic
    signal. A declared diffusion/collaboration relationship is handled by its
    own ranking prior instead of becoming a blanket generic rejection.
    """
    if (
        relationship
        and relationship.value in {"diffusion", "collaboration"}
        and (
            normalize_text(signal.matched_term).casefold()
            == normalize_text(relationship.matched_term).casefold()
            or (
                relationship.value == "diffusion"
                and normalize_text(signal.value).casefold() == "diffusion"
            )
        )
    ):
        return False
    return bool(
        signal.value.startswith("generic:")
        or signal.provenance.endswith(".generic_signals")
        or signal.value in {"basic", "logo tee"}
    )


def enrich_title(title: str, hinted_brand: str | None = None) -> FashionProfile:
    taxonomy = load_taxonomy()
    text = normalize_text(title).casefold()
    profile = FashionProfile()
    matched_brand: dict[str, Any] | None = None
    collaboration_match: re.Match[str] | None = None

    for brand_key, record in taxonomy.get("brands", {}).items():
        core_aliases = [str(alias) for alias in record.get("aliases", [])]
        recognition_aliases = [
            str(alias) for alias in record.get("recognition_aliases", [])
        ]
        recognition_terms = [*core_aliases, *recognition_aliases]
        match = next(
            (str(alias) for alias in recognition_terms if _term_matches(text, str(alias))),
            None,
        )
        if not match:
            continue
        profile.brand = Evidence(record["canonical"], 0.98, f"taxonomy.brands.{brand_key}", match)
        matched_brand = record
        collaboration_match = _find_brand_collaboration_match(text, record)
        profile.relationship = _classify_relationship(
            text,
            brand_key=brand_key,
            record=record,
            matched_term=match,
            matched_core_alias=match in core_aliases,
            collaboration_match=collaboration_match,
        )
        inherits_mainline_context = profile.relationship.value in {"core", "collaboration"} and not (
            profile.relationship.provenance.endswith(".collaboration_low_priority")
        )
        if record.get("designer") and inherits_mainline_context:
            profile.designer = Evidence(
                record["designer"], 0.9, f"taxonomy.brands.{brand_key}", match
            )
        profile.auth_sensitive = bool(record.get("auth_sensitive", False))
        if inherits_mainline_context:
            for era, terms in record.get("eras", {}).items():
                era_match = next(
                    (str(term) for term in terms if _term_matches(text, str(term))),
                    None,
                )
                if era_match:
                    profile.era = Evidence(
                        era, 0.8, f"taxonomy.brands.{brand_key}.eras", era_match
                    )
                    break
            collection_matches: list[tuple[bool, int, dict[str, Any], str]] = []
            for collection in record.get("collections", []):
                for term in collection.get("terms", []):
                    term_text = str(term)
                    if _term_matches(text, term_text):
                        is_season_only = bool(
                            re.fullmatch(
                                r"(?i)(?:aw|fw|ss)\d{2}|\d{2}(?:aw|fw|ss)",
                                term_text,
                            )
                        )
                        collection_matches.append(
                            (not is_season_only, len(term_text), collection, term_text)
                        )
            if collection_matches:
                _, _, collection, collection_match = max(
                    collection_matches, key=lambda item: (item[0], item[1])
                )
                profile.collection = Evidence(
                    collection["name"],
                    0.9,
                    f"taxonomy.brands.{brand_key}.collections",
                    collection_match,
                )
        if inherits_mainline_context:
            for term in record.get("strong_signals", []):
                if _term_matches(text, str(term)):
                    profile.positive_signals.append(
                        Evidence(
                            str(term),
                            0.85,
                            f"taxonomy.brands.{brand_key}.strong_signals",
                            str(term),
                        )
                    )
        for term in record.get("generic_signals", []):
            if _term_matches(text, str(term)):
                profile.negative_signals.append(
                    Evidence(
                        str(term), 0.85, f"taxonomy.brands.{brand_key}.generic_signals", str(term)
                    )
                )
        break

    profile.item_type = _first_match(text, taxonomy.get("item_types", {}), "taxonomy.item_types")
    profile.material = _first_match(text, taxonomy.get("materials", {}), "taxonomy.materials")
    season_match = re.search(r"(?i)\b((?:aw|fw|ss)\s?\d{2}|\d{2}\s?(?:aw|fw|ss))\b", text)
    if season_match:
        profile.season = Evidence(
            season_match.group(1).upper().replace(" ", ""),
            0.95,
            "title.regex.season",
            season_match.group(0),
        )
    size_match = re.search(r"(?i)(?:size|サイズ)\s*[:：]?\s*(xs|s|m|l|xl|xxl|[0-5]|\d{2})\b", text)
    if size_match:
        profile.size = Evidence(
            size_match.group(1).upper(), 0.85, "title.regex.size", size_match.group(0)
        )
    colors = {
        "black": ["black", "ブラック", "黒"],
        "white": ["white", "ホワイト", "白"],
        "red": ["red", "レッド", "赤"],
        "blue": ["blue", "ブルー", "青"],
        "brown": ["brown", "ブラウン", "茶"],
    }
    profile.color = _first_match(text, colors, "title.colors")
    if collaboration_match:
        profile.collaboration = Evidence(
            "collaboration", 0.8, "title.collaboration", collaboration_match.group(0).strip()
        )
    if profile.item_type:
        category = (
            "jewelry"
            if profile.item_type.value == "jewelry"
            else (
                "accessory"
                if profile.item_type.value in {"accessory", "bag", "belt", "wallet", "footwear"}
                else "menswear"
            )
        )
        profile.category = Evidence(category, 0.9, "derived.item_type", profile.item_type.value)

    for group, terms in taxonomy.get("negative_terms", {}).items():
        for term in terms:
            # Maison Margiela's named Replica Line is legitimate house
            # nomenclature. Remove only that phrase before risk matching so an
            # additional standalone "replica" (or any other fake wording)
            # remains a veto.
            legitimate_margiela_replica_line = bool(
                group == "counterfeit"
                and str(term).casefold() == "replica"
                and profile.brand
                and profile.brand.value == "Maison Martin Margiela"
                and _term_matches(text, "replica line")
            )
            risk_text = (
                re.sub(
                    r"(?i)(?<![a-z0-9])replica\s+line(?![a-z0-9])",
                    " ",
                    text,
                )
                if legitimate_margiela_replica_line
                else text
            )
            if _term_matches(risk_text, str(term)):
                profile.negative_signals.append(
                    Evidence(f"{group}:{term}", 0.95, f"taxonomy.negative_terms.{group}", str(term))
                )
    if matched_brand is None and hinted_brand and hinted_brand != "unknown":
        profile.brand = Evidence(hinted_brand, 0.55, "marketplace_hint", hinted_brand)
        profile.relationship = PieceRelationship(
            "uncertain", 0.55, "marketplace_hint", hinted_brand
        )
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
