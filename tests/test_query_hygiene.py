from buyeeye.discovery import build_query_universe
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
