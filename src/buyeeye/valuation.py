from __future__ import annotations

from typing import Any


def listing_price_usd(
    item_price_jpy: int | None, settings: dict[str, Any] | None = None
) -> float | None:
    """Approximate listing-only conversion, never a live FX/checkout quote."""
    fx = float((settings or {}).get("pricing", {}).get("fx_jpy_to_usd", 0.00675))
    if not item_price_jpy or item_price_jpy <= 0 or fx <= 0:
        return None
    return round(item_price_jpy * fx, 2)


def estimate_landed_cost_jpy(
    item_price_jpy: int, settings: dict[str, Any] | None = None
) -> dict[str, Any]:
    """An explicit budgeting scenario, not an observed invoice or shipping quote.

    Legacy percentage overrides remain accepted as estimates, but the default
    service estimate is fixed. A guarantee plan is an optional assumption;
    percentage insurance is not added again by default. Domestic shipping was
    previously configured but accidentally omitted.
    """
    config = settings or {}
    pricing = config.get("pricing", {})
    fee_percent = float(pricing.get("buyee_fee_percent", 0))
    service_fee = float(pricing.get("service_fee_jpy", 0 if fee_percent else 500))
    plan_fee = float(pricing.get("plan_fee_jpy", 500))
    domestic_shipping = float(pricing.get("domestic_shipping_jpy", 800))
    shipping = float(pricing.get("international_shipping_jpy", 1500))
    packaging = float(pricing.get("packaging_jpy", 0))
    insurance = float(pricing.get("insurance_percent", 0))
    duty = float(pricing.get("duty_percent", 0.08))
    reserve = float(pricing.get("reserve_percent", 0.05))

    item_cost = float(item_price_jpy)
    fee = service_fee + item_cost * fee_percent
    insurance_cost = item_cost * insurance
    duty_cost = item_cost * duty
    reserve_cost = item_cost * reserve
    landed = (
        item_cost
        + fee
        + plan_fee
        + domestic_shipping
        + shipping
        + packaging
        + insurance_cost
        + duty_cost
        + reserve_cost
    )
    return {
        "item_price_jpy": item_cost,
        "listing_only_usd": listing_price_usd(item_price_jpy, settings),
        "fx_jpy_to_usd": float(pricing.get("fx_jpy_to_usd", 0.00675)),
        "fx_basis": "configured estimate, not a live quote",
        "landed_jpy": landed,
        "estimated_landed_usd": round(landed * float(pricing.get("fx_jpy_to_usd", 0.00675)), 2),
        "fee_jpy": fee,
        "plan_jpy": plan_fee,
        "domestic_shipping_jpy": domestic_shipping,
        "shipping_jpy": shipping,
        "packaging_jpy": packaging,
        "insurance_jpy": insurance_cost,
        "duty_jpy": duty_cost,
        "reserve_jpy": reserve_cost,
        "assumptions": [
            "service fee is a configured estimate; verify at checkout",
            "guarantee plan and protective services are optional, not observed selections",
            "domestic/international shipping are estimates; weight, method and consolidation unknown",
            "duty and reserve are budgeting assumptions, not a tax or invoice quote",
            "listing price is used as observed; tax-inclusive/exclusive status is not inferred",
        ],
    }


def estimate_max_bid_jpy(
    current_price_jpy: int, price_floor_jpy: int, settings: dict[str, Any] | None = None
) -> int:
    config = settings or {}
    min_multiple = float(config.get("pricing", {}).get("min_profit_multiple", 1.5))
    if current_price_jpy <= 0:
        return 0
    max_bid = int(price_floor_jpy / min_multiple)
    return min(max_bid, max(0, current_price_jpy * 2))
