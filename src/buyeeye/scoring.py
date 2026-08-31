from __future__ import annotations

from typing import Any


def score_listing(payload: dict[str, Any]) -> int:
    title = (payload.get("title_original") or "").lower()
    tags = [str(tag).lower() for tag in payload.get("tags", [])]
    brand = (payload.get("brand") or "").lower()
    current_price = int(payload.get("current_price") or 0)

    score = 0
    if "number (n)ine" in brand or "ナンバーナイン" in brand or "number nine" in brand:
        score += 10
    if "needles" in brand:
        score += 8
    if "chrome hearts" in brand:
        score += 8
    if "dior" in brand:
        score += 6
    if any(
        word in title
        for word in [
            "destroy",
            "mohair",
            "patchwork",
            "archive",
            "rebuild",
            "rare",
            "runway",
            "collab",
        ]
    ):
        score += 30
    if any(
        tag in tags
        for tag in ["destroy", "mohair", "patchwork", "archive", "rebuild", "silver", "leather"]
    ):
        score += 25
    if any(word in title for word in ["generic", "basic", "reprint", "diffusion", "logo tee"]):
        score -= 25
    if current_price and current_price < 5000:
        score += 10
    if current_price and current_price > 150000:
        score -= 10
    return max(0, min(score, 100))
