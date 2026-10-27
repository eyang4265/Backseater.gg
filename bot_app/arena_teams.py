"""Shared Arena team labels and chart colors."""

from __future__ import annotations


# Discord headings use color emoji while matplotlib needs matching hex colors.
# Arena currently has six teams; the final two entries keep historical or future
# eight-team payloads distinct without changing the presentation contract.
ARENA_TEAM_MARKERS = ("🔵", "🔴", "🟢", "🟡", "🟣", "⚪", "🟤", "🟠")
ARENA_TEAM_COLORS = (
    "#0072B2", "#D55E00", "#009E73", "#F0E442",
    "#CC79A7", "#FFFFFF", "#8C564B", "#E69F00",
)


def arena_ordinal(placement: int) -> str:
    """Return a compact ordinal such as ``1st`` or ``12th``."""
    remainder = placement % 100
    suffix = "th" if 11 <= remainder <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(
        placement % 10, "th"
    )
    return f"{placement}{suffix}"


def arena_team_label(number: int, placement: int | None = None) -> str:
    """Return a concise Arena team heading, optionally with its final place."""
    marker = ARENA_TEAM_MARKERS[(number - 1) % len(ARENA_TEAM_MARKERS)]
    if placement is None or placement <= 0:
        return f"{marker} Team {number}"
    return f"{marker} {arena_ordinal(placement)}"


def arena_team_placement(participants: list[dict]) -> int | None:
    """Return the final placement shared by an Arena team's participants."""
    for participant in participants:
        try:
            placement = int(participant.get("placement") or 0)
        except (TypeError, ValueError):
            continue
        if placement > 0:
            return placement
    return None


def arena_team_sort_key(item: tuple[int, list[dict]]) -> tuple[int, int]:
    """Sort completed Arena teams by placement, then stable subteam id."""
    team_id, participants = item
    placement = arena_team_placement(participants)
    return (placement if placement is not None else 10_000, team_id)
