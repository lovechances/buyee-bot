from __future__ import annotations

POSITIVE_BASE = {
    "miyashita",
    "本人期",
    "destroy",
    "mohair",
    "patchwork",
    "silver",
    "leather",
    "rebuild",
    "runway",
    "archive",
    "distressed",
    "vintage",
    "music",
    "artist",
    "rock",
    "metal",
    "punk",
    "grunge",
    "cult",
    "collaboration",
}

NEGATIVE_BASE = {
    "generic",
    "basic",
    "logo tee",
    "replica",
    "fake",
    "reprint",
    "diffusion",
    "style",
    "type",
    "no brand",
    "inspired by",
}


def extract_taste_signals(title: str, text: str | None = None) -> tuple[list[str], list[str]]:
    haystack = f"{title} {text or ''}".lower()
    positives = [term for term in sorted(POSITIVE_BASE) if term.lower() in haystack]
    negatives = [term for term in sorted(NEGATIVE_BASE) if term.lower() in haystack]
    return positives, negatives


def classify_brand(value: str | None) -> str:
    if not value:
        return "unknown"
    lowered = value.lower()
    for key in ["number (n)ine", "number nine", "ナンバーナイン"]:
        if key in lowered:
            return "number_nine"
    if "needles" in lowered:
        return "needles"
    if "chrome hearts" in lowered or "クロムハーツ" in lowered:
        return "chrome_hearts"
    if "dior" in lowered:
        return "dior_homme"
    if "rick owens" in lowered:
        return "rick_owens"
    return "generic"
