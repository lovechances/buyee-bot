from __future__ import annotations

from types import SimpleNamespace

from buyeeye.discord_alerts import build_listing_alert_payload, send_listing_alert
from buyeeye.live_sources import LiveListing
from buyeeye.models import Comp


def _listing(
    *,
    current_price: int = 1_000,
    buyout_price: int | None = 4_000,
    time_remaining: str = "6 day(s)",
) -> LiveListing:
    return LiveListing(
        source="buyee",
        source_listing_id="discord-card",
        url="https://buyee.jp/item/discord-card",
        title_original="Maison Margiela 2003 archive graphic T-shirt",
        title_normalized="maison margiela 2003 archive graphic t shirt",
        brand="Maison Margiela",
        category="menswear",
        current_price=current_price,
        buyout_price=buyout_price,
        starting_price=1_000,
        seller_name="archive-seller",
        condition="Used",
        image_url="https://img.example/item.jpg",
        time_remaining=time_remaining,
        raw={"seller_feedback_percent": 98.7, "seller_feedback_count": 412},
    )


def _evaluation(
    *,
    actionable: bool = True,
    auction_type: str = "fixed_price",
    comp_confidence: str = "none",
    multiple: float | None = None,
    directional_confidence: str = "high",
    relationship: str = "core",
    comps: list[Comp] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        profile=SimpleNamespace(
            brand=SimpleNamespace(value="Maison Margiela", confidence=0.98),
            item_type=SimpleNamespace(value="t-shirt", confidence=0.9),
            relationship=SimpleNamespace(value=relationship),
        ),
        identity=SimpleNamespace(confidence="high"),
        economics=SimpleNamespace(
            opportunity_score=82,
            comp_confidence=comp_confidence,
            multiple=multiple,
        ),
        valuation=SimpleNamespace(confidence=comp_confidence, reason="test valuation"),
        directional=SimpleNamespace(
            score=88,
            thesis="asymmetric_value",
            landed_usd=43.0 if actionable else None,
            confidence=directional_confidence,
        ),
        auction_maturity=SimpleNamespace(
            auction_type=auction_type,
            level="high" if actionable else "low",
            price_actionable=actionable,
        ),
        auth_risk=SimpleNamespace(level="medium", reasons=["limited detail photos"]),
        comps=SimpleNamespace(
            low_jpy=15_000,
            high_jpy=45_000,
            matches=comps or [],
        ),
    )


def _rendered(payload: dict) -> str:
    embed = payload["embeds"][0]
    return "\n".join(
        [embed["title"], embed["description"]]
        + [f"{field['name']}\n{field['value']}" for field in embed["fields"]]
    )


def test_actionable_verified_card_is_compact_and_evidence_backed() -> None:
    comps = [
        Comp(
            source="grailed_sold",
            title=f"Verified sold piece {index}",
            price_jpy=price_jpy,
            similarity=0.9,
            recency_days=30,
            source_listing_id=str(index),
            url=f"https://grailed.example/sold/{index}",
            original_price=usd,
            original_currency="USD",
        )
        for index, (price_jpy, usd) in enumerate(
            ((15_000, 100), (30_000, 200), (45_000, 300)), start=1
        )
    ]
    payload = build_listing_alert_payload(
        _listing(),
        "buyouts",
        "core archive tee at an unusually attractive actionable price",
        82,
        _evaluation(comp_confidence="high", multiple=4.24, comps=comps),
    )
    embed = payload["embeds"][0]
    rendered = _rendered(payload)

    assert "¥4,000 BUYOUT" in embed["title"]
    assert "¥1,000 CURRENT BID" not in rendered
    assert "U.S. sold $100–$300" in rendered
    assert "est. 4.2×" in rendered
    assert rendered.count("core archive tee at an unusually attractive actionable price") == 1
    assert rendered.count("Identity HIGH") == 1
    assert rendered.count("Relationship CORE") == 1
    assert rendered.count("Risk MEDIUM") == 1
    assert rendered.count("seller 98.7% (412)") == 1
    assert rendered.count("https://grailed.example") == 2
    assert "OPEN ON BUYEE" in embed["description"]
    assert len(embed["fields"]) <= 5


def test_unverified_directional_card_never_invents_fmv() -> None:
    payload = build_listing_alert_payload(
        _listing(buyout_price=3_800),
        "buyouts",
        "confirmed diffusion piece with low absolute downside",
        58,
        _evaluation(relationship="diffusion", directional_confidence="medium"),
    )
    rendered = _rendered(payload)

    assert "¥3,800 BUYOUT" in rendered
    assert "U.S. resale unverified" in rendered
    assert "Directional confidence: MED" in rendered
    assert "Relationship DIFFUSION" in rendered
    assert "U.S. sold" not in rendered
    assert "FMV" not in rendered
    assert "×" not in rendered


def test_early_auction_marks_bid_provisional_and_hides_even_available_economics() -> None:
    comp = Comp(
        source="grailed_sold",
        title="Verified archive sale",
        price_jpy=45_000,
        similarity=0.9,
        recency_days=30,
        url="https://grailed.example/sold/archive",
        original_price=300,
        original_currency="USD",
    )
    listing = _listing(buyout_price=None, time_remaining="5 day(s)")
    evaluation = _evaluation(
        actionable=False,
        auction_type="open_auction",
        comp_confidence="high",
        multiple=12.5,
        comps=[comp],
    )
    payload = build_listing_alert_payload(
        listing,
        "grails",
        "exceptional archive relevance; current bid is provisional",
        91,
        evaluation,
    )
    rendered = _rendered(payload)

    assert "¥1,000 CURRENT BID" in rendered
    assert "EARLY AUCTION · Ends 5 day(s)" in rendered
    assert rendered.count("Current bid is provisional") == 1
    assert "U.S. sold $300" not in rendered
    assert "$300" not in rendered
    assert "https://grailed.example" not in rendered
    assert "12.5×" not in rendered
    assert "Landed" not in rendered


def test_yahoo_ended_evidence_cannot_be_rendered_as_us_sold_or_fmv() -> None:
    yahoo_comps = [
        Comp(
            source="yahoo_ended",
            title=f"Yahoo ended result {index}",
            price_jpy=100_000 + index * 1_000,
            similarity=0.9,
            recency_days=30,
            url=f"https://auctions.yahoo.co.jp/closed/{index}",
            original_price=100_000 + index * 1_000,
            original_currency="JPY",
        )
        for index in range(3)
    ]
    lone_us_sale = Comp(
        source="grailed_sold",
        title="One U.S. sale cannot borrow Yahoo confidence",
        price_jpy=90_000,
        similarity=0.9,
        recency_days=30,
        url="https://grailed.example/sold/lone",
        original_price=600,
        original_currency="USD",
    )
    payload = build_listing_alert_payload(
        _listing(),
        "buyouts",
        "directionally interesting acquisition",
        75,
        _evaluation(
            comp_confidence="high", multiple=3.5, comps=[*yahoo_comps, lone_us_sale]
        ),
    )
    rendered = _rendered(payload)

    assert "U.S. resale unverified" in rendered
    assert "U.S. sold" not in rendered
    assert "est. 3.5×" not in rendered
    assert "FMV" not in rendered
    assert "¥101,000" not in rendered
    assert "$600" not in rendered


def test_low_confidence_us_match_is_a_link_not_a_valuation_claim() -> None:
    weak_match = Comp(
        source="grailed_sold",
        title="One weakly matched sold listing",
        price_jpy=90_000,
        similarity=0.5,
        recency_days=200,
        url="https://grailed.example/sold/weak",
        original_price=600,
        original_currency="USD",
    )
    payload = build_listing_alert_payload(
        _listing(),
        "buyouts",
        "directionally interesting acquisition",
        75,
        _evaluation(comp_confidence="low", multiple=8.0, comps=[weak_match]),
    )
    rendered = _rendered(payload)

    assert "U.S. resale unverified" in rendered
    assert "https://grailed.example/sold/weak" in rendered
    assert "$600" not in rendered
    assert "8.0×" not in rendered


def test_payload_omits_blank_optional_fields_and_send_uses_pure_builder(monkeypatch) -> None:
    listing = _listing()
    listing.seller_name = None
    listing.raw = {}
    evaluation = _evaluation()
    captured: dict = {}
    monkeypatch.setattr(
        "buyeeye.discord_alerts._webhooks", lambda: {"buyouts": "https://discord.invalid"}
    )

    def capture(_url: str, payload: dict):
        captured.update(payload)
        return True, 204, 0.01

    monkeypatch.setattr("buyeeye.discord_alerts._send_webhook", capture)

    assert send_listing_alert(listing, "buyouts", "worth inspecting", 70, evaluation)[0]
    assert captured == build_listing_alert_payload(
        listing, "buyouts", "worth inspecting", 70, evaluation
    )
    assert all(field["value"].strip() for field in captured["embeds"][0]["fields"])
    assert not any(field["name"] == "Seller" for field in captured["embeds"][0]["fields"])
