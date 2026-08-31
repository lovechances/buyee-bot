import asyncio
import subprocess
import sys
from pathlib import Path

import httpx

from buyeeye.browser_session import (
    _remove_stale_profile_artifacts,
    has_valid_session,
    load_session_cookies,
)
from buyeeye.comp_cache import CompRepository, CompService
from buyeeye.comp_sources import SoldCompSource, YahooEndedSource, build_comp_queries
from buyeeye.db import Listing, get_session, save_listings
from buyeeye.discovery import dedupe_listings, generate_search_queries
from buyeeye.evaluation import (
    assess_auth_risk,
    evaluate_comps,
    evaluate_economics,
    evaluate_listing,
    score_desirability,
)
from buyeeye.fashion import enrich_title, expand_discovery_terms
from buyeeye.intelligence import list_proposals
from buyeeye.live_sources import BuyeeAuthenticatedSource, LiveListing
from buyeeye.models import Comp
from buyeeye.normalize import negative_term_hits, normalize_season, normalize_text
from buyeeye.pipeline import (
    _is_relevant,
    _minutes_remaining,
    _price_is_valid,
    _route,
    _route_evaluation,
    scan_once,
)
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


def test_stale_singleton_cleanup_preserves_profile_data(tmp_path):
    profile = tmp_path / "browser"
    profile.mkdir()
    cookie_db = profile / "Cookies"
    level_db = profile / "000123.ldb"
    nested_lock = profile / "Local Storage" / "leveldb" / "LOCK"
    nested_lock.parent.mkdir(parents=True)
    cookie_db.write_bytes(b"cookie-state")
    level_db.write_bytes(b"level-state")
    nested_lock.write_bytes(b"level-lock")
    (profile / "SingletonCookie 2").write_text("stale", encoding="utf-8")
    (profile / "SingletonLock").symlink_to(profile / "missing-target")

    _remove_stale_profile_artifacts(profile)

    assert not (profile / "SingletonCookie 2").exists()
    assert not (profile / "SingletonLock").is_symlink()
    assert cookie_db.read_bytes() == b"cookie-state"
    assert level_db.read_bytes() == b"level-state"
    assert nested_lock.read_bytes() == b"level-lock"


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
    positives, negatives = extract_taste_signals(
        "Number (N)ine destroy mohair archive", "This is a rebuild"
    )
    assert "destroy" in positives
    assert "rebuild" in positives
    assert classify_brand("Number Nine") == "number_nine"
    assert "generic" not in negatives


def test_fashion_enrichment_recognizes_archive_context_and_japanese_terms():
    profile = enrich_title("ナンバーナイン 03AW カート期 モヘア ニット")
    assert profile.brand and profile.brand.value == "Number (N)ine"
    assert profile.collection and profile.collection.value == "The High Streets"
    assert profile.item_type and profile.item_type.value == "knitwear"
    assert profile.material and profile.material.value == "mohair"
    assert profile.season and profile.season.value == "03AW"
    assert profile.brand.provenance.startswith("taxonomy.brands")


def test_placeholder_authentication_badge_is_not_a_listing_title():
    assert BuyeeAuthenticatedSource._extract_card_title({"title": "With Authentication"}) == ""


def test_generic_brand_item_scores_below_piece_specific_archive_item():
    generic = enrich_title("Saint Laurent basic logo tee")
    archive = enrich_title("Saint Laurent Paris Hedi 2015 Babycat runway jacket")
    generic_score = score_desirability(generic, "Saint Laurent basic logo tee")
    archive_score = score_desirability(archive, "Saint Laurent Paris Hedi 2015 Babycat runway jacket")
    assert generic_score.score < 20
    assert archive_score.score >= 55
    assert "brand match without piece-specific desirability evidence" in generic_score.warnings


def test_piece_aware_comps_reject_brand_only_category_mismatch_and_active_asks():
    profile = enrich_title("Chrome Hearts keeper 925 silver bracelet")
    comps = [
        Comp("grailed_sold", "Chrome Hearts keeper 925 silver bracelet", 180_000, 0.95, 30),
        Comp("ebay_sold", "Chrome Hearts floral 925 silver bracelet", 160_000, 0.85, 60),
        Comp("grailed_sold", "Chrome Hearts logo t-shirt", 45_000, 0.95, 20),
        Comp("reseller_ask", "Chrome Hearts keeper bracelet", 400_000, 1.0, 1, is_sold=False),
    ]
    result = evaluate_comps(profile, comps)
    assert result.median_jpy == 170_000
    assert len(result.matches) == 2
    assert result.confidence == "low"


def test_exact_piece_comp_outranks_brand_only_and_outlier_is_trimmed():
    profile = enrich_title("Dior Homme 04SS Strip distressed wax denim jeans")
    assert profile.collection and profile.collection.value == "Strip"
    prices = [100_000, 105_000, 110_000, 115_000, 9_999_999]
    comps = [
        Comp(
            "yahoo_ended",
            "Dior Homme 04SS Strip distressed wax denim jeans",
            price,
            1.0,
            index + 1,
            query="Dior Homme Strip denim",
        )
        for index, price in enumerate(prices)
    ]
    comps.append(Comp("yahoo_ended", "Dior Homme generic shirt", 20_000, 1.0, 2))
    result = evaluate_comps(profile, comps)
    assert result.matches[0].similarity > 0.8
    assert all("shirt" not in comp.title for comp in result.matches)
    assert result.median_jpy == 110_000
    assert result.high_jpy < 9_999_999
    assert result.matches[0].query == "Dior Homme Strip denim"


def test_yahoo_closed_parser_requires_explicit_winning_price():
    html = """
    <ul><li>
      <a href="https://auctions.yahoo.co.jp/jp/auction/a123">Dior Homme 04SS Strip wax denim</a>
      <span>落札 54,900 円</span><span>8/25 19:38 終了</span>
    </li><li>
      <a href="https://auctions.yahoo.co.jp/jp/auction/b456">Active Dior ask</a>
      <span>現在 99,999 円</span>
    </li></ul>
    """
    comps = YahooEndedSource.parse(html, "Dior Homme Strip")
    assert len(comps) == 1
    assert comps[0].is_sold is True
    assert comps[0].price_jpy == 54_900
    assert comps[0].original_currency == "JPY"
    assert comps[0].source_listing_id == "a123"


def test_comp_cache_prevents_repeat_retrieval(tmp_path):
    class CountingSource(SoldCompSource):
        name = "yahoo_ended"

        def __init__(self):
            self.calls = 0

        async def fetch(self, query):
            self.calls += 1
            return [
                Comp(
                    "yahoo_ended",
                    "Number (N)ine A Closed Feeling jacket",
                    80_000,
                    1.0,
                    5,
                    source_listing_id="sold-1",
                    query=query,
                )
            ]

    source = CountingSource()
    service = CompService([source], CompRepository(tmp_path / "cache.db"))
    profile = enrich_title("Number (N)ine 08AW A Closed Feeling jacket")
    first = asyncio.run(service.retrieve(profile, minimum_candidates=1))
    second = asyncio.run(service.retrieve(profile, minimum_candidates=1))
    assert first and second
    assert source.calls == 1


def test_comp_query_builder_preserves_piece_identity_then_broadens():
    profile = enrich_title("Number (N)ine 08AW A Closed Feeling mohair knit")
    queries = build_comp_queries(profile)
    assert "A Closed Feeling" in queries[0]
    assert queries[-1] == "Number (N)ine"


def test_economics_stays_separate_and_fails_closed_without_comps():
    no_comps = evaluate_comps(enrich_title("Dior Homme Hedi Strip jacket"), [])
    result = evaluate_economics(20_000, no_comps)
    assert result.landed_cost_jpy and result.landed_cost_jpy > 20_000
    assert result.opportunity_score == 0
    assert result.multiple is None
    assert "no reliable sold-market valuation" in result.warnings


def test_auth_risk_is_explainable_and_conservative():
    profile = enrich_title("Chrome Hearts style 925 ring")
    risk = assess_auth_risk(profile, "Chrome Hearts style 925 ring", 3_000, {})
    assert risk.level == "do_not_touch"
    assert "counterfeit-associated seller wording" in risk.reasons


def test_auth_risk_blocks_apparently_excellent_economics():
    listing = LiveListing(
        source="buyee",
        source_listing_id="risky-ring",
        url="https://buyee.jp/item/risky-ring",
        title_original="Chrome Hearts style keeper 925 silver ring",
        title_normalized="chrome hearts style keeper 925 silver ring",
        brand="Chrome Hearts",
        category="jewelry",
        current_price=3_000,
        buyout_price=3_000,
        starting_price=3_000,
        seller_name=None,
        condition=None,
    )
    comps = [
        Comp("yahoo_ended", "Chrome Hearts keeper 925 silver ring", price, 1.0, 10)
        for price in (80_000, 90_000, 100_000)
    ]
    evaluation = evaluate_listing(listing, comps)
    assert evaluation.economics.opportunity_score >= 70
    assert evaluation.auth_risk.level == "do_not_touch"
    assert _route_evaluation(listing, evaluation) is None


def test_high_confidence_economics_can_route_buyout():
    listing = LiveListing(
        source="buyee",
        source_listing_id="good-buyout",
        url="https://buyee.jp/item/good-buyout",
        title_original="Number (N)ine 08AW A Closed Feeling mohair knit",
        title_normalized="number n ine 08aw a closed feeling mohair knit",
        brand="Number (N)ine",
        category="menswear",
        current_price=10_000,
        buyout_price=10_000,
        starting_price=10_000,
        seller_name=None,
        condition=None,
    )
    comps = [
        Comp("yahoo_ended", listing.title_original, price, 1.0, 10)
        for price in (90_000, 100_000, 110_000)
    ]
    evaluation = evaluate_listing(listing, comps)
    assert evaluation.comps.confidence == "medium"
    assert evaluation.economics.opportunity_score >= 70
    assert _route_evaluation(listing, evaluation) == (
        "buyouts",
        "strong sold-comp economics at a fixed buyout",
    )


def test_discovery_expansion_has_provenance_and_japanese_aliases():
    terms = expand_discovery_terms("margiela")
    assert any(term["term"] == "マルタンマルジェラ" for term in terms)
    assert all(term["provenance"].startswith("taxonomy.brands.margiela") for term in terms)


def test_landed_cost_and_bid_math():
    landed = estimate_landed_cost_jpy(
        10000,
        {
            "pricing": {
                "buyee_fee_percent": 0.1,
                "plan_fee_jpy": 350,
                "international_shipping_jpy": 1500,
                "packaging_jpy": 300,
                "insurance_percent": 0.02,
                "duty_percent": 0.08,
                "reserve_percent": 0.05,
            }
        },
    )
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


def test_buyee_authenticated_source_uses_browser_fallback_on_http_403(monkeypatch):
    class FakeResponse:
        status_code = 403
        text = "<html><body><a href='https://buyee.jp/item/123'>Number (N)ine tee ¥12000</a></body></html>"

        def raise_for_status(self):
            raise httpx.HTTPStatusError("403 Forbidden", request=None, response=self)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("buyeeye.live_sources.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr(
        "buyeeye.live_sources.BuyeeAuthenticatedSource._fetch_via_browser",
        lambda self, query: [
            LiveListing(
                source="buyee",
                source_listing_id="123",
                url="https://buyee.jp/item/123",
                title_original="Number (N)ine tee",
                title_normalized="number n ine tee",
                brand="Number (N)ine",
                category="unknown",
                current_price=12000,
                buyout_price=12000,
                starting_price=12000,
                seller_name=None,
                condition=None,
                raw={"query": query},
            )
        ],
    )

    items = asyncio.run(BuyeeAuthenticatedSource().fetch("Number (N)ine"))
    assert len(items) == 1
    assert items[0].source == "buyee"
    assert items[0].current_price == 12000


def test_buyee_authenticated_source_uses_browser_fallback_on_empty_http_200(monkeypatch):
    class FakeResponse:
        text = "<html><body>interstitial without listings</body></html>"

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    expected = [
        LiveListing(
            source="buyee",
            source_listing_id="fallback-200",
            url="https://buyee.jp/item/fallback-200",
            title_original="Number (N)ine jacket",
            title_normalized="number n ine jacket",
            brand="Number (N)ine",
            category="menswear",
            current_price=12000,
            buyout_price=None,
            starting_price=12000,
            seller_name=None,
            condition=None,
        )
    ]
    monkeypatch.setattr("buyeeye.live_sources.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr(
        "buyeeye.live_sources.BuyeeAuthenticatedSource._fetch_via_browser",
        lambda self, query: expected,
    )

    assert asyncio.run(BuyeeAuthenticatedSource().fetch("Number (N)ine")) == expected


def test_buyee_browser_payload_parser_extracts_live_listing_fields_from_real_dom():
    rows = [
        {
            "href": "https://buyee.jp/item/jdirectitems/auction/s1242650927?conversionType=yac_top_recommend_list",
            "text": "Time Remaining 6 day(s) 3,300 YEN (US$21.65)",
            "title": "Number (N)ine overshirt",
            "image_url": "https://example.com/overshirt.jpg",
        },
        {
            "href": "https://buyee.jp/item/jdirectitems/auction/c1242648160?conversionType=yac_top_recommend_list",
            "text": "Time Remaining 13 hour(s) 4,000 YEN (US$26.24)",
            "image_url": "https://example.com/other.jpg",
        },
    ]

    items = BuyeeAuthenticatedSource._items_from_browser_payload(rows, "Number (N)ine")
    assert len(items) == 2
    assert items[0].title_original == "Number (N)ine overshirt"
    assert items[0].time_remaining == "6 day(s)"
    assert items[0].current_price == 3300
    assert items[0].source_listing_id.endswith("1242650927")
    assert items[1].title_original == ""
    assert items[1].time_remaining == "13 hour(s)"
    assert items[1].current_price == 4000
    assert (
        BuyeeAuthenticatedSource._extract_time_remaining(
            "Current Price 3,300 YEN Time Remaining 2 day(s) Number of Bids 4"
        )
        == "2 day(s)"
    )
    assert (
        BuyeeAuthenticatedSource._extract_brand("ナンバーナイン ジップパーカー") == "Number (N)ine"
    )
    assert BuyeeAuthenticatedSource._infer_category("ナンバーナイン ジップパーカー") == "menswear"


def test_alert_quality_guards_reject_discovery_only_and_bad_prices():
    listing = LiveListing(
        source="buyee",
        source_listing_id="quality-1",
        url="https://buyee.jp/item/quality-1",
        title_original="Number (N)ine archive overshirt",
        title_normalized="Number (N)ine archive overshirt",
        brand="Number (N)ine",
        category="unknown",
        current_price=3300,
        buyout_price=None,
        starting_price=3300,
        seller_name=None,
        condition=None,
        time_remaining="7 day(s)",
    )
    assert _is_relevant(listing) is True
    assert _minutes_remaining("7 day(s)") == 10080
    assert _route(listing, 10) is None
    listing.current_price = 99_999_999
    assert _price_is_valid(listing) is False


def test_ending_route_requires_confident_sixty_minute_window():
    listing = LiveListing(
        source="buyee",
        source_listing_id="ending-1",
        url="https://buyee.jp/item/ending-1",
        title_original="Number (N)ine destroy knit",
        title_normalized="Number (N)ine destroy knit",
        brand="Number (N)ine",
        category="menswear",
        current_price=3300,
        buyout_price=None,
        starting_price=3300,
        seller_name=None,
        condition=None,
        time_remaining="45 minutes",
    )
    assert _route(listing, 70, 60) == ("ending", "auction ends within the configured threshold")
    listing.time_remaining = "2 day(s)"
    assert _route(listing, 70, 60) is None
    listing.time_remaining = "unknown"
    assert _route(listing, 70, 60) is None


def test_irrelevant_listing_is_not_relevant():
    listing = LiveListing(
        source="buyee",
        source_listing_id="irrelevant-1",
        url="https://buyee.jp/item/irrelevant-1",
        title_original="Number (N)ine poster figure",
        title_normalized="Number (N)ine poster figure",
        brand="Number (N)ine",
        category="collectible",
        current_price=1000,
        buyout_price=None,
        starting_price=1000,
        seller_name=None,
        condition=None,
    )
    assert _is_relevant(listing) is False


def test_save_listings_upserts_existing_listing_by_source_and_id(tmp_path):
    db_path = tmp_path / "buyeeye.db"
    first = [
        LiveListing(
            source="buyee",
            source_listing_id="abc123",
            url="https://buyee.jp/item/jdirectitems/auction/abc123",
            title_original="Number (N)ine overshirt",
            title_normalized="number n ine overshirt",
            brand="Number (N)ine",
            category="unknown",
            current_price=3300,
            buyout_price=None,
            starting_price=3300,
            seller_name=None,
            condition=None,
            image_url="https://example.com/overshirt.jpg",
            time_remaining="6 day(s)",
            bid_count=5,
            watch_count=17,
            raw={"query": "Number (N)ine"},
        )
    ]
    created1, updated1 = save_listings(first, db_path)
    assert created1 == 1 and updated1 == 0

    second = [
        LiveListing(
            source="buyee",
            source_listing_id="abc123",
            url="https://buyee.jp/item/jdirectitems/auction/abc123",
            title_original="Number (N)ine overshirt",
            title_normalized="number n ine overshirt",
            brand="Number (N)ine",
            category="unknown",
            current_price=3600,
            buyout_price=None,
            starting_price=3300,
            seller_name=None,
            condition=None,
            image_url="https://example.com/overshirt.jpg",
            time_remaining="5 day(s)",
            bid_count=7,
            watch_count=21,
            raw={"query": "Number (N)ine"},
        )
    ]
    created2, updated2 = save_listings(second, db_path)
    assert created2 == 0 and updated2 == 1

    session = get_session(db_path)
    try:
        rows = session.query(Listing).all()
        assert len(rows) == 1
        assert rows[0].current_price == 3600
        assert rows[0].time_remaining == "5 day(s)"
        assert rows[0].bid_count == 7
    finally:
        session.close()


def test_scan_suppresses_unchanged_qualifying_alert(monkeypatch, tmp_path):
    listing = LiveListing(
        source="buyee",
        source_listing_id="dedupe-alert-1",
        url="https://buyee.jp/item/dedupe-alert-1",
        title_original="Number (N)ine 08AW A Closed Feeling destroy mohair patchwork jacket",
        title_normalized="number n ine 08aw a closed feeling destroy mohair patchwork jacket",
        brand="Number (N)ine",
        category="menswear",
        current_price=4000,
        buyout_price=4000,
        starting_price=4000,
        seller_name=None,
        condition=None,
        time_remaining="2 day(s)",
    )

    async def fake_run_once(self, query, persist=False):
        return [listing]

    monkeypatch.setattr("buyeeye.pipeline.LiveMarketLoop.run_once", fake_run_once)
    monkeypatch.setattr("buyeeye.pipeline.send_listing_alert", lambda *args: (True, 204, 0.01))
    db_path = tmp_path / "dedupe.db"

    first = asyncio.run(scan_once("Number (N)ine", db_path))
    second = asyncio.run(scan_once("Number (N)ine", db_path))

    assert first.new_alerts == 1
    assert second.new_alerts == 0
    assert second.suppressed_alerts == 1
    assert second.updated_rows == 1
