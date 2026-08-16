"""The League rank scale, and snapshots of where an account sits on it."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .queues import FLEX_QUEUE_ID, QUEUE_TYPES, SOLO_QUEUE_ID
from .riot import get_client

LOGGER = logging.getLogger(__name__)

SUB_MASTER_TIERS: tuple[str, ...] = (
    "IRON",
    "BRONZE",
    "SILVER",
    "GOLD",
    "PLATINUM",
    "EMERALD",
    "DIAMOND",
)
DIVISIONS: dict[str, int] = {"IV": 0, "III": 1, "II": 2, "I": 3}
_DIVISION_LABELS: dict[int, str] = {index: label for label, index in DIVISIONS.items()}
APEX_TIERS: frozenset[str] = frozenset({"MASTER", "GRANDMASTER", "CHALLENGER"})
TIER_LABELS: dict[str, str] = {
    tier: tier.title() for tier in (*SUB_MASTER_TIERS, *APEX_TIERS)
}
TIER_LABELS["PLATINUM"] = "Plat"


_TIER_SPAN = 400
_DIVISION_SPAN = 100
APEX_THRESHOLD = len(SUB_MASTER_TIERS) * _TIER_SPAN


@dataclass(frozen=True)
class RankSnapshot:
    """An account's standing in one ranked queue at a point in time.

    A snapshot with ``tier is None`` means *unranked*, which is different from
    a failed fetch — callers that need to tell those apart get ``None``
    instead of a snapshot when the request failed.
    """

    tier: str | None = None
    division: str = ""
    lp: int = 0
    wins: int = 0
    losses: int = 0
    updated_at: int | None = None

    @property
    def is_ranked(self) -> bool:
        """Handle ranked."""
        return bool(self.tier)

    @property
    def games(self) -> int:
        """Handle games."""
        return self.wins + self.losses

    @property
    def winrate(self) -> float | None:
        """Handle winrate."""
        return self.wins / self.games if self.games else None

    @property
    def value(self) -> int | None:
        """Position on one continuous scale, so gains survive promotions."""
        return rank_value(self.tier, self.division, self.lp)

    @classmethod
    def from_entry(cls, entry: dict[str, Any]) -> "RankSnapshot":
        """Handle entry."""
        return cls(
            tier=entry.get("tier"),
            division=entry.get("rank", "") or "",
            lp=entry.get("leaguePoints", 0),
            wins=entry.get("wins", 0),
            losses=entry.get("losses", 0),
            updated_at=int(time.time()),
        )

    @classmethod
    def from_state(cls, raw: dict[str, Any] | None) -> "RankSnapshot | None":
        """Handle state."""
        if not isinstance(raw, dict):
            return None
        return cls(
            tier=raw.get("tier"),
            division=raw.get("rank", "") or "",
            lp=raw.get("lp", 0),
            wins=raw.get("wins", 0),
            losses=raw.get("losses", 0),
            updated_at=raw.get("updated_at"),
        )

    def to_state(self) -> dict[str, Any]:
        """Handle state."""
        state = {
            "tier": self.tier,
            "rank": self.division,
            "lp": self.lp,
            "wins": self.wins,
            "losses": self.losses,
        }
        if self.updated_at is not None:
            state["updated_at"] = self.updated_at
        return state


def rank_value(tier: str | None, division: str | None, lp: int) -> int | None:
    """Flatten tier/division/LP into one comparable number."""
    if not tier:
        return None
    if tier in APEX_TIERS:
        return APEX_THRESHOLD + lp
    if tier not in SUB_MASTER_TIERS:
        return None
    tier_index = SUB_MASTER_TIERS.index(tier)
    division_index = DIVISIONS.get(division or "", 0)
    return tier_index * _TIER_SPAN + division_index * _DIVISION_SPAN + lp


def value_to_rank(value: float | None) -> tuple[str, str, int] | None:
    """Inverse of :func:`rank_value`: ``(tier, division_label, lp)``.

    Values at or above the apex threshold come back as ``("MASTER", "", lp)``
    — the same flattening :func:`rank_value` applies on the way in.
    """
    if value is None:
        return None
    if value >= APEX_THRESHOLD:
        return "MASTER", "", round(value - APEX_THRESHOLD)

    tier_index = min(int(value // _TIER_SPAN), len(SUB_MASTER_TIERS) - 1)
    remainder = value - tier_index * _TIER_SPAN
    division_index = min(int(remainder // _DIVISION_SPAN), len(DIVISIONS) - 1)
    return (
        SUB_MASTER_TIERS[tier_index],
        _DIVISION_LABELS.get(division_index, "IV"),
        round(remainder - division_index * _DIVISION_SPAN),
    )


def lp_change(before: RankSnapshot | None, after: RankSnapshot | None) -> str | None:
    """``"+18 LP"`` / ``"-14 LP"``, or None when either side is unusable."""
    if before is None or after is None:
        return None
    previous, current = before.value, after.value
    if previous is None or current is None:
        return None
    diff = current - previous
    return f"{'+' if diff >= 0 else ''}{diff} LP"


def average_value(values: list[int]) -> float | None:
    """Handle value."""
    return sum(values) / len(values) if values else None


def fetch_ranks(puuid: str, server: str) -> dict[int, RankSnapshot] | None:
    """Every ranked queue's snapshot for an account, in one request.

    Returns None if the request failed. Queues the account is unranked in are
    present with an unranked snapshot, so a caller can distinguish "no data"
    from "no rank".
    """
    entries = get_client().league_entries(puuid, server)
    if entries is None:
        return None

    by_type = {entry.get("queueType"): entry for entry in entries}
    return {
        queue_id: (
            RankSnapshot.from_entry(by_type[queue_type])
            if queue_type in by_type
            else RankSnapshot()
        )
        for queue_id, queue_type in QUEUE_TYPES.items()
    }


def fetch_rank(
    puuid: str, server: str, queue_id: int = SOLO_QUEUE_ID
) -> RankSnapshot | None:
    """One queue's snapshot, or None if the fetch failed."""
    ranks = fetch_ranks(puuid, server)
    if ranks is None:
        return None
    return ranks.get(queue_id, RankSnapshot())


def rank_queue_for_match(queue_id: int | None) -> int:
    """Which ranked queue's standing to show for a match.

    Flex matches show Flex rank; everything else — including unranked queues,
    where there is no queue-specific rank to show — falls back to Solo/Duo.
    """
    return FLEX_QUEUE_ID if queue_id == FLEX_QUEUE_ID else SOLO_QUEUE_ID


def rank_queue_label(rank_queue_id: int) -> str:
    """Names the queue a rank column is showing, so Flex ranks aren't read as Solo/Duo.

    Takes an already-resolved rank queue id — the output of
    :func:`rank_queue_for_match` — not the lobby's own queue id.
    """
    return "Flex Rank:" if rank_queue_id == FLEX_QUEUE_ID else "Solo/Duo Rank:"
