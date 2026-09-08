from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlencode

from .normalize import normalize_text


@dataclass(frozen=True)
class MarketplaceSearchFilters:
    """Native Buyee search controls used only to choose inventory to inspect."""

    category_id: str | None = None
    category_group: str | None = None
    min_current_price_jpy: int | None = None
    max_current_price_jpy: int | None = None
    min_buyout_price_jpy: int | None = None
    max_buyout_price_jpy: int | None = None
    buy_now_only: bool = False
    item_type: str | None = None
    newly_posted: bool = False
    ending_within_one_hour: bool = False
    sort_by: str | None = None
    sort_order: str | None = None
    price_bucket: str | None = None
    discovery_intent: str | None = None

    def __post_init__(self) -> None:
        if self.category_id and not self.category_id.isdigit():
            raise ValueError("Buyee category_id must contain only digits")
        for minimum, maximum, label in (
            (
                self.min_current_price_jpy,
                self.max_current_price_jpy,
                "current price",
            ),
            (
                self.min_buyout_price_jpy,
                self.max_buyout_price_jpy,
                "buyout price",
            ),
        ):
            for value in (minimum, maximum):
                if value is not None and not 300 <= value <= 300_000:
                    raise ValueError(f"Buyee {label} filters must be between 300 and 300000 JPY")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(f"Buyee minimum {label} cannot exceed its maximum")
        if self.item_type not in {None, "auction", "fleamarket"}:
            raise ValueError("Unsupported Buyee item_type")
        valid_sorts = {
            None: {None},
            "recommended": {"recommended"},
            "cbids": {"a", "d"},
            "bids": {"a", "d"},
            "end": {"a", "d"},
            "bidorbuy": {"a", "d"},
            "score": {None},
        }
        if self.sort_by not in valid_sorts or self.sort_order not in valid_sorts[self.sort_by]:
            raise ValueError("Unsupported Buyee sort/order combination")
        if self.ending_within_one_hour and self.item_type != "auction":
            raise ValueError("Ending-within-one-hour requires JDirectItems Auction results")

    def query_params(self) -> dict[str, str]:
        """Return the current, visible Buyee advanced-search parameter names."""
        params: dict[str, str] = {}
        values = (
            ("aucminprice", self.min_current_price_jpy),
            ("aucmaxprice", self.max_current_price_jpy),
            ("aucmin_bidorbuy_price", self.min_buyout_price_jpy),
            ("aucmax_bidorbuy_price", self.max_buyout_price_jpy),
        )
        for name, value in values:
            if value is not None:
                params[name] = str(value)
        if self.buy_now_only:
            params["buynow"] = "1"
        if self.item_type:
            params["item_type"] = self.item_type
        if self.newly_posted:
            params["new"] = "1"
        if self.ending_within_one_hour:
            params["one_hour_to_finish"] = "1"
        if self.sort_by:
            params["sort"] = self.sort_by
        if self.sort_order:
            params["order"] = self.sort_order
        return params

    def encoded_query(self) -> str:
        return urlencode(self.query_params())

    def observations(self) -> dict[str, Any]:
        """Serializable discovery provenance; deliberately contains no valuation claim."""
        return {
            key: value
            for key, value in {
                "category_id": self.category_id,
                "category_group": self.category_group,
                "min_current_price_jpy": self.min_current_price_jpy,
                "max_current_price_jpy": self.max_current_price_jpy,
                "min_buyout_price_jpy": self.min_buyout_price_jpy,
                "max_buyout_price_jpy": self.max_buyout_price_jpy,
                "buy_now_only": self.buy_now_only or None,
                "item_type": self.item_type,
                "newly_posted": self.newly_posted or None,
                "ending_within_one_hour": self.ending_within_one_hour or None,
                "sort_by": self.sort_by,
                "sort_order": self.sort_order,
                "price_bucket": self.price_bucket,
                "discovery_intent": self.discovery_intent,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class QuerySpec:
    query: str
    provenance: str
    family: str
    priority: int
    cooldown_minutes: int = 180
    discovery_mode: str = "direct_piece"
    query_layer: str = "surgical"
    strategy_id: str | None = None
    strategy_type: str | None = None
    search_filters: MarketplaceSearchFilters | None = None
    deep_search: bool = False

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("Discovery query cannot be blank")
        if self.cooldown_minutes <= 0:
            raise ValueError("Discovery cooldown must be positive")
        if self.query_layer == "market_structure":
            if not self.strategy_id or not self.strategy_type or not self.search_filters:
                raise ValueError("Market-structure queries require an id, type, and filters")
        elif self.strategy_type is None:
            object.__setattr__(self, "strategy_type", f"semantic_{self.query_layer}")

    @property
    def runtime_identity(self) -> str:
        """Keep legacy semantic history while separating same-query market slices."""
        if self.query_layer == "market_structure":
            return f"market_structure:{self.strategy_id}"
        return self.query.casefold()

    def discovery_observation(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "family": self.family,
            "provenance": self.provenance,
            "query_layer": self.query_layer,
            "strategy_id": self.strategy_id,
            "strategy_type": self.strategy_type,
            "search_depth_role": "deep_anchor" if self.deep_search else "shallow",
            "search_filters": self.search_filters.observations() if self.search_filters else {},
        }


def _discovery_mode(provenance: str) -> str:
    if ".collection" in provenance or ".designer" in provenance:
        return "collection_era"
    if ".strong_signal" in provenance:
        return "hidden_mislabeled"
    return "direct_piece"


def query_layer_counts(specs: list[QuerySpec]) -> dict[str, int]:
    counts = Counter(spec.query_layer for spec in specs)
    return {
        layer: counts.get(layer, 0) for layer in ("broad", "medium", "surgical", "market_structure")
    }


def search_page_budget(spec: QuerySpec, scanner_settings: dict[str, Any]) -> int:
    """Return a bounded depth allocation without multiplying every hot scan.

    The global page limit is a hard ceiling.  Core broad searches and the slow
    exploration/price-band strategies can sample a second page; rapidly
    changing buyout and closing searches stay on page one unless explicitly
    configured otherwise.  Lower-priority aliases remain shallow so adding
    depth does not multiply all broad-source traffic.
    """
    ceiling = max(1, min(5, int(scanner_settings.get("search_pages_per_query", 2))))
    layer_defaults = {
        "broad": 1,
        "medium": 1,
        "surgical": 1,
        "market_structure": 1,
    }
    strategy_defaults = {
        "market_explore": 2,
        "market_price_band": 2,
    }
    layer_depth = {
        **layer_defaults,
        **(scanner_settings.get("search_pages_by_layer", {}) or {}),
    }
    strategy_depth = {
        **strategy_defaults,
        **(scanner_settings.get("search_pages_by_strategy", {}) or {}),
    }
    requested = strategy_depth.get(
        spec.strategy_type,
        layer_depth.get(spec.query_layer, 1),
    )
    if spec.deep_search:
        requested = max(
            int(requested),
            int(scanner_settings.get("deep_broad_pages", 2)),
        )
    return max(1, min(ceiling, int(requested)))


def _taxonomy_family_lookup(taxonomy: dict[str, Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for key, brand in taxonomy.get("brands", {}).items():
        names = [brand.get("canonical"), *brand.get("aliases", [])]
        for name in names:
            if name:
                lookup[normalize_text(str(name)).casefold()] = key
    return lookup


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
        terms = [
            *family.get("medium_terms", []),
            *family.get("surgical_terms", []),
            *family.get("terms", []),
        ]
        for alias in aliases:
            queries.append(f"{alias}")
            for term in terms:
                queries.append(f"{alias} {term}")
        if brand:
            for term in terms:
                queries.append(f"{brand} {term}")
    return sorted({normalize_text(q) for q in queries if q})


def _market_band(profiles: dict[str, Any], profile_name: str, band_name: str) -> tuple[int, int]:
    try:
        band = profiles[profile_name]["bands"][band_name]
        minimum = int(band["min_jpy"])
        maximum = int(band["max_jpy"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid market-structure band {profile_name}.{band_name}") from exc
    if minimum > maximum:
        raise ValueError(f"Market band {profile_name}.{band_name} has min above max")
    return minimum, maximum


def build_market_structure_queries(
    config: dict[str, Any], taxonomy: dict[str, Any]
) -> list[QuerySpec]:
    """Build configured marketplace slices without feeding their prices into valuation."""
    if not config:
        return []
    categories = config.get("categories", {})
    profiles = config.get("band_profiles", {})
    defaults = config.get("defaults", {})
    cooldowns = {
        "market_buyout": 75,
        "market_ending": 45,
        "market_closing": 30,
        "market_explore": 360,
        "market_price_band": 240,
        **defaults.get("cooldown_minutes", {}),
    }
    priorities = {
        "market_buyout": 88,
        "market_ending": 86,
        "market_closing": 92,
        "market_explore": 68,
        "market_price_band": 72,
        **defaults.get("priority", {}),
    }
    taxonomy_brands = taxonomy.get("brands", {})
    specs: list[QuerySpec] = []

    for family_config in config.get("market_families", []):
        family = str(family_config.get("family") or "").strip()
        if not family or family not in taxonomy_brands:
            raise ValueError(f"Unknown market-structure fashion family: {family or '<blank>'}")
        query = normalize_text(
            str(family_config.get("query") or taxonomy_brands[family].get("canonical") or "")
        )
        family_priority = int(family_config.get("priority_adjustment", 0))
        for category_config in family_config.get("categories", []):
            group = str(category_config.get("group") or "").strip()
            profile = str(category_config.get("profile") or "").strip()
            if group not in categories:
                raise ValueError(f"Unknown market-structure category group: {group}")
            category_id = str(categories[group].get("id") or "")
            ultra_min, ultra_max = _market_band(profiles, profile, "ultra_cheap")
            _, bargain_max = _market_band(profiles, profile, "bargain")
            moderate_min, moderate_max = _market_band(profiles, profile, "moderate")
            intents = set(category_config.get("intents", ["buyout", "ending", "price_band"]))

            def add_spec(
                suffix: str,
                strategy_type: str,
                filters: MarketplaceSearchFilters,
                *,
                priority_adjustment: int = 0,
                cooldown_adjustment: int = 0,
                query_text: str = query,
                family_key: str = family,
                category_group: str = group,
                base_priority_adjustment: int = family_priority,
            ) -> None:
                specs.append(
                    QuerySpec(
                        query=query_text,
                        provenance=(f"market_structure.{family_key}.{category_group}.{suffix}"),
                        family=family_key,
                        priority=int(priorities[strategy_type])
                        + base_priority_adjustment
                        + priority_adjustment,
                        cooldown_minutes=max(
                            15,
                            int(cooldowns[strategy_type]) + cooldown_adjustment,
                        ),
                        discovery_mode="market_slice",
                        query_layer="market_structure",
                        strategy_id=f"{family_key}.{category_group}.{suffix}",
                        strategy_type=strategy_type,
                        search_filters=filters,
                    )
                )

            if "buyout" in intents:
                add_spec(
                    "buyout.actionable",
                    "market_buyout",
                    MarketplaceSearchFilters(
                        category_id=category_id,
                        category_group=group,
                        min_buyout_price_jpy=ultra_min,
                        max_buyout_price_jpy=bargain_max,
                        buy_now_only=True,
                        item_type="auction",
                        newly_posted=True,
                        sort_by="end",
                        sort_order="d",
                        price_bucket="actionable",
                        discovery_intent="cheap_actionable_buyout",
                    ),
                )
                if category_config.get("negative_space"):
                    add_spec(
                        "buyout.ultra_cheap",
                        "market_buyout",
                        MarketplaceSearchFilters(
                            category_id=category_id,
                            category_group=group,
                            min_buyout_price_jpy=ultra_min,
                            max_buyout_price_jpy=ultra_max,
                            buy_now_only=True,
                            item_type="auction",
                            newly_posted=True,
                            sort_by="bidorbuy",
                            sort_order="a",
                            price_bucket="ultra_cheap",
                            discovery_intent="negative_space_buyout",
                        ),
                        priority_adjustment=2,
                        cooldown_adjustment=-15,
                    )
            if "explore" in intents:
                add_spec(
                    "buyout.explore",
                    "market_explore",
                    MarketplaceSearchFilters(
                        category_id=category_id,
                        category_group=group,
                        min_buyout_price_jpy=ultra_min,
                        max_buyout_price_jpy=bargain_max,
                        buy_now_only=True,
                        item_type="auction",
                        sort_by="bidorbuy",
                        sort_order="a",
                        price_bucket="exploration",
                        discovery_intent="retained_family_buyout_exploration",
                    ),
                )
            if "ending" in intents:
                add_spec(
                    "ending.mature",
                    "market_ending",
                    MarketplaceSearchFilters(
                        category_id=category_id,
                        category_group=group,
                        min_current_price_jpy=ultra_min,
                        max_current_price_jpy=bargain_max,
                        item_type="auction",
                        sort_by="end",
                        sort_order="a",
                        price_bucket="actionable",
                        discovery_intent="ending_soon_bargain",
                    ),
                )
                if category_config.get("closing_hour"):
                    add_spec(
                        "ending.closing_hour",
                        "market_closing",
                        MarketplaceSearchFilters(
                            category_id=category_id,
                            category_group=group,
                            min_current_price_jpy=ultra_min,
                            max_current_price_jpy=bargain_max,
                            item_type="auction",
                            ending_within_one_hour=True,
                            sort_by="end",
                            sort_order="a",
                            price_bucket="actionable",
                            discovery_intent="closing_hour_bargain",
                        ),
                        priority_adjustment=3,
                        cooldown_adjustment=-15,
                    )
            if "price_band" in intents:
                add_spec(
                    "price_band.moderate",
                    "market_price_band",
                    MarketplaceSearchFilters(
                        category_id=category_id,
                        category_group=group,
                        min_current_price_jpy=moderate_min,
                        max_current_price_jpy=moderate_max,
                        item_type="auction",
                        sort_by="cbids",
                        sort_order="a",
                        price_bucket="moderate",
                        discovery_intent="moderate_archive_inventory",
                    ),
                )
    return specs


def build_query_universe(
    search_config: dict[str, Any],
    taxonomy: dict[str, Any],
    market_config: dict[str, Any] | None = None,
) -> list[QuerySpec]:
    """Build a controlled discovery set; deliberately avoids Cartesian combinations."""
    candidates: list[QuerySpec] = []
    family_lookup = _taxonomy_family_lookup(taxonomy)
    for family in get_search_families(search_config):
        brand_name = normalize_text(str(family.get("brand") or "unknown"))
        brand = family_lookup.get(brand_name.casefold(), brand_name)
        cooldown = int(family.get("cooldown_minutes", 180))
        priority = int(family.get("priority", 70))
        aliases = [normalize_text(str(value)) for value in family.get("aliases", []) if value]
        medium_terms = [
            normalize_text(str(value)) for value in family.get("medium_terms", []) if value
        ]
        surgical_terms = [
            normalize_text(str(value))
            for value in [*family.get("surgical_terms", []), *family.get("terms", [])]
            if value
        ]
        for alias in aliases[:4]:
            candidates.append(
                QuerySpec(
                    alias,
                    "searches.alias",
                    brand,
                    priority,
                    cooldown,
                    "direct_piece",
                    "broad",
                )
            )
        anchor = aliases[0] if aliases else brand
        for term in medium_terms[:6]:
            candidates.append(
                QuerySpec(
                    normalize_text(f"{anchor} {term}"),
                    "searches.medium_term",
                    brand,
                    priority,
                    cooldown,
                    "collection_era",
                    "medium",
                )
            )
        for term in surgical_terms[:10]:
            candidates.append(
                QuerySpec(
                    normalize_text(f"{anchor} {term}"),
                    "searches.surgical_term",
                    brand,
                    priority + 5,
                    cooldown,
                    "direct_piece",
                    "surgical",
                )
            )

    for key, brand in taxonomy.get("brands", {}).items():
        canonical = normalize_text(str(brand.get("canonical") or key))
        aliases = [normalize_text(str(value)) for value in brand.get("aliases", []) if value]
        provenance = f"taxonomy.brands.{key}.canonical"
        candidates.append(
            QuerySpec(canonical, provenance, key, 80, 240, _discovery_mode(provenance), "broad")
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
                        "broad",
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
                    "medium",
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
                        "surgical",
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
                        "surgical",
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
                    "surgical",
                )
            )

    candidates.extend(build_market_structure_queries(market_config or {}, taxonomy))

    best: dict[str, QuerySpec] = {}
    for spec in candidates:
        key = spec.runtime_identity
        if spec.query and (key not in best or spec.priority > best[key].priority):
            best[key] = spec
    deduped = list(best.values())
    canonical_by_family = {
        key: normalize_text(str(record.get("canonical") or key)).casefold()
        for key, record in taxonomy.get("brands", {}).items()
    }
    # One stable broad anchor per retained family samples page two. This gives
    # every family sustained depth without granting duplicate English/Japanese
    # aliases multiple deep slots. Slow market exploration/price-band slices
    # retain their independent depth allocation through strategy settings.
    anchor_identity_by_family: dict[str, str] = {}
    for family, canonical in canonical_by_family.items():
        family_broad = [
            spec
            for spec in deduped
            if spec.family == family and spec.query_layer == "broad"
        ]
        if not family_broad:
            continue
        anchor = max(
            family_broad,
            key=lambda spec: (
                normalize_text(spec.query).casefold() == canonical,
                spec.provenance.endswith(".canonical"),
                spec.priority,
                -len(spec.query),
            ),
        )
        anchor_identity_by_family[family] = anchor.runtime_identity
    deduped = [
        replace(spec, deep_search=True)
        if (
            spec.query_layer == "broad"
            and anchor_identity_by_family.get(spec.family) == spec.runtime_identity
        )
        else spec
        for spec in deduped
    ]
    return sorted(deduped, key=lambda spec: (-spec.priority, spec.family, spec.query))


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
