"""Aggregate a player's recent standard Summoner's Rift champion pool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .queues import FLEX_QUEUE_ID, SOLO_QUEUE_ID

POOL_QUEUES = frozenset({400, SOLO_QUEUE_ID, 430, FLEX_QUEUE_ID, 480, 490})
ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
ROLE_NAMES = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "ADC", "UTILITY": "Support"}


@dataclass(frozen=True)
class PoolRow:
    """One champion's record in one role within the selected history window."""

    role: str
    champion: str
    games: int
    wins: int

    @property
    def win_rate(self) -> float:
        """Return the win fraction for the full sample."""
        return self.wins / self.games


def aggregate_pool(matches: Iterable[dict], puuid: str, queue: str = "All SR") -> tuple[PoolRow, ...]:
    """Count non-remake games by champion and role.

    Missing roles and nonstandard queues are excluded so lanes are comparable.
    """
    allowed = {
        "All SR": POOL_QUEUES,
        "Ranked Solo/Duo": frozenset({SOLO_QUEUE_ID}),
        "Ranked Flex": frozenset({FLEX_QUEUE_ID}),
    }[queue]
    records: dict[tuple[str, str], list[int]] = {}
    for match in matches:
        info = match.get("info") or {}
        if info.get("queueId") not in allowed:
            continue
        try:
            if float(info.get("gameDuration") or 0) <= 900:
                continue
        except (TypeError, ValueError):
            continue
        participant = next(
            (p for p in info.get("participants", ()) if p.get("puuid") == puuid), None
        )
        if not participant or participant.get("gameEndedInEarlySurrender"):
            continue
        role = str(participant.get("teamPosition") or participant.get("individualPosition") or "").upper()
        champion = str(participant.get("championName") or "")
        if role not in ROLES or not champion:
            continue
        totals = records.setdefault((role, champion), [0, 0])
        totals[0] += 1
        totals[1] += int(bool(participant.get("win")))
    return tuple(
        PoolRow(role, champion, totals[0], totals[1])
        for (role, champion), totals in records.items()
    )
