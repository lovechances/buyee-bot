from buyeeye.discovery import MarketplaceSearchFilters, build_query_universe
from buyeeye.fashion import enrich_title
from buyeeye.live_sources import BuyeeAuthenticatedSource
from buyeeye.settings import CONFIG_DIR, load_yaml


def test_self_named_designer_does_not_duplicate_broad_search_or_drop_collections():
    taxonomy = {
        "brands": {
            "self_named": {
                "canonical": "Example Designer",
                "designer": "example designer",
                "collections": [{"name": "Important Collection"}],
            },
            "different_designer": {
                "canonical": "Prada",
                "designer": "Miuccia Prada",
            },
            "repeated_brand_tokens": {"canonical": "Miu Miu"},
        }
    }

    specs = build_query_universe({}, taxonomy)
    queries = {spec.query for spec in specs}

    assert "Example Designer" in queries
    assert "Example Designer example designer" not in queries
    assert "Example Designer Important Collection" in queries
    assert "Prada Miuccia Prada" in queries
    assert "Miu Miu" in queries


def test_louboutin_bag_query_keeps_one_mens_constraint_and_market_coverage():
    specs = build_query_universe(
        load_yaml(CONFIG_DIR / "searches.yaml"),
        load_yaml(CONFIG_DIR / "fashion_taxonomy.yaml"),
        load_yaml(CONFIG_DIR / "market_structure.yaml"),
    )
    louboutin = [spec for spec in specs if spec.family == "christian_louboutin"]
    queries = {spec.query for spec in louboutin}

    assert "Christian Louboutin メンズ バッグ" in queries
    assert "Christian Louboutin メンズ メンズ バッグ" not in queries
    assert "Christian Louboutin Christian Louboutin" not in queries
    assert any(
        spec.search_filters
        and spec.search_filters.category_group == "mens_shoes"
        and spec.strategy_type == "market_buyout"
        for spec in louboutin
    )
    assert any(
        spec.search_filters and spec.search_filters.category_group == "mens_fashion_goods"
        for spec in louboutin
    )


def test_demonstrated_y_project_source_spelling_keeps_filters_and_other_slashes():
    filters = MarketplaceSearchFilters(category_id="23176", category_group="mens_fashion",
        max_buyout_price_jpy=25000, buy_now_only=True, item_type="auction")
    assert BuyeeAuthenticatedSource._search_url("Y/Project メンズ", filters) == (
        BuyeeAuthenticatedSource._search_url("y project メンズ", filters))
    url = BuyeeAuthenticatedSource._search_url("Y/Project メンズ", filters)
    assert "aucmax_bidorbuy_price=25000" in url and "item_type=auction" in url
    assert "/category/23176" in url and "buynow=1" in url
    assert "AC%2FDC" in BuyeeAuthenticatedSource._search_url("AC/DC")


def test_erd_alias_correction_preserves_small_existing_coverage():
    specs = build_query_universe(load_yaml(CONFIG_DIR / "searches.yaml"),
        load_yaml(CONFIG_DIR / "fashion_taxonomy.yaml"), load_yaml(CONFIG_DIR / "market_structure.yaml"))
    erd = [s for s in specs if s.family == "enfants_riches_deprimes"]
    assert len(erd) == 4
    assert any(s.query == "アンファンリッシュデプリメ" for s in erd)
    assert not any(s.query in {"ERD", "E.R.D."} for s in erd)
    assert enrich_title("ERD printed shirt").brand.value == "Enfants Riches Déprimés"
    assert enrich_title("NERD printed shirt").brand is None
    assert not enrich_title("generic printed shirt ERD style").product_scope.acquisition_attributable
