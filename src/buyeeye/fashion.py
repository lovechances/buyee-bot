from __future__ import annotations

import re
import unicodedata
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


@dataclass(frozen=True)
class ProductScope:
    """What the seller is offering, independently of a matched brand token."""

    target_role: str = "unknown"
    offer_kind: str = "product"
    audience: str = "unknown"
    offer_clarity: str = "clear"
    remaining_variant: str | None = None
    confidence: float = 0.6
    reasons: tuple[str, ...] = ()

    @property
    def hard_non_target(self) -> bool:
        return bool(
            self.target_role in {"style_reference", "contradicted"}
            or self.offer_kind in {"packaging_only", "media_ephemera", "non_fashion"}
            or self.audience in {"womens", "kids"}
        )

    @property
    def acquisition_attributable(self) -> bool:
        return bool(
            not self.hard_non_target
            and self.offer_clarity == "clear"
            and self.offer_kind != "packaging_only"
        )


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
    product_scope: ProductScope = field(default_factory=ProductScope)
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


def _brand_match(
    text: str, taxonomy: dict[str, Any]
) -> tuple[str, dict[str, Any], str, bool] | None:
    """Return the most specific configured target alias in the evidence text."""
    matches: list[tuple[int, bool, str, dict[str, Any], str]] = []
    for brand_key, record in taxonomy.get("brands", {}).items():
        core_aliases = [str(alias) for alias in record.get("aliases", [])]
        recognition_aliases = [
            str(alias) for alias in record.get("recognition_aliases", [])
        ]
        for alias in (*core_aliases, *recognition_aliases):
            if _term_matches(text, alias):
                matches.append(
                    (
                        len(normalize_text(alias)),
                        alias in core_aliases,
                        brand_key,
                        record,
                        alias,
                    )
                )
    if not matches:
        return None
    _, core_alias, brand_key, record, alias = max(
        matches, key=lambda match: (match[0], match[1])
    )
    return brand_key, record, alias, core_alias


def _brand_reference_match(text: str, matched_term: str) -> str | None:
    """Find language that syntactically makes the target a style reference."""
    relationships = load_taxonomy().get("relationship_terms", {})
    alias = normalize_text(matched_term).casefold()
    escaped = re.escape(alias)
    if any(character.isascii() and character.isalnum() for character in alias):
        alias_pattern = rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
    else:
        alias_pattern = escaped

    suffixes = [
        normalize_text(str(term)).casefold()
        for term in relationships.get("style_reference_suffixes", [])
    ]
    for suffix in sorted(suffixes, key=len, reverse=True):
        match = re.search(
            rf"{alias_pattern}\s*(?:[-:：/]\s*)?{re.escape(suffix)}",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return normalize_text(match.group(0))

    prefixes = [
        normalize_text(str(term)).casefold()
        for term in relationships.get("style_reference_prefixes", [])
    ]
    alias_match = re.search(alias_pattern, text, flags=re.IGNORECASE)
    if alias_match:
        for prefix in sorted(prefixes, key=len, reverse=True):
            for prefix_match in re.finditer(re.escape(prefix), text, flags=re.IGNORECASE):
                # Prefix constructions can include an offered object between
                # "homage" and a hashtagged target. Keep the window local.
                if 0 <= alias_match.start() - prefix_match.end() <= 64:
                    return normalize_text(prefix_match.group(0))

    for phrase in relationships.get("cultural_reference_phrases", []):
        normalized_phrase = normalize_text(str(phrase)).casefold()
        if _term_matches(text, normalized_phrase) and alias in normalized_phrase:
            return str(phrase)
    return None


_BRAND_OBSERVED_PLACEHOLDERS = {
    "-",
    "--",
    "n/a",
    "not specified",
    "other",
    "unknown",
    "その他",
    "不明",
}


def _identity_key(value: str | None) -> str:
    """Compare structured maker values despite harmless punctuation/accents."""
    decomposed = unicodedata.normalize("NFKD", normalize_text(value).casefold())
    unaccented = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"[^\w]+", "", unaccented)


def _observed_brand_matches_target(
    observed: str,
    matched_brand: Evidence,
    taxonomy: dict[str, Any],
) -> bool:
    """Allow explicit marketplace parent-house values without title broadening."""
    observed_match = _brand_match(observed, taxonomy)
    if observed_match and observed_match[1].get("canonical") == matched_brand.value:
        return True
    observed_key = _identity_key(observed)
    if observed_key == _identity_key(matched_brand.value):
        return True
    target_record = next(
        (
            record
            for record in taxonomy.get("brands", {}).values()
            if record.get("canonical") == matched_brand.value
        ),
        {},
    )
    return observed_key in {
        _identity_key(alias)
        for alias in target_record.get("observed_brand_aliases", [])
        if alias
    }


def _participant_matches_observed(
    observed: str, relationship: PieceRelationship | None
) -> bool:
    if not relationship or relationship.value != "collaboration":
        return False
    normalized = normalize_text(observed).casefold()
    return any(
        normalized == participant
        or _term_matches(normalized, participant)
        or _term_matches(participant, normalized)
        for participant in relationship.participants
        if participant
    )


def _scope_audience(text: str, category_hierarchy: tuple[str, ...]) -> tuple[str, str | None]:
    hierarchy = " ".join(normalize_text(value).casefold() for value in category_hierarchy)
    if re.search(
        r"(?i)(?:^|[ >/＞])(?:kids?|children|junior|キッズ|子供服|子ども)(?:$|[ >/＞])",
        hierarchy,
    ):
        return "kids", "detail category identifies kids inventory"
    if re.search(
        r"(?i)(?:^|[ >/＞])(?:women'?s?|ladies|レディース|女性用)(?:$|[ >/＞])",
        hierarchy,
    ):
        return "womens", "detail category identifies women's inventory"

    item_words = (
        r"jacket|coat|shirt|tee|t-?shirt|hoodie|sweater|pants|trousers|jeans|"
        r"boots?|shoes?|ジャケット|コート|シャツ|パーカー|ニット|パンツ|デニム|ブーツ"
        r"|tシャツ|ポロシャツ"
    )
    if re.search(
        rf"(?i)\b(?:kids?|children|junior)\b\s*(?:size\s*)?(?:\d{{2,3}}\s*)?(?:{item_words})",
        text,
    ):
        return "kids", "title grammatically scopes the product to kids"
    if re.search(
        rf"(?:キッズ|子供服|子ども|ジュニア)(?:用|サイズ)?\s*(?:\d{{2,3}}\s*)?(?:{item_words})",
        text,
    ):
        return "kids", "title grammatically scopes the product to kids"
    if re.search(r"(?i)\b(?:women'?s?|womens|ladies)\b|レディース|女性用", text):
        return "womens", "title identifies women's inventory"
    if re.search(
        r"(?i)(?:^|[ >/＞])(?:men'?s?|mens|メンズ|男性用)(?:$|[ >/＞])",
        hierarchy,
    ):
        return "mens", "detail category identifies men's inventory"
    return "unknown", None


def _partial_offer(text: str) -> tuple[str, str | None, str | None]:
    pairs = (
        (r"(?i)\bleft\b.{0,30}\b(?:sold|sold out)\b", "right"),
        (r"(?i)\bright\b.{0,30}\b(?:sold|sold out)\b", "left"),
        (r"左.{0,30}(?:売り切れ|売れました|販売済)", "right"),
        (r"右.{0,30}(?:売り切れ|売れました|販売済)", "left"),
    )
    multi_item_context = bool(
        re.search(
            r"(?i)\b(?:pair|set|two|2\s*(?:items?|pcs?)|both|"
            r"pictured\s+(?:items?|variants?)|variants?)\b|"
            r"(?:2点|二点|セット|左右|掲載(?:商品|品)|画像(?:の商品)?)",
            text,
        )
    )
    for pattern, remaining in pairs:
        sold_match = re.search(pattern, text)
        counterpart_available = bool(
            sold_match
            and re.search(
                rf"(?i)\b{remaining}\b.{{0,30}}\b"
                r"(?:remain(?:s|ing)?|available|included|for sale)\b",
                text,
            )
        )
        if sold_match and (multi_item_context or counterpart_available):
            return "partial", remaining, "one pictured/listed variant is already sold"
    if re.search(
        r"(?i)(?:one|one item|片方|一部|1点).{0,30}(?:sold|sold out|売り切れ|売れました|販売済)",
        text,
    ):
        return "ambiguous", None, "multi-item offer is only partially available"
    return "clear", None, None


def _media_ephemera_offer(
    scope_title: str,
    category_hierarchy: tuple[str, ...],
    terms: list[str],
) -> str | None:
    """Identify offered media without blacklisting media-inspired garments."""
    media_match = _longest_term_match(scope_title, terms)
    if not media_match:
        return None
    hierarchy = [normalize_text(value).casefold() for value in category_hierarchy]
    media_leaf_values = {
        "blu-ray",
        "bluray",
        "book",
        "books",
        "cd",
        "cds",
        "dvd",
        "dvds",
        "magazine",
        "magazines",
        "media",
        "music",
        "vhs",
        "video",
        "ブルーレイ",
        "メディア",
        "音楽",
        "映像",
        "本",
        "書籍",
        "雑誌",
    }
    explicit_media_category = bool(
        hierarchy and hierarchy[-1].strip(" /＞") in media_leaf_values
    )
    if explicit_media_category:
        # Structured source taxonomy is stronger than title keyword stuffing
        # or a coincidental garment/reference phrase.
        return media_match

    garment_terms = _mixed_term_pattern(
        (
            "t-shirt",
            "t shirt",
            "tshirt",
            "tee",
            "shirt",
            "hoodie",
            "sweatshirt",
            "jacket",
            "coat",
            "knit",
            "sweater",
            "cardigan",
            "pants",
            "trousers",
            "jeans",
            "boot",
            "boots",
            "shoe",
            "shoes",
            "bag",
            "belt",
            "wallet",
        ),
        (
            "tシャツ",
            "シャツ",
            "パーカー",
            "ジャケット",
            "コート",
            "ニット",
            "カーディガン",
            "パンツ",
            "デニム",
            "ブーツ",
            "バッグ",
            "ベルト",
            "財布",
        ),
    )
    reference_marker = _mixed_term_pattern(
        ("cover", "art", "artwork", "graphic", "print", "motif", "inspired", "reference"),
        ("カバー", "アート", "グラフィック", "プリント", "柄", "モチーフ"),
    )
    media_terms_pattern = "(?:" + "|".join(
        _term_pattern(term)
        for term in sorted(terms, key=lambda value: len(normalize_text(value)), reverse=True)
    ) + ")"
    bundled_media = bool(
        re.search(
            rf"(?i){garment_terms}.{{0,40}}"
            rf"(?:with|includes?|including|comes with|w/)\s*"
            rf"(?:an?\s+|the\s+|official\s+|original\s+|collection\s+)*"
            rf"{media_terms_pattern}",
            scope_title,
        )
        or re.search(
            rf"{garment_terms}.{{0,40}}(?:公式|オリジナル)?\s*"
            rf"{media_terms_pattern}\s*(?:付き|付属|同梱)",
            scope_title,
            flags=re.IGNORECASE,
        )
    )
    if bundled_media:
        return None
    if re.search(reference_marker, scope_title, flags=re.IGNORECASE) and re.search(
        garment_terms, scope_title, flags=re.IGNORECASE
    ):
        return None

    terminal_other_category = bool(
        hierarchy and hierarchy[-1] in {"other", "その他", "その他の商品"}
    )
    has_fashion_product_noun = bool(
        re.search(garment_terms, scope_title, flags=re.IGNORECASE)
    )
    strong_media_format = normalize_text(media_match).casefold() in {
        "blu-ray",
        "bluray",
        "dvd",
        "vhs",
        "cd",
        "ブルーレイ",
    }
    corroborated_other_category = bool(
        terminal_other_category and (strong_media_format or not has_fashion_product_noun)
    )
    media_pattern = _term_pattern(media_match)
    explicit_media_phrase = bool(
        re.search(
            rf"(?i)(?:collection|runway|official|promo(?:tional)?|archive|"
            rf"コレクション|ランウェイ|公式|販促)\s*{media_pattern}",
            scope_title,
        )
    )
    terminal_media_noun = bool(
        re.search(rf"(?i){media_pattern}\s*$", scope_title)
    )
    if (
        corroborated_other_category
        or explicit_media_phrase
        or terminal_media_noun
    ):
        return media_match
    return None


def _automotive_non_fashion_offer(
    scope_title: str,
    category_hierarchy: tuple[str, ...],
    terms: list[str],
) -> str | None:
    """Identify offered automotive parts without rejecting car-themed garments."""
    normalized_categories = [
        normalize_text(value).casefold().strip(" /＞")
        for value in category_hierarchy
        if normalize_text(value)
    ]
    exact_automotive_categories = {
        "automotive",
        "automotive parts",
        "auto parts",
        "automobiles",
        "automobiles & motorcycles",
        "automobiles and motorcycles",
        "automobiles, motorcycles",
        "car parts",
        "cars & motorcycles",
        "cars and motorcycles",
        "cars, motorcycles",
        "motorcycles",
        "vehicle parts",
        "vehicles",
        "自動車",
        "自動車、オートバイ",
        "自動車・オートバイ",
        "カー用品",
        "車用品",
    }
    for category in normalized_categories:
        if category in exact_automotive_categories or re.fullmatch(
            r"(?i)(?:automotive|auto|car|vehicle)\s+"
            r"(?:parts?|accessories|interior(?:\s+(?:parts?|trim))?)"
            r"(?:\s*(?:&|and|/)\s*(?:parts?|accessories))?",
            category,
        ):
            return f"category:{category}"
        if re.fullmatch(
            r"(?:自動車|車両|車|カー)(?:用)?"
            r"(?:部品|用品|パーツ|アクセサリー|内装(?:品|パーツ)?)",
            category,
        ):
            return f"category:{category}"

    automotive_match = _longest_term_match(scope_title, terms)
    if not automotive_match:
        return None

    fashion_product = _mixed_term_pattern(
        (
            "t-shirt",
            "t shirt",
            "tshirt",
            "tee",
            "shirt",
            "hoodie",
            "sweatshirt",
            "jacket",
            "coat",
            "knit",
            "sweater",
            "cardigan",
            "pants",
            "trousers",
            "jeans",
            "shorts",
            "boots",
            "boot",
            "shoes",
            "shoe",
            "sneakers",
            "sneaker",
            "bag",
            "belt",
            "wallet",
            "necktie",
            "tie",
            "cap",
            "hat",
            "sunglasses case",
            "eyewear case",
        ),
        (
            "tシャツ",
            "シャツ",
            "パーカー",
            "ジャケット",
            "コート",
            "ニット",
            "カーディガン",
            "パンツ",
            "デニム",
            "ブーツ",
            "シューズ",
            "スニーカー",
            "バッグ",
            "ベルト",
            "財布",
            "ネクタイ",
            "キャップ",
            "帽子",
        ),
    )
    depicted_reference = _mixed_term_pattern(
        (
            "graphic",
            "graphics",
            "print",
            "printed",
            "motif",
            "art",
            "artwork",
            "photo",
            "photograph",
            "image",
            "illustration",
            "inspired",
            "pattern",
        ),
        (
            "グラフィック",
            "プリント",
            "柄",
            "モチーフ",
            "アート",
            "写真",
            "イラスト",
            "オマージュ",
        ),
    )
    if re.search(fashion_product, scope_title, flags=re.IGNORECASE) and re.search(
        depicted_reference, scope_title, flags=re.IGNORECASE
    ):
        return None
    return automotive_match


def analyze_product_scope(
    text: str,
    *,
    relationship: PieceRelationship | None,
    matched_brand: Evidence | None,
    brand_observed: str | None = None,
    category_hierarchy: tuple[str, ...] = (),
    scope_title: str | None = None,
) -> ProductScope:
    """Parse manufacturer role, offered object, audience, and offer clarity once."""
    taxonomy = load_taxonomy()
    normalized = normalize_text(text).casefold()
    reasons: list[str] = []
    target_role = {
        "core": "manufacturer",
        "diffusion": "diffusion",
        "collaboration": "collaborator",
        "homage_reference": "style_reference",
    }.get(relationship.value if relationship else "", "unknown")
    if target_role == "style_reference":
        reasons.append("target name is explicitly a style/reference, not manufacturer evidence")
    non_target_maker = _longest_term_match(
        normalized,
        [str(term) for term in taxonomy.get("negative_terms", {}).get("non_target_maker", [])],
    )
    if non_target_maker and matched_brand:
        target_role = "contradicted"
        reasons.append(f"seller identifies a non-target maker ({non_target_maker})")

    observed = normalize_text(brand_observed).casefold()
    if (
        observed
        and observed not in _BRAND_OBSERVED_PLACEHOLDERS
        and matched_brand
        and not _observed_brand_matches_target(observed, matched_brand, taxonomy)
        and not _participant_matches_observed(observed, relationship)
    ):
        target_role = "contradicted"
        reasons.append(f"detail brand {brand_observed!r} contradicts target manufacturer")

    scope_terms = taxonomy.get("product_scope_terms", {})
    scope_title_text = normalize_text(scope_title).casefold() if scope_title else normalized
    packaging_match = _longest_term_match(
        normalized,
        [str(term) for term in scope_terms.get("packaging_only", [])],
    )
    if not packaging_match:
        product_box = re.search(
            r"(?i)\b(?:wallet|cardholder|card holder|coin case|shoes?|sneakers?|"
            r"boots?|belt|bag)\s+(?:empty\s+)?box\b",
            scope_title_text,
        )
        if product_box:
            without_box_phrase = (
                scope_title_text[: product_box.start()]
                + " "
                + scope_title_text[product_box.end() :]
            )
            if not _first_match(
                without_box_phrase,
                taxonomy.get("item_types", {}),
                "product_scope",
            ):
                packaging_match = product_box.group(0)
    if not packaging_match:
        dust_bag = re.search(r"(?i)\bdust bag\b|保存袋", scope_title_text)
        if dust_bag:
            without_dust_bag = (
                scope_title_text[: dust_bag.start()]
                + " "
                + scope_title_text[dust_bag.end() :]
            )
            if not _first_match(
                without_dust_bag,
                taxonomy.get("item_types", {}),
                "product_scope",
            ):
                packaging_match = dust_bag.group(0)
    container_match = _longest_term_match(
        normalized,
        [str(term) for term in scope_terms.get("container_products", [])],
    )
    media_match = _media_ephemera_offer(
        scope_title_text,
        category_hierarchy,
        [str(term) for term in scope_terms.get("media_ephemera", [])],
    )
    automotive_match = _automotive_non_fashion_offer(
        scope_title_text,
        category_hierarchy,
        [str(term) for term in scope_terms.get("automotive_non_fashion", [])],
    )
    if packaging_match:
        offer_kind = "packaging_only"
        reasons.append(f"offer is packaging/accessory only ({packaging_match})")
    elif media_match:
        offer_kind = "media_ephemera"
        reasons.append(f"offered object is media/ephemera ({media_match})")
    elif automotive_match:
        offer_kind = "non_fashion"
        reasons.append(f"offered object is an automotive part ({automotive_match})")
    elif container_match:
        offer_kind = "container"
        reasons.append(f"offered product is a container ({container_match})")
    else:
        offer_kind = "product"

    audience, audience_reason = _scope_audience(scope_title_text, category_hierarchy)
    if audience_reason:
        reasons.append(audience_reason)
    clarity, remaining_variant, partial_reason = _partial_offer(normalized)
    if partial_reason:
        reasons.append(partial_reason)
    return ProductScope(
        target_role=target_role,
        offer_kind=offer_kind,
        audience=audience,
        offer_clarity=clarity,
        remaining_variant=remaining_variant,
        confidence=0.95 if reasons else 0.7,
        reasons=tuple(reasons),
    )


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


def _participant_label(value: str) -> str:
    return re.sub(r"^[\W_]+|[\W_]+$", "", normalize_text(value).casefold())


def _term_pattern(term: str) -> str:
    normalized = normalize_text(term).casefold()
    escaped = re.escape(normalized)
    if any(character.isascii() and character.isalnum() for character in normalized):
        return rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
    return escaped


def _mixed_term_pattern(
    latin_terms: tuple[str, ...], japanese_terms: tuple[str, ...]
) -> str:
    """Build boundary-safe Latin alternatives plus Japanese substring terms."""
    terms = sorted(
        (*latin_terms, *japanese_terms),
        key=lambda value: len(normalize_text(value)),
        reverse=True,
    )
    return "(?:" + "|".join(_term_pattern(term) for term in terms) + ")"


def _known_partner_at_edge(
    fragment: str,
    taxonomy: dict[str, Any],
    *,
    from_start: bool,
    target_canonical: str,
) -> tuple[str, int, int] | None:
    candidates: list[tuple[int, str, int, int]] = []
    for record in taxonomy.get("brands", {}).values():
        canonical = str(record.get("canonical") or "")
        if not canonical or canonical == target_canonical:
            continue
        terms = (
            canonical,
            *record.get("aliases", []),
            *record.get("recognition_aliases", []),
        )
        for term in terms:
            pattern = _term_pattern(str(term))
            edge_pattern = (
                rf"^\s*(?P<term>{pattern})"
                if from_start
                else rf"(?P<term>{pattern})\s*$"
            )
            match = re.search(edge_pattern, fragment, flags=re.IGNORECASE)
            if match:
                candidates.append(
                    (
                        len(normalize_text(str(term))),
                        _participant_label(canonical),
                        match.start("term"),
                        match.end("term"),
                    )
                )
    if not candidates:
        return None
    _, label, start, end = max(candidates, key=lambda candidate: candidate[0])
    return label, start, end


def _find_brand_collaboration_match(
    text: str, record: dict[str, Any], taxonomy: dict[str, Any]
) -> tuple[str, tuple[str, ...]] | None:
    """Return only an explicit collaboration that actually names the matched house.

    Marketplace titles commonly contain dimensions and variants such as
    ``48 x black``.  Treating every token around an ``x`` as a collaboration
    corrupts relationship ranking and comp matching, so one participant must
    share a meaningful token with a configured brand alias.
    """
    target_canonical = str(record.get("canonical") or "")
    target_label = _participant_label(target_canonical)
    aliases = sorted(
        {
            str(alias)
            for alias in (
                target_canonical,
                *record.get("aliases", []),
                *record.get("recognition_aliases", []),
            )
            if alias
        },
        key=lambda alias: len(normalize_text(alias)),
        reverse=True,
    )
    for alias in aliases:
        for target_match in re.finditer(_term_pattern(alias), text, flags=re.IGNORECASE):
            tail = text[target_match.end() :]
            forward_connector = re.match(r"\s*(?:x|×)\s*", tail, flags=re.IGNORECASE)
            if forward_connector:
                partner_fragment = tail[forward_connector.end() :]
                known = _known_partner_at_edge(
                    partner_fragment,
                    taxonomy,
                    from_start=True,
                    target_canonical=target_canonical,
                )
                if known:
                    partner, _, end = known
                else:
                    token = re.match(r"[^\s×|,/;:()\[\]]+", partner_fragment)
                    if not token:
                        continue
                    partner, end = _participant_label(token.group(0)), token.end()
                if partner and partner != target_label:
                    match_end = target_match.end() + forward_connector.end() + end
                    return (
                        text[target_match.start() : match_end],
                        tuple(sorted((target_label, partner))),
                    )

            prefix = text[: target_match.start()]
            reverse_connector = re.search(r"\s*(?:x|×)\s*$", prefix, flags=re.IGNORECASE)
            if reverse_connector:
                partner_fragment = prefix[: reverse_connector.start()]
                known = _known_partner_at_edge(
                    partner_fragment,
                    taxonomy,
                    from_start=False,
                    target_canonical=target_canonical,
                )
                if known:
                    partner, start, _ = known
                else:
                    token = re.search(r"[^\s×|,/;:()\[\]]+$", partner_fragment)
                    if not token:
                        continue
                    partner, start = _participant_label(token.group(0)), token.start()
                if partner and partner != target_label:
                    return (
                        text[start : target_match.end()],
                        tuple(sorted((target_label, partner))),
                    )
    return None


def _classify_relationship(
    text: str,
    *,
    brand_key: str,
    record: dict[str, Any],
    matched_term: str,
    matched_core_alias: bool,
    collaboration_match: tuple[str, tuple[str, ...]] | None,
) -> PieceRelationship:
    """Classify only relationships evidenced by title/detail text or taxonomy."""
    homage_match = _brand_reference_match(text, matched_term)
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
        matched_collaboration, participants = collaboration_match
        return PieceRelationship(
            "collaboration",
            0.75,
            "title.relationship.collaboration",
            matched_collaboration,
            participants,
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


def enrich_title(
    title: str,
    hinted_brand: str | None = None,
    *,
    brand_observed: str | None = None,
    category_hierarchy: tuple[str, ...] = (),
    scope_title: str | None = None,
) -> FashionProfile:
    taxonomy = load_taxonomy()
    text = normalize_text(title).casefold()
    profile = FashionProfile()
    profile.item_type = _first_match(
        text, taxonomy.get("item_types", {}), "taxonomy.item_types"
    )
    matched_brand: dict[str, Any] | None = None
    collaboration_match: tuple[str, tuple[str, ...]] | None = None

    brand_match = _brand_match(text, taxonomy)
    observed_text = normalize_text(brand_observed).casefold()
    observed_match = _brand_match(observed_text, taxonomy) if observed_text else None
    if brand_match is None and observed_match is not None:
        brand_match = observed_match
    if brand_match is not None:
        brand_key, record, match, matched_core_alias = brand_match
        profile.brand = Evidence(record["canonical"], 0.98, f"taxonomy.brands.{brand_key}", match)
        matched_brand = record
        relationship_text = normalize_text(
            " ".join(value for value in (text, observed_text) if value)
        )
        collaboration_match = _find_brand_collaboration_match(
            relationship_text, record, taxonomy
        )
        profile.relationship = _classify_relationship(
            relationship_text,
            brand_key=brand_key,
            record=record,
            matched_term=match,
            matched_core_alias=matched_core_alias,
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
            for configured_signal in record.get("iconic_signals", []):
                if isinstance(configured_signal, dict):
                    terms = configured_signal.get("terms", [])
                    allowed_item_types = {
                        str(value) for value in configured_signal.get("item_types", [])
                    }
                else:
                    terms = [configured_signal]
                    allowed_item_types = set()
                if allowed_item_types and (
                    not profile.item_type
                    or profile.item_type.value not in allowed_item_types
                ):
                    continue
                for term in terms:
                    if _term_matches(text, str(term)):
                        profile.positive_signals.append(
                            Evidence(
                                str(term),
                                0.85,
                                f"taxonomy.brands.{brand_key}.iconic_signals",
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
            "collaboration", 0.8, "title.collaboration", collaboration_match[0]
        )
    if profile.item_type:
        category = (
            "jewelry"
            if profile.item_type.value == "jewelry"
            else (
                "accessory"
                if profile.item_type.value
                in {"accessory", "bag", "belt", "wallet", "footwear", "eyewear_case"}
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
    profile.product_scope = analyze_product_scope(
        text,
        relationship=profile.relationship,
        matched_brand=profile.brand,
        brand_observed=brand_observed,
        category_hierarchy=category_hierarchy,
        scope_title=scope_title,
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
