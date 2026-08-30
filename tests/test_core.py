import subprocess
import sys
from pathlib import Path

from buyeeye.browser_session import has_valid_session, load_session_cookies
from buyeeye.discovery import dedupe_listings, generate_search_queries
from buyeeye.intelligence import list_proposals
from buyeeye.normalize import negative_term_hits, normalize_season, normalize_text
from buyeeye.scoring import score_listing
from buyeeye.taste import classify_brand, extract_taste_signals
from buyeeye.valuation import estimate_landed_cost_jpy, estimate_max_bid_jpy


def test_has_valid_session_detects_buyee_cookie(monkeypatch, tmp_path):
    state_path = tmp_path / "buyee_state.json"
    state_path.write_text(
        '{"cookies": [{"name": "_fwb", "value": "abc123", "domain": ".buyee.jp", "path": "/"}], "origins": []}',
        encoding="utf-8",
    )
    assert has_valid_session(state_path) is True


def test_load_session_cookies_reads_saved_state(monkeypatch, tmp_path):
    state_path = tmp_path / "buyee_state.json"
    state_path.write_text(
        '{"cookies": [{"name": "_fwb", "value": "abc123", "domain": ".buyee.jp", "path": "/", "secure": false}], "origins": []}',
        encoding="utf-8",
    )
    monkeypatch.setattr("buyeeye.browser_session.STATE_PATH", state_path)

    cookies = load_session_cookies()
    assert cookies["_fwb"] == "abc123"
    assert cookies["_fwb"]


def test_generate_search_queries_contains_aliases_and_terms():
    config = {
        "search_families": [
            {
                "brand": "Number (N)ine",
                "aliases": ["Number (N)ine", "ナンバーナイン"],
                "terms": ["destroy", "mohair"],
            }
        ]
    }
    queries = generate_search_queries(config)
    assert "Number (N)ine" in queries
    assert "ナンバーナイン destroy" in queries
    assert "Number (N)ine mohair" in queries


def test_dedupe_listings_uses_source_listing_id():
    listings = [
        {"source_listing_id": "A1", "title_original": "X"},
        {"source_listing_id": "A1", "title_original": "X duplicate"},
        {"source_listing_id": "B2", "title_original": "Y"},
    ]
    unique = dedupe_listings(listings)
    assert len(unique) == 2


def test_normalization_and_negative_terms():
    assert normalize_text("  hello   world  ") == "hello world"
    assert normalize_season("09AW") == "AW"
    assert negative_term_hits("fake replica leather") == ["fake", "replica"]


def test_extract_taste_signals_and_brand_classification():
    positives, negatives = extract_taste_signals("Number (N)ine destroy mohair archive", "This is a rebuild")
    assert "destroy" in positives
    assert "rebuild" in positives
    assert classify_brand("Number Nine") == "number_nine"
    assert "generic" not in negatives


def test_landed_cost_and_bid_math():
    landed = estimate_landed_cost_jpy(10000, {"pricing": {"buyee_fee_percent": 0.1, "plan_fee_jpy": 350, "international_shipping_jpy": 1500, "packaging_jpy": 300, "insurance_percent": 0.02, "duty_percent": 0.08, "reserve_percent": 0.05}})
    assert landed["landed_jpy"] > 10000
    assert estimate_max_bid_jpy(5000, 8000, {"pricing": {"min_profit_multiple": 1.5}}) <= 10000


def test_intelligence_has_proposals():
    proposals = list_proposals()
    assert len(proposals) >= 1
    assert proposals[0].term == "本人期"


def test_score_listing_prefers_distinctive_piece_over_generic_brand_noise():
    generic = {
        "brand": "YSL",
        "title_original": "YSL basic shirt",
        "current_price": 1500,
        "tags": ["generic", "basic"],
    }
    distinct = {
        "brand": "Number (N)ine",
        "title_original": "Number (N)ine destroy mohair archive piece",
        "current_price": 1500,
        "tags": ["destroy", "mohair", "archive"],
    }
    generic_score = score_listing(generic)
    distinct_score = score_listing(distinct)
    assert distinct_score > generic_score


def test_installed_package_cli_starts():
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "buyeeye", "status"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "BuyEye status: ready" in result.stdout
