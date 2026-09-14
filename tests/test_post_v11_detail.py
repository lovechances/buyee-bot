import json
from pathlib import Path

import pytest

from buyeeye.detail_enrichment import parse_detail_payload


@pytest.mark.parametrize("row", json.loads((Path(__file__).parent / "fixtures/post_v11_primary_brand.json").read_text()))
def test_real_primary_brand_row_is_not_lost(row):
    payload = {"association_version": 1,
        "page_url": f"https://buyee.jp/item/jdirectitems/auction/{row['id']}",
        "heading": row["title"], "visible_text": row["visible_text"], "labels": []}
    observed = parse_detail_payload("buyee", row["id"], payload)
    assert observed.brand_observed == row["brand"]
    assert observed.raw == payload


@pytest.mark.parametrize("visible", [
    "Item Explanation\nBrand\nNeedles\nItem Condition\nUsed",
    "Recently viewed items\nBrand\nNeedles\nItem Condition\nUsed",
    "Brand\nItem Condition\nUsed", "Brand\n-\nItem Condition\nUsed",
    "Brand\nNeedles\nItem Condition\nUsed\nBrand\nKapital\nItem Condition\nUsed",
])
def test_missing_or_nonprimary_brand_is_not_manufacturer_evidence(visible):
    payload = {"association_version": 1,
        "page_url": "https://buyee.jp/item/jdirectitems/auction/a1234567890",
        "heading": "shirt", "visible_text": visible}
    assert parse_detail_payload("buyee", "a1234567890", payload).brand_observed is None
