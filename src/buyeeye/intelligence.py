from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Proposal:
    id: int
    term: str
    brand: str
    reason: str
    support_count: int
    negative_count: int
    confidence: float


PROPOSALS = [
    Proposal(
        id=1,
        term="本人期",
        brand="Number (N)ine",
        reason="Repeatedly appears in desirable original-era Japanese listings.",
        support_count=17,
        negative_count=1,
        confidence=0.91,
    ),
    Proposal(
        id=2,
        term="destroy",
        brand="Number (N)ine",
        reason="Distressed construction is a strong positive signal for archive pieces.",
        support_count=12,
        negative_count=2,
        confidence=0.83,
    ),
    Proposal(
        id=3,
        term="リビルド",
        brand="Needles",
        reason="Rebuild language often indicates reconstructive pieces with stronger resale demand.",
        support_count=11,
        negative_count=1,
        confidence=0.82,
    ),
]


def list_proposals() -> list[Proposal]:
    return list(PROPOSALS)


def promote_proposal(record_id: int) -> Proposal | None:
    for item in PROPOSALS:
        if item.id == record_id:
            return item
    return None
