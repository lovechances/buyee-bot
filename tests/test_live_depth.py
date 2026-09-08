from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from buyeeye.detail_enrichment import (
    BuyeeDetailService,
    DetailObservation,
    DetailRepository,
)
from buyeeye.live_sources import (
    BuyeeAuthenticatedSource,
    LiveListing,
    _bound_page_number,
    _next_page_number_from_pagination_html,
)

ROOT = Path(__file__).resolve().parents[1]


def _listing(
    listing_id: str,
    *,
    current_price: int = 6_980,
    buyout_price: int | None = 8_000,
    time_remaining: str = "2 day(s)",
    bid_count: int | None = 3,
    watch_count: int | None = 8,
) -> LiveListing:
    return LiveListing(
        source="buyee",
        source_listing_id=listing_id,
        url=f"https://buyee.jp/item/jdirectitems/auction/{listing_id}",
        title_original="Maison Margiela denim",
        title_normalized="maison margiela denim",
        brand="Maison Margiela",
        category="denim",
        current_price=current_price,
        buyout_price=buyout_price,
        starting_price=5_000,
        seller_name=None,
        condition=None,
        time_remaining=time_remaining,
        bid_count=bid_count,
        watch_count=watch_count,
        raw={},
    )


def test_search_fixture_groups_cards_prefers_true_title_and_records_rank():
    html = (ROOT / "tests/fixtures/buyee_search_pagination.html").read_text(
        encoding="utf-8"
    )

    listings = BuyeeAuthenticatedSource._parse_listing_html(html, "Maison Margiela")

    assert [listing.source_listing_id for listing in listings] == [
        "a111111111",
        "b222222222",
    ]
    assert listings[0].title_original == "Maison Margiela reconstructed denim jacket"
    assert listings[0].title_original != "With Authentication 10% OFF"
    assert listings[0].current_price == 6_980
    assert listings[0].buyout_price == 8_000
    assert listings[0].raw["search_page"] == 1
    assert listings[0].raw["search_rank_on_page"] == 1
    assert listings[0].raw["search_rank"] == 1
    assert listings[1].raw["search_rank_on_page"] == 2
    assert listings[1].raw["search_rank"] == 2
    assert (
        BuyeeAuthenticatedSource._extract_card_title(
            {"title": "With Authentication 10% OFF"}
        )
        == ""
    )


def test_knockout_paginator_uses_bound_page_without_href():
    html = (ROOT / "tests/fixtures/buyee_search_pagination.html").read_text(
        encoding="utf-8"
    )

    assert _bound_page_number('click: search.bind($data, {"page":2})') == 2
    assert _bound_page_number("click: search.bind($data, {'page':'3'})") == 3
    assert _bound_page_number("click: unrelated") is None
    assert _next_page_number_from_pagination_html(html, 1) == 2
    assert _next_page_number_from_pagination_html(html, 2) == 3
    assert _next_page_number_from_pagination_html(html, 3) is None


@pytest.mark.parametrize(("max_pages", "expected_clicks"), [(1, 0), (2, 1)])
def test_browser_pagination_never_clicks_beyond_page_budget(
    monkeypatch, max_pages, expected_clicks
):
    class FakeLink:
        def __init__(self, page, target):
            self.page = page
            self.target = target

        async def get_attribute(self, name):
            assert name == "data-bind"
            return f'click: search.bind($data, {{"page":{self.target}}})'

        async def click(self, timeout):
            assert timeout == 15_000
            self.page.clicks += 1
            self.page.current_page = self.target

    class FakeLinks:
        def __init__(self, page):
            self.link = FakeLink(page, page.current_page + 1)

        async def count(self):
            return 1

        def nth(self, index):
            assert index == 0
            return self.link

    class FakePaginator:
        def __init__(self, page):
            self.page = page

        @property
        def first(self):
            return self

        async def count(self):
            return 1

        async def evaluate(self, script):
            assert script == "node => node.outerHTML"
            target = self.page.current_page + 1
            return (
                '<nav class="search_page_navi"><div class="page_navi">'
                f'<span class="current">{self.page.current_page}</span>'
                f'<a data-bind=\'click: search.bind($data, {{"page":{target}}})\'>'
                f"{target}</a></div></nav>"
            )

    class FakePage:
        def __init__(self):
            self.current_page = 1
            self.clicks = 0

        async def goto(self, *_args, **_kwargs):
            return None

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        async def wait_for_function(self, expression, *, arg=None, timeout=None):
            assert "expected" in expression
            assert arg == {
                "expected": self.current_page,
                "before": f"signature-page-{self.current_page - 1}",
            }
            assert timeout == 30_000

        async def evaluate(self, script):
            if "const resultLinks" in script:
                listing_id = f"p{self.current_page}00000001"
                return [
                    {
                        "href": (
                            "https://buyee.jp/item/jdirectitems/auction/"
                            f"{listing_id}?conversionType="
                            "jdirectitems_auction_DirectSearch"
                        ),
                        "text": "Current Price 1,000 YEN Time Remaining 2 hour(s)",
                        "item_title": f"Page {self.current_page} listing",
                    }
                ]
            return f"signature-page-{self.current_page}"

        def locator(self, selector):
            if selector == "nav.search_page_navi":
                return FakePaginator(self)
            if selector == "nav.search_page_navi .page_navi a[data-bind]":
                return FakeLinks(self)
            raise AssertionError(f"unexpected locator: {selector}")

    class FakeContext:
        def __init__(self, page):
            self.page = page

        async def new_page(self):
            return self.page

        async def close(self):
            return None

    class FakeChromium:
        def __init__(self, context):
            self.context = context

        async def launch_persistent_context(self, *_args, **_kwargs):
            return self.context

    class FakePlaywright:
        def __init__(self, context):
            self.chromium = FakeChromium(context)

    class FakePlaywrightManager:
        def __init__(self, playwright):
            self.playwright = playwright

        async def __aenter__(self):
            return self.playwright

        async def __aexit__(self, *_args):
            return False

    async def no_op_restore(*_args, **_kwargs):
        return None

    page = FakePage()
    context = FakeContext(page)
    manager = FakePlaywrightManager(FakePlaywright(context))
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: manager)
    monkeypatch.setattr("buyeeye.live_sources.ensure_browser_dir", lambda: None)
    monkeypatch.setattr(
        "buyeeye.live_sources.restore_session_cookies_async", no_op_restore
    )

    source = BuyeeAuthenticatedSource(max_pages=max_pages)
    listings = asyncio.run(source._fetch_via_browser("Maison Margiela"))

    assert source.last_page_count == max_pages
    assert len(listings) == max_pages
    assert page.clicks == expected_clicks


def test_payload_dedupes_listing_ids_and_preserves_actual_dom_rank():
    common_text = "Current Price 1,000 YEN Time Remaining 2 hour(s)"
    rows = [
        {
            "href": "https://buyee.jp/item/jdirectitems/auction/a111111111",
            "text": f"First listing {common_text}",
            "item_title": "First listing",
            "search_page": 1,
            "search_rank_on_page": 1,
        },
        {
            "href": "https://buyee.jp/item/jdirectitems/auction/a111111111",
            "text": f"Duplicate listing {common_text}",
            "item_title": "Duplicate listing",
            "search_page": 2,
            "search_rank_on_page": 1,
        },
        {
            "href": "https://buyee.jp/item/jdirectitems/auction/b222222222",
            "text": f"Second listing {common_text}",
            "item_title": "Second listing",
            "search_page": 2,
            "search_rank_on_page": 2,
        },
    ]

    listings = BuyeeAuthenticatedSource._items_from_browser_payload(rows)

    assert [listing.source_listing_id for listing in listings] == [
        "a111111111",
        "b222222222",
    ]
    assert listings[0].raw["search_page"] == 1
    assert listings[0].raw["search_rank_on_page"] == 1
    assert listings[0].raw["search_rank"] == 1
    assert listings[1].raw["search_page"] == 2
    assert listings[1].raw["search_rank_on_page"] == 2
    assert listings[1].raw["search_rank"] == 2


def test_cached_detail_keeps_fresh_search_card_auction_state(tmp_path):
    repository = DetailRepository(tmp_path / "detail.db")
    repository.put(
        DetailObservation(
            source="buyee",
            source_listing_id="cached-price",
            observed_at=datetime.now(UTC).isoformat(),
            title_original="Maison Margiela reconstructed denim jacket",
            current_price=1_000,
            buyout_price=2_000,
            starting_price=500,
            bid_count=1,
            watch_count=2,
            raw={
                "heading": "Maison Margiela reconstructed denim jacket",
                "labels": [
                    ["Current Price", "1,000 YEN"],
                    ["Buyout Price", "2,000 YEN"],
                ],
                "visible_text": (
                    "Starting Price\n500 YEN\nNumber of Bids\n1\nWatch\n2"
                ),
            },
        )
    )
    listing = _listing(
        "cached-price",
        current_price=6_980,
        buyout_price=8_000,
        bid_count=7,
        watch_count=14,
    )

    assert asyncio.run(BuyeeDetailService(repository).enrich([listing])) == (1, 0)

    assert listing.current_price == 6_980
    assert listing.buyout_price == 8_000
    assert listing.bid_count == 7
    assert listing.watch_count == 14
    assert listing.title_original == "Maison Margiela reconstructed denim jacket"
    assert listing.raw["stage_b"]["retrieval"] == "cache_hit"
    assert listing.raw["stage_b"]["cache_hit"] is True
    assert listing.raw["stage_b"]["live_fetch"] is False
    assert listing.raw["stage_b"]["observed"]["current_price"] == 1_000


def test_closing_open_or_dual_auction_forces_refresh_but_fixed_buyout_does_not(
    tmp_path,
):
    service = BuyeeDetailService(
        DetailRepository(tmp_path / "detail.db"),
        force_open_auction_refresh_minutes=60,
    )

    assert service._force_live_refresh(
        _listing("closing", buyout_price=None, time_remaining="45 minute(s)")
    )
    assert not service._force_live_refresh(
        _listing("later", buyout_price=None, time_remaining="2 hour(s)")
    )
    assert service._force_live_refresh(
        _listing("dual", current_price=6_980, buyout_price=8_000, time_remaining="45 minute(s)")
    )
    assert not service._force_live_refresh(
        _listing("fixed", current_price=8_000, buyout_price=8_000, time_remaining="45 minute(s)")
    )


def test_closing_open_auction_bypasses_a_fresh_detail_cache():
    class RecordingRepository:
        def __init__(self):
            self.lookups: list[str] = []

        def get_fresh(self, source: str, listing_id: str) -> DetailObservation:
            self.lookups.append(listing_id)
            return DetailObservation(
                source=source,
                source_listing_id=listing_id,
                observed_at=datetime.now(UTC).isoformat(),
            )

    repository = RecordingRepository()
    service = BuyeeDetailService(repository, force_open_auction_refresh_minutes=60)

    closing = _listing("closing", buyout_price=None, time_remaining="45 minute(s)")
    later = _listing("later", buyout_price=None, time_remaining="2 hour(s)")

    assert service._cached_observation(closing) is None
    assert repository.lookups == []
    assert service._cached_observation(later) is not None
    assert repository.lookups == ["later"]
