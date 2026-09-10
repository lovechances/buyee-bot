from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from sqlmodel import select

from buyeeye.db import Listing, get_session, save_listings
from buyeeye.detail_enrichment import (
    BuyeeDetailService,
    DetailCache,
    DetailIdentityError,
    DetailRepository,
    apply_detail,
    detail_payload_script,
    parse_detail_payload,
)
from buyeeye.discord_alerts import build_listing_alert_payload
from buyeeye.live_sources import (
    BuyeeAuthenticatedSource,
    image_url_from_attributes,
    parser_failure_shape_script,
    search_card_payload_script,
)

FIXTURES = Path(__file__).parent / "fixtures"
ITEM_URL = "https://buyee.jp/item/jdirectitems/auction/a111111111"
PANTS = "https://cdnyauction.buyee.jp/images.auctions.yahoo.co.jp/image/pants.jpg"
JACKET = "https://cdnyauction.buyee.jp/images.auctions.yahoo.co.jp/image/jacket.jpg"


def _cards():
    return BuyeeAuthenticatedSource._parse_listing_html(
        (FIXTURES / "buyee_bound_search_cards.html").read_text(), "Needles"
    )


def _payload(**changes):
    return {
        "association_version": 1,
        "page_url": ITEM_URL,
        "canonical_url": ITEM_URL,
        "heading": "Needles 20SS orange velour track pants Appraisal option availableNEW",
        "labels": [["Seller", "pants-seller"], ["Buyout Price", "9,561 YEN"]],
        "primary_images": [{"provenance": "og_image", "attributes": {"src": PANTS}}],
        "images": [JACKET, PANTS],
        **changes,
    }


def test_adjacent_cards_bind_title_price_image_and_omit_ambiguous_or_missing():
    listings = _cards()
    assert [(item.source_listing_id, item.current_price, item.image_url) for item in listings] == [
        ("a111111111", 9561, PANTS), ("b222222222", 20000, JACKET),
        ("c333333333", 4000, None),
    ]
    assert listings[0].title_original == "Needles 20SS orange velour track pants"
    assert listings[2].title_original == "Margiela NEW shirt"
    assert listings[0].raw["search_card"]["image_url"] == PANTS
    # Known unsupported and unhydrated cards preceding a valid auction are
    # skipped individually, never a reason to stop processing later cards.
    assert listings[0].raw["search_rank_on_page"] == 3
    assert "thumbnail" not in build_listing_alert_payload(listings[2], "buyouts", "test", 70)["embeds"][0]


def test_mixed_source_links_inside_one_card_cannot_borrow_unsupported_title():
    soup = BeautifulSoup((FIXTURES / "buyee_bound_search_cards.html").read_text(), "lxml")
    first_card = soup.select_one(".g-main > ul.auctionSearchResult > li.itemCard")
    first_card.append(soup.new_tag("a", href="https://buyee.jp/item/jdirectitems/auction/m666666666"))
    listings = BuyeeAuthenticatedSource._parse_listing_html(str(soup), "Needles")
    assert [x.source_listing_id for x in listings] == ["a111111111", "b222222222", "c333333333"]


def test_exact_browser_extraction_scripts_against_offline_fixtures():
    # No persistent profile, credentials, display, or outbound requests. These
    # execute the production scripts instead of mocking the extracted rows.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.abort())
            page.set_content((FIXTURES / "buyee_bound_search_cards.html").read_text())
            rows = page.evaluate(search_card_payload_script())
            shape = page.evaluate(parser_failure_shape_script(), ".g-main > ul.auctionSearchResult > li.itemCard")
            assert shape["primary_paypay_links"] == 1
            assert shape["primary_missing_href"] == 1
            assert shape["primary_supported_links"] == 7
            assert shape["all_auction_links"] == 8
            parsed = BuyeeAuthenticatedSource._items_from_browser_payload(rows, "Needles")
            assert [(x.source_listing_id, x.image_url) for x in parsed] == [
                (x.source_listing_id, x.image_url) for x in _cards()
            ]
            assert parsed[0].raw["search_rank_on_page"] == 3
            page.set_content((FIXTURES / "buyee_bound_detail.html").read_text())
            payload = page.evaluate(detail_payload_script())
            assert payload["page_url"] == "about:blank"
            # Only the test transport differs: fixture is local, not a request.
            payload["page_url"] = ITEM_URL
            observed = parse_detail_payload("buyee", "a111111111", payload)
            assert observed.image_urls == [PANTS]
            assert observed.seller_name == "pants-seller"
            assert observed.buyout_price == 9561
            assert observed.title_original == "Needles 20SS orange velour track pants"
        finally:
            browser.close()


def test_whole_document_pants_jacket_fault_cannot_replace_bound_product_image():
    listing = _cards()[0]
    observation = parse_detail_payload("buyee", listing.source_listing_id, _payload())
    apply_detail(listing, observation)
    assert listing.image_url == PANTS
    assert listing.seller_name == "pants-seller"
    assert listing.buyout_price == 9561
    assert listing.raw["detail_photo_count"] == 1
    assert listing.raw["detail_photo_coverage"] == "primary_only"
    assert observation.raw["images"] == [JACKET, PANTS]
    assert build_listing_alert_payload(listing, "buyouts", "test", 70)["embeds"][0]["thumbnail"]["url"] == PANTS


@pytest.mark.parametrize("field", ["page_url", "canonical_url", "og_url"])
def test_conflicting_page_identity_is_rejected_before_mutation(field):
    with pytest.raises(DetailIdentityError):
        parse_detail_payload("buyee", "a111111111", _payload(**{field: ITEM_URL.replace("a111111111", "b222222222")}))


@pytest.mark.parametrize("source, listing_id", [("yahoo", "a111111111"), ("buyee", "b222222222")])
def test_reordered_or_cross_source_detail_cannot_merge_with_different_listing(source, listing_id):
    listing = _cards()[0]
    before = copy.deepcopy(listing)
    observation = parse_detail_payload("buyee", "a111111111", _payload())
    observation.source = source
    observation.source_listing_id = listing_id
    with pytest.raises(DetailIdentityError):
        apply_detail(listing, observation)
    assert listing == before


def test_cache_keeps_current_card_image_and_price_but_enriches_matching_identity(tmp_path):
    repository = DetailRepository(tmp_path / "cache.db")
    observed = parse_detail_payload("buyee", "a111111111", _payload())
    repository.put(observed)
    listing = _cards()[0]
    listing.image_url = PANTS + "?revision=current"
    listing.buyout_price = 10000
    assert asyncio.run(BuyeeDetailService(repository).enrich([listing])) == (1, 0)
    assert listing.image_url.endswith("?revision=current")
    assert listing.buyout_price == 10000
    assert listing.seller_name == "pants-seller"


def test_cache_never_relabels_another_source_or_listing(tmp_path):
    repository = DetailRepository(tmp_path / "cache.db")
    observed = parse_detail_payload("buyee", "a111111111", _payload())
    repository.put(observed)
    assert repository.get_fresh("yahoo", "a111111111") is None
    with get_session(repository.db_path) as session:
        row = session.get(DetailCache, repository.key("buyee", "a111111111"))
        row.source_listing_id = "b222222222"
        session.add(row)
        session.commit()
    assert repository.get_fresh("buyee", "a111111111") is None


def test_partial_detail_without_bound_image_keeps_card_not_global_document_image():
    listing = _cards()[0]
    observation = parse_detail_payload("buyee", "a111111111", _payload(primary_images=[]))
    apply_detail(listing, observation)
    assert listing.image_url == PANTS
    assert listing.raw["detail_photo_count"] == 0
    missing = _cards()[2]
    payload = _payload(page_url=missing.url, canonical_url=missing.url, primary_images=[])
    apply_detail(missing, parse_detail_payload("buyee", missing.source_listing_id, payload))
    assert missing.image_url is None


def test_explicit_image_integrity_clear_removes_old_persisted_thumbnail(tmp_path):
    listing = _cards()[2]
    listing.image_url = JACKET
    db_path = tmp_path / "listings.db"
    save_listings([listing], db_path)
    listing.image_url = None
    save_listings([listing], db_path)
    with get_session(db_path) as session:
        row = session.exec(select(Listing)).one()
        assert row.image_url is None


def test_source_provided_og_image_is_not_claimed_visually_authenticated():
    # A coherent canonical page whose seller/metadata actually supplies a wrong
    # image is a source-content problem. This parser is not a vision classifier.
    observation = parse_detail_payload("buyee", "a111111111", _payload(
        primary_images=[{"provenance": "og_image", "attributes": {"src": JACKET}}]
    ))
    assert observation.image_urls == [JACKET]
    assert observation.raw["primary_images"][0]["provenance"] == "og_image"


def test_generic_social_image_and_unscoped_legacy_images_are_not_product_evidence():
    payload = _payload(primary_images=[{
        "provenance": "og_image", "attributes": {"src": "https://cdn.buyee.jp/social-share.png"},
    }])
    assert parse_detail_payload("buyee", "a111111111", payload).image_urls == []
    legacy = {"heading": "Needles pants", "images": [JACKET, PANTS]}
    assert parse_detail_payload("buyee", "a111111111", legacy).image_urls == []


def test_declared_image_changes_do_not_reuse_stale_current_src():
    assert image_url_from_attributes({"src": PANTS, "current_src": JACKET}) == PANTS
    assert image_url_from_attributes({"data-src": PANTS, "src": JACKET}) == PANTS
    assert image_url_from_attributes({"srcset": f"{PANTS} 1200w, {JACKET} 100w"}) == PANTS


def test_observed_auction_id_conflict_is_not_advisory_missing_detail():
    with pytest.raises(DetailIdentityError):
        parse_detail_payload("buyee", "a111111111", _payload(
            visible_text="Auction ID\nb222222222\nSeller\nother-seller"
        ))


def test_unprovenanced_image_merge_fails_atomically_before_title_price_seller():
    listing = _cards()[0]
    before = copy.deepcopy(listing)
    observation = parse_detail_payload("buyee", "a111111111", _payload())
    observation.image_urls = [JACKET]
    observation.title_original = "Wrong jacket"
    observation.buyout_price = 99999
    observation.seller_name = "other-seller"
    with pytest.raises(DetailIdentityError):
        apply_detail(listing, observation)
    assert listing == before


def test_failed_cross_item_detail_does_not_poison_cache_or_following_candidate(monkeypatch, tmp_path):
    from types import SimpleNamespace

    listings = _cards()[:2]

    class Page:
        async def goto(self, url, **_kwargs):
            self.url = url

        async def wait_for_timeout(self, *_args):
            pass

        async def wait_for_load_state(self, *_args, **_kwargs):
            pass

        async def evaluate(self, _script):
            # First navigation lands on another item. The second succeeds.
            return _payload(
                page_url=listings[1].url,
                canonical_url=listings[1].url,
                heading="Rick Owens black leather jacket",
                labels=[["Seller", "jacket-seller"], ["Buyout Price", "20,000 YEN"]],
                primary_images=[{"provenance": "og_image", "attributes": {"src": JACKET}}],
            )

        async def close(self):
            pass

    class Context:
        async def new_page(self):
            return Page()

        async def close(self):
            pass

    class Chromium:
        async def launch_persistent_context(self, *_args, **_kwargs):
            return Context()

    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *_args):
            pass

    async def no_restore(*_args):
        pass

    monkeypatch.setattr("playwright.async_api.async_playwright", Playwright)
    monkeypatch.setattr("buyeeye.detail_enrichment.ensure_browser_dir", lambda: None)
    monkeypatch.setattr("buyeeye.detail_enrichment.restore_session_cookies_async", no_restore)
    repository = DetailRepository(tmp_path / "detail.db")
    assert asyncio.run(BuyeeDetailService(repository).enrich(listings)) == (1, 1)
    assert listings[0].title_original == "Needles 20SS orange velour track pants"
    assert listings[0].image_url == PANTS
    assert listings[0].raw["stage_b"]["identity_conflict"] is True
    assert repository.get_fresh("buyee", listings[0].source_listing_id) is None
    assert listings[1].seller_name == "jacket-seller"
    assert listings[1].image_url == JACKET
    assert listings[1].buyout_price == 20000
    assert repository.get_fresh("buyee", listings[1].source_listing_id) is not None
