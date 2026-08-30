from __future__ import annotations

import re


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip()
    cleaned = cleaned.replace("\u3000", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def normalize_brand(value: str | None) -> str:
    if not value:
        return ""
    text = normalize_text(value)
    text = text.replace("(N)ine", "(N)ine")
    return text


def normalize_season(value: str | None) -> str | None:
    if not value:
        return None
    text = normalize_text(value).upper()
    if "AW" in text or "A/W" in text:
        return "AW"
    if "SS" in text or "S/S" in text:
        return "SS"
    return text


def japanese_aliases() -> list[str]:
    return ["ナンバーナイン", "本人期", "希少", "アーカイブ", "リビルド", "クロムハーツ"]


def negative_term_hits(text: str | None) -> list[str]:
    if not text:
        return []
    lowered = text.lower()
    negative_terms = [
        "fake", "replica", "inspired by", "type", "style", "no brand", "reprint",
        "diffusion", "generic", "logo tee", "レプリカ", "ノーブランド", "タイプ",
    ]
    hits: list[str] = []
    for term in negative_terms:
        if term in lowered:
            hits.append(term)
    return hits
