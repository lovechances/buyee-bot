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
from buyeeye.comp_sources import (
    ObservedUSSoldSource,
    SoldCompSource,
    YahooEndedSource,
    build_comp_queries,
)
from buyeeye.db import Listing, get_session, save_listings
from buyeeye.detail_enrichment import (
    BuyeeDetailService,
    DetailObservation,
    DetailRepository,
    apply_detail,
    parse_detail_payload,
)
from buyeeye.discord_alerts import send_listing_alert
from buyeeye.discovery import (
    QuerySpec,
    build_query_universe,
    dedupe_listings,
    generate_search_queries,
)
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
from buyeeye.runtime_state import choose_query, mark_listing_seen, record_query_result
from buyeeye.scoring import score_listing
from buyeeye.settings import load_yaml
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

    async def fake_detail(self, listings):
        return 0, len(listings)

    async def fake_comps(self, profile, minimum_candidates=8):
        return []

    monkeypatch.setattr("buyeeye.pipeline.LiveMarketLoop.run_once", fake_run_once)
    monkeypatch.setattr("buyeeye.pipeline.BuyeeDetailService.enrich", fake_detail)
    monkeypatch.setattr("buyeeye.pipeline.CompService.retrieve", fake_comps)
    monkeypatch.setattr("buyeeye.pipeline.send_listing_alert", lambda *args: (True, 204, 0.01))
    db_path = tmp_path / "dedupe.db"

    first = asyncio.run(scan_once("Number (N)ine", db_path))
    second = asyncio.run(scan_once("Number (N)ine", db_path))

    assert first.new_alerts == 1
    assert second.new_alerts == 0
    assert second.suppressed_alerts == 1
    assert second.updated_rows == 1


def test_detail_payload_preserves_raw_observations():
    payload = {
        "heading": "Number (N)ine 2001SS Time Migration Skull Mohair Knit Size 3",
        "description": "Original Japanese description with shoulder 46 cm",
        "breadcrumbs": ["Fashion", "Men", "Knitwear"],
        "labels": [
            ["Brand", "Number (N)ine"],
            ["Item Type", "Knitwear"],
            ["Condition", "Used"],
            ["Size", "3"],
            ["Measurements", "Shoulder 46 cm / Chest 52 cm"],
            ["Material", "Mohair 70% / Nylon 30%"],
            ["Seller", "archive_store"],
            ["Feedback", "99.8% (1,234 ratings)"],
            ["Current Price", "3,111 YEN"],
            ["Buyout", "25,000 YEN"],
            ["End Time", "2026-09-02 22:14 JST"],
        ],
        "images": ["https://img.example/1.jpg", "https://img.example/2.jpg"],
    }
    observation = parse_detail_payload("buyee", "number-nine-1", payload)

    assert observation.title_original == payload["heading"]
    assert observation.category_hierarchy == ["Fashion", "Men", "Knitwear"]
    assert observation.item_type_observed == "Knitwear"
    assert observation.material_original == "Mohair 70% / Nylon 30%"
    assert observation.seller_feedback_percent == 99.8
    assert observation.seller_feedback_count == 1234
    assert observation.current_price == 3111
    assert observation.buyout_price == 25000
    assert observation.raw == payload


def test_detail_cache_avoids_browser_fetch_and_propagates_full_title(tmp_path):
    repository = DetailRepository(tmp_path / "detail.db")
    observation = DetailObservation(
        source="buyee",
        source_listing_id="cached-1",
        observed_at="2026-08-31T00:00:00+00:00",
        title_original="Number (N)ine 2001SS Time Migration Skull Mohair Knit Size 3",
        item_type_observed="knitwear",
        material_original="mohair",
        raw={
            "heading": "Number (N)ine 2001SS Time Migration Skull Mohair Knit Size 3",
            "labels": [["Item Type", "knitwear"], ["Material", "mohair"]],
        },
    )
    repository.put(observation)
    listing = LiveListing(
        source="buyee",
        source_listing_id="cached-1",
        url="https://buyee.jp/item/cached-1",
        title_original="Number (N)ine Time Migration...",
        title_normalized="number n ine time migration",
        brand="Number (N)ine",
        category="unknown",
        current_price=3111,
        buyout_price=None,
        starting_price=3111,
        seller_name=None,
        condition=None,
    )

    assert asyncio.run(BuyeeDetailService(repository).enrich([listing])) == (1, 0)
    assert listing.title_original == observation.title_original
    assert listing.raw["stage_b"]["observed"]["raw"] == observation.raw


def test_stage_b_is_candidate_only_and_improves_item_identity(monkeypatch, tmp_path):
    rejected = LiveListing(
        source="buyee",
        source_listing_id="generic-1",
        url="https://buyee.jp/item/generic-1",
        title_original="Number (N)ine basic t-shirt",
        title_normalized="number n ine basic t shirt",
        brand="Number (N)ine",
        category="menswear",
        current_price=3000,
        buyout_price=None,
        starting_price=3000,
        seller_name=None,
        condition=None,
    )
    candidate = LiveListing(
        source="buyee",
        source_listing_id="candidate-1",
        url="https://buyee.jp/item/candidate-1",
        title_original="Number (N)ine 2001SS Time Migration skull...",
        title_normalized="number n ine 2001ss time migration skull",
        brand="Number (N)ine",
        category="unknown",
        current_price=3111,
        buyout_price=None,
        starting_price=3111,
        seller_name=None,
        condition=None,
    )
    detail_calls = []
    comp_profiles = []

    async def fake_run_once(self, query, persist=False):
        return [rejected, candidate]

    async def fake_detail(self, listings):
        detail_calls.extend(item.source_listing_id for item in listings)
        apply_detail(
            listings[0],
            DetailObservation(
                source="buyee",
                source_listing_id="candidate-1",
                observed_at="2026-08-31T00:00:00+00:00",
                title_original="Number (N)ine 2001SS Time Migration Skull Mohair Knit Size 3",
                description_original="Skull motif mohair pullover knit",
                item_type_observed="knitwear",
                material_original="mohair",
            ),
        )
        return 1, 0

    async def fake_comps(self, profile, minimum_candidates=8):
        comp_profiles.append(profile)
        return []

    monkeypatch.setattr("buyeeye.pipeline.LiveMarketLoop.run_once", fake_run_once)
    monkeypatch.setattr("buyeeye.pipeline.BuyeeDetailService.enrich", fake_detail)
    monkeypatch.setattr("buyeeye.pipeline.CompService.retrieve", fake_comps)

    result = asyncio.run(scan_once("Number (N)ine", tmp_path / "stage.db", send_alerts=False))

    assert detail_calls == ["candidate-1"]
    assert result.stage_a_rejected == 1
    assert result.stage_b_candidates == 1
    assert result.stage_b_enriched == 1
    assert result.comp_queried == 1
    assert len(comp_profiles) == 2
    assert all(profile.item_type.value == "knitwear" for profile in comp_profiles)
    assert candidate.title_original.endswith("Mohair Knit Size 3")


def test_stage_b_failure_degrades_without_crashing(monkeypatch, tmp_path):
    listing = LiveListing(
        source="buyee",
        source_listing_id="detail-failure-1",
        url="https://buyee.jp/item/detail-failure-1",
        title_original="Number (N)ine 08AW A Closed Feeling destroy mohair jacket",
        title_normalized="number n ine 08aw a closed feeling destroy mohair jacket",
        brand="Number (N)ine",
        category="menswear",
        current_price=4000,
        buyout_price=None,
        starting_price=4000,
        seller_name=None,
        condition=None,
    )

    async def fake_run_once(self, query, persist=False):
        return [listing]

    async def fake_detail(self, listings):
        return 0, 1

    async def fake_comps(self, profile, minimum_candidates=8):
        return []

    monkeypatch.setattr("buyeeye.pipeline.LiveMarketLoop.run_once", fake_run_once)
    monkeypatch.setattr("buyeeye.pipeline.BuyeeDetailService.enrich", fake_detail)
    monkeypatch.setattr("buyeeye.pipeline.CompService.retrieve", fake_comps)

    result = asyncio.run(scan_once("Number (N)ine", tmp_path / "failure.db", send_alerts=False))

    assert result.stage_b_candidates == 1
    assert result.stage_b_enriched == 0
    assert result.stage_b_failed == 1
    assert result.comp_queried == 1
    assert result.new_rows == 1
    assert listing.raw["stage_b"]["status"] == "failed"
    assert listing.comp_confidence in {"none", "low"}


def test_stage_b_failure_caps_comp_confidence():
    listing = LiveListing(
        source="buyee",
        source_listing_id="failed-confidence-1",
        url="https://buyee.jp/item/failed-confidence-1",
        title_original="Number (N)ine 2001SS Time Migration t-shirt",
        title_normalized="number n ine 2001ss time migration t shirt",
        brand="Number (N)ine",
        category="menswear",
        current_price=5000,
        buyout_price=None,
        starting_price=5000,
        seller_name=None,
        condition=None,
        raw={"stage_b": {"status": "failed", "error_type": "TimeoutError"}},
    )
    comps = [
        Comp("yahoo_ended", listing.title_original, price, 1.0, 10)
        for price in (20_000, 25_000, 30_000)
    ]

    evaluation = evaluate_listing(listing, comps)

    assert evaluation.comps.confidence == "low"
    assert "Stage B identity unavailable" in evaluation.comps.reason


def test_time_migration_archive_opportunity_routes_with_seller_warning():
    listing = LiveListing(
        source="buyee",
        source_listing_id="time-migration-live",
        url="https://buyee.jp/item/time-migration-live",
        title_original="希少初期NUMBER (N)INE 2001SSタイム期 散弾銃tシャツ②2ナンバーナイン",
        title_normalized="number nine 2001ss time migration shotgun t shirt",
        brand="Number (N)ine",
        category="menswear",
        current_price=3111,
        buyout_price=None,
        starting_price=1,
        seller_name="st",
        condition="No obvious damages/dirt",
        time_remaining="6 day(s)",
        raw={
            "seller_feedback_percent": 84.4,
            "seller_feedback_count": 526,
            "detail_photo_count": 3,
        },
    )
    comps = [
        Comp(
            "grailed_sold",
            listing.title_original,
            price,
            1.0,
            30,
            original_price=usd,
            original_currency="USD",
        )
        for price, usd in ((10_370, 70), (17_778, 120), (22_815, 154))
    ]
    japan_comps = [
        Comp("yahoo_ended", listing.title_original, price, 1.0, 30)
        for price in (7777, 20_000, 25_000, 30_000, 35_000)
    ]

    evaluation = evaluate_listing(listing, comps, japan_comps=japan_comps)

    assert evaluation.auth_risk.level == "medium"
    assert any("84.4% across 526 ratings" in reason for reason in evaluation.auth_risk.reasons)
    assert evaluation.economics.resale_median_jpy == 17_778
    assert evaluation.economics.multiple is None
    assert evaluation.japan_comps.median_jpy == 25_000
    assert evaluation.directional.thesis is None
    assert evaluation.auction_maturity.level == "low"
    assert _route_evaluation(listing, evaluation) == (
        "grails",
        "early archive discovery; current bid is provisional",
    )


def test_cheap_desirable_margiela_can_route_without_us_fmv():
    listing = LiveListing(
        source="buyee",
        source_listing_id="margiela-asymmetric",
        url="https://buyee.jp/item/margiela-asymmetric",
        title_original="Maison Martin Margiela 本人期 AIDS archive graphic t-shirt",
        title_normalized="maison martin margiela aids archive graphic t shirt",
        brand="Maison Martin Margiela",
        category="menswear",
        current_price=1500,
        buyout_price=None,
        starting_price=1500,
        seller_name=None,
        condition="Used",
        time_remaining="8 hour(s)",
        raw={"detail_photo_count": 4, "seller_feedback_count": 300},
    )

    evaluation = evaluate_listing(listing)

    assert evaluation.comps.confidence == "none"
    assert evaluation.economics.resale_median_jpy is None
    assert evaluation.directional.us_valuation_state == "us_resale_unverified"
    assert evaluation.directional.thesis == "asymmetric_value"
    assert _route_evaluation(listing, evaluation)[0] == "grails"


def test_generic_margiela_at_normal_price_without_comps_is_suppressed():
    listing = LiveListing(
        source="buyee",
        source_listing_id="margiela-generic",
        url="https://buyee.jp/item/margiela-generic",
        title_original="Maison Margiela basic logo t-shirt",
        title_normalized="maison margiela basic logo t shirt",
        brand="Maison Martin Margiela",
        category="menswear",
        current_price=22_000,
        buyout_price=None,
        starting_price=22_000,
        seller_name=None,
        condition="Used",
        time_remaining="7 day(s)",
    )

    evaluation = evaluate_listing(listing)

    assert evaluation.directional.thesis is None
    assert _route_evaluation(listing, evaluation) is None


def test_archive_grail_can_route_without_resale_proof():
    listing = LiveListing(
        source="buyee",
        source_listing_id="archive-grail-no-comps",
        url="https://buyee.jp/item/archive-grail-no-comps",
        title_original="Number (N)ine 08AW A Closed Feeling destroy mohair patchwork jacket",
        title_normalized="number nine a closed feeling destroy mohair patchwork jacket",
        brand="Number (N)ine",
        category="menswear",
        current_price=40_000,
        buyout_price=None,
        starting_price=40_000,
        seller_name=None,
        condition="Used",
        time_remaining="5 day(s)",
    )

    evaluation = evaluate_listing(listing)

    assert evaluation.economics.resale_median_jpy is None
    assert evaluation.desirability.score >= 82
    assert _route_evaluation(listing, evaluation) == (
        "grails",
        "early archive discovery; current bid is provisional",
    )


def test_auth_sensitive_silver_with_implausible_price_routes_to_review():
    listing = LiveListing(
        source="buyee",
        source_listing_id="chrome-review",
        url="https://buyee.jp/item/chrome-review",
        title_original="Chrome Hearts keeper 925 silver ring",
        title_normalized="chrome hearts keeper 925 silver ring",
        brand="Chrome Hearts",
        category="jewelry",
        current_price=3000,
        buyout_price=None,
        starting_price=3000,
        seller_name=None,
        condition=None,
        raw={"detail_photo_count": 2, "seller_feedback_count": 100},
    )
    comps = [
        Comp("yahoo_ended", listing.title_original, price, 1.0, 10)
        for price in (80_000, 90_000, 100_000)
    ]

    evaluation = evaluate_listing(listing, comps)

    assert evaluation.auth_risk.level == "high"
    assert _route_evaluation(listing, evaluation)[0] == "review"


def test_controlled_query_universe_has_provenance_without_cartesian_explosion():
    root = Path(__file__).resolve().parents[1]
    searches = load_yaml(root / "config" / "searches.yaml")
    taxonomy = load_yaml(root / "config" / "fashion_taxonomy.yaml")

    universe = build_query_universe(searches, taxonomy)
    query_text = {spec.query.casefold() for spec in universe}

    assert len(universe) < 300
    assert len(query_text) == len(universe)
    assert all(spec.provenance and spec.family and spec.cooldown_minutes > 0 for spec in universe)
    for expected in (
        "number (n)ine",
        "ディオールオム",
        "サンローラン",
        "ヴェトモン",
        "クロムハーツ",
        "リックオウエンス",
        "マルタンマルジェラ",
        "イフシックスワズナイン",
        "ロエン",
        "バレンシアガ",
        "プラダ",
        "キングベイビー",
        "ガボラトリー",
        "ヴィンテージ バンドt",
    ):
        assert expected.casefold() in query_text


def test_query_scheduler_cooldown_and_persistent_global_dedupe(tmp_path):
    db_path = tmp_path / "runtime.db"
    high = QuerySpec("Number (N)ine", "test", "number_nine", 100, 60)
    explore = QuerySpec("ナンバーナイン", "test", "number_nine", 80, 60)

    assert choose_query([high, explore], db_path) == high
    record_query_result(high, object(), db_path)
    assert choose_query([high, explore], db_path) == explore

    first = mark_listing_seen("buyee", "abc", "sig-1", high.query, db_path)
    second = mark_listing_seen("buyee", "abc", "sig-1", explore.query, db_path)
    changed = mark_listing_seen("buyee", "abc", "sig-2", explore.query, db_path)
    assert first.is_duplicate is False
    assert second.is_duplicate is True and second.unchanged is True
    assert changed.is_duplicate is False and changed.unchanged is False


def test_observed_us_sold_source_excludes_asks_and_non_us_records(tmp_path):
    path = tmp_path / "us_sold.yaml"
    path.write_text(
        """
sold_comps:
  - {listing_id: 'sold-1', market: US, status: sold, title: 'Number (N)ine Time Migration T-Shirt', sold_price_usd: 120, query_terms: ['number (n)ine', 'time migration'], url: 'https://www.grailed.com/listings/sold-1'}
  - {listing_id: 'ask-1', market: US, status: active, title: 'Active reseller ask', sold_price_usd: 999, query_terms: ['number (n)ine'], url: 'https://example.com/ask'}
  - {listing_id: 'jp-1', market: JP, status: sold, title: 'Japanese sale', sold_price_usd: 20, query_terms: ['number (n)ine'], url: 'https://example.com/jp'}
""",
        encoding="utf-8",
    )

    records = asyncio.run(
        ObservedUSSoldSource(path, jpy_to_usd=0.01).fetch(
            "Number (N)ine Time Migration t shirt"
        )
    )

    assert len(records) == 1
    assert records[0].source == "grailed_sold"
    assert records[0].original_currency == "USD"
    assert records[0].original_price == 120
    assert records[0].price_jpy == 12_000


def test_discord_alert_is_compact_and_thesis_led(monkeypatch):
    listing = LiveListing(
        source="buyee",
        source_listing_id="compact-card",
        url="https://buyee.jp/item/compact-card",
        title_original="Number (N)ine 2001SS Time Migration Bullet T-Shirt",
        title_normalized="number nine time migration bullet t shirt",
        brand="Number (N)ine",
        category="menswear",
        current_price=3111,
        buyout_price=None,
        starting_price=1,
        seller_name="st",
        condition="Used",
        time_remaining="8 hour(s)",
        raw={
            "seller_feedback_percent": 84.4,
            "seller_feedback_count": 526,
            "detail_photo_count": 3,
        },
    )
    comps = [
        Comp(
            "grailed_sold",
            listing.title_original,
            price,
            1.0,
            30,
            source_listing_id=str(index),
            url=f"https://www.grailed.com/listings/{index}",
            original_price=usd,
            original_currency="USD",
        )
        for index, (price, usd) in enumerate(((10_370, 70), (17_778, 120), (22_815, 154)))
    ]
    evaluation = evaluate_listing(listing, comps)
    captured = {}

    monkeypatch.setattr(
        "buyeeye.discord_alerts._webhooks", lambda: {"grails": "https://discord.invalid"}
    )

    def capture(_url, payload):
        captured.update(payload)
        return True, 204, 0.01

    monkeypatch.setattr("buyeeye.discord_alerts._send_webhook", capture)

    sent, _, _ = send_listing_alert(
        listing,
        "grails",
        "fashion-relevant piece with unusually low absolute downside",
        evaluation.desirability.score,
        evaluation,
    )
    embed = captured["embeds"][0]

    assert sent is True
    assert "ASYMMETRIC VALUE" in embed["description"]
    assert len(embed["fields"]) <= 5
    assert sum("seller 84.4%" in field["value"] for field in embed["fields"]) == 1
    assert any(field["name"] == "U.S. sold / upside" for field in embed["fields"])


def _margiela_auction(
    *,
    listing_id: str,
    time_remaining: str,
    current_price: int = 1000,
    buyout_price: int | None = None,
) -> LiveListing:
    return LiveListing(
        source="buyee",
        source_listing_id=listing_id,
        url=f"https://buyee.jp/item/{listing_id}",
        title_original="Maison Martin Margiela 本人期 AIDS archive graphic t-shirt",
        title_normalized="maison martin margiela aids archive graphic t shirt",
        brand="Maison Martin Margiela",
        category="menswear",
        current_price=current_price,
        buyout_price=buyout_price,
        starting_price=1000,
        seller_name="archive-seller",
        condition="Used",
        time_remaining=time_remaining,
        bid_count=1,
        watch_count=2,
        raw={"detail_photo_count": 5, "seller_feedback_count": 300},
    )


def test_early_open_auction_cannot_route_on_low_provisional_bid():
    listing = _margiela_auction(listing_id="early-open", time_remaining="6 day(s)")

    evaluation = evaluate_listing(listing)

    assert evaluation.auction_maturity.level == "low"
    assert evaluation.auction_maturity.price_actionable is False
    assert evaluation.economics.landed_cost_jpy is None
    assert evaluation.economics.multiple is None
    assert evaluation.directional.thesis is None
    assert _route_evaluation(listing, evaluation) is None


def test_actionable_buyout_is_not_suppressed_by_long_time_remaining():
    listing = _margiela_auction(
        listing_id="early-buyout", time_remaining="6 day(s)", buyout_price=4000
    )

    evaluation = evaluate_listing(listing)

    assert evaluation.auction_maturity.auction_type == "fixed_price"
    assert evaluation.auction_maturity.price_actionable is True
    assert evaluation.directional.thesis == "asymmetric_value"
    assert _route_evaluation(listing, evaluation)[0] == "buyouts"


def test_open_auction_becomes_economically_actionable_inside_maturity_window():
    listing = _margiela_auction(listing_id="mature-open", time_remaining="8 hour(s)")

    evaluation = evaluate_listing(listing)

    assert evaluation.auction_maturity.level == "medium"
    assert evaluation.auction_maturity.price_actionable is True
    assert evaluation.economics.landed_cost_jpy is not None
    assert evaluation.directional.thesis == "asymmetric_value"
    assert _route_evaluation(listing, evaluation)[0] == "grails"


def test_ending_soon_remains_distinct_from_auction_maturity():
    listing = LiveListing(
        source="buyee",
        source_listing_id="ending-time-migration",
        url="https://buyee.jp/item/ending-time-migration",
        title_original="希少初期 NUMBER (N)INE 2001SS Time Migration shotgun t-shirt archive",
        title_normalized="number nine 2001ss time migration shotgun t shirt",
        brand="Number (N)ine",
        category="menswear",
        current_price=3111,
        buyout_price=None,
        starting_price=1,
        seller_name="st",
        condition="Used",
        time_remaining="45 minute(s)",
        bid_count=5,
        watch_count=12,
        raw={"detail_photo_count": 3, "seller_feedback_count": 526},
    )

    evaluation = evaluate_listing(listing)

    assert evaluation.auction_maturity.level == "high"
    assert evaluation.auction_maturity.remaining_minutes == 45
    assert evaluation.desirability.score >= 70
    assert _route_evaluation(listing, evaluation)[0] == "ending"


def test_early_archive_watch_omits_provisional_flip_economics(monkeypatch):
    listing = LiveListing(
        source="buyee",
        source_listing_id="early-archive-watch",
        url="https://buyee.jp/item/early-archive-watch",
        title_original="Number (N)ine 08AW A Closed Feeling destroy mohair patchwork jacket",
        title_normalized="number nine a closed feeling destroy mohair patchwork jacket",
        brand="Number (N)ine",
        category="menswear",
        current_price=1200,
        buyout_price=None,
        starting_price=1000,
        seller_name="archive-seller",
        condition="Used",
        time_remaining="5 day(s)",
        bid_count=1,
        watch_count=8,
    )
    evaluation = evaluate_listing(listing)
    route = _route_evaluation(listing, evaluation)
    captured = {}
    monkeypatch.setattr(
        "buyeeye.discord_alerts._webhooks", lambda: {"grails": "https://discord.invalid"}
    )

    def capture(_url, payload):
        captured.update(payload)
        return True, 204, 0.01

    monkeypatch.setattr("buyeeye.discord_alerts._send_webhook", capture)
    send_listing_alert(listing, route[0], route[1], evaluation.desirability.score, evaluation)
    embed = captured["embeds"][0]
    rendered = " ".join(field["value"] for field in embed["fields"])

    assert route == ("grails", "early archive discovery; current bid is provisional")
    assert evaluation.economics.multiple is None
    assert "ARCHIVE WATCH" in embed["description"]
    assert "EARLY AUCTION" in rendered
    assert "×" not in rendered


def test_generic_cheap_early_auction_is_suppressed():
    listing = LiveListing(
        source="buyee",
        source_listing_id="generic-cheap-early",
        url="https://buyee.jp/item/generic-cheap-early",
        title_original="Maison Margiela basic logo t-shirt",
        title_normalized="maison margiela basic logo t shirt",
        brand="Maison Martin Margiela",
        category="menswear",
        current_price=500,
        buyout_price=None,
        starting_price=500,
        seller_name=None,
        condition="Used",
        time_remaining="7 day(s)",
    )

    evaluation = evaluate_listing(listing)

    assert evaluation.auction_maturity.level == "low"
    assert _route_evaluation(listing, evaluation) is None
