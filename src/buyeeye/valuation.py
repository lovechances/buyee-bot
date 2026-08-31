from __future__ import annotations

from typing import Any


def estimate_landed_cost_jpy(
    item_price_jpy: int, settings: dict[str, Any] | None = None
) -> dict[str, float]:
    config = settings or {}
    pricing = config.get("pricing", {})
    fee_percent = float(pricing.get("buyee_fee_percent", 0.11))
    plan_fee = float(pricing.get("plan_fee_jpy", 350))
    shipping = float(pricing.get("international_shipping_jpy", 1500))
    packaging = float(pricing.get("packaging_jpy", 300))
    insurance = float(pricing.get("insurance_percent", 0.02))
    duty = float(pricing.get("duty_percent", 0.08))
    reserve = float(pricing.get("reserve_percent", 0.05))

    item_cost = float(item_price_jpy)
    fee = item_cost * fee_percent
    insurance_cost = item_cost * insurance
    duty_cost = item_cost * duty
    reserve_cost = item_cost * reserve
    landed = (
        item_cost
        + fee
        + plan_fee
        + shipping
        + packaging
        + insurance_cost
        + duty_cost
        + reserve_cost
    )
    return {
        "landed_jpy": landed,
        "fee_jpy": fee,
        "shipping_jpy": shipping,
        "duty_jpy": duty_cost,
        "reserve_jpy": reserve_cost,
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
