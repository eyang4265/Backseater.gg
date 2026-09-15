"""Announcement value objects and their persistent JSON representation.

This module deliberately has no Discord, network, rendering, or storage
dependencies. Pollers and commands can exchange announcement data without
depending on the large presentation implementation in ``announce``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ranks import RankSnapshot


@dataclass(frozen=True)
class TrackedPlayer:
    """A player an announcement should call out by name."""

    puuid: str
    riot_id: str
    server: str | None = None
    lp_change: str | None = None
    rank: RankSnapshot | None = None


@dataclass(frozen=True)
class MatchAnnouncement:
    """Everything needed to publish or restore a completed match."""

    text: str
    outcome: str | None
    match: dict[str, Any]
    highlight_puuids: set[str]
    game_type: str = "lol"
    lp_changes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LiveGameAnnouncement:
    """A lobby posted once when one or more tracked players enter a game."""

    text: str
    game: dict[str, Any]
    highlight_puuids: set[str]
    game_type: str = "lol"


def match_announcement_payload(announcement: MatchAnnouncement) -> dict[str, Any]:
    """Serialize the data needed to rebuild a completed-match view."""
    return {
        "text": announcement.text,
        "outcome": announcement.outcome,
        "match": announcement.match,
        "highlight_puuids": sorted(announcement.highlight_puuids),
        "game_type": announcement.game_type,
        "lp_changes": dict(announcement.lp_changes),
    }


def live_game_announcement_payload(
    announcement: LiveGameAnnouncement,
) -> dict[str, Any]:
    """Serialize the data needed to rebuild a live-game view."""
    return {
        "text": announcement.text,
        "game": announcement.game,
        "highlight_puuids": sorted(announcement.highlight_puuids),
        "game_type": announcement.game_type,
    }


def match_announcement_from_payload(payload: dict[str, Any]) -> MatchAnnouncement:
    """Restore a completed-match announcement from JSON state."""
    return MatchAnnouncement(
        text=str(payload.get("text", "")),
        outcome=payload.get("outcome"),
        match=payload.get("match", {}),
        highlight_puuids=set(payload.get("highlight_puuids", [])),
        game_type=str(payload.get("game_type", "lol")),
        lp_changes=dict(payload.get("lp_changes", {}) or {}),
    )


def live_game_announcement_from_payload(
    payload: dict[str, Any],
) -> LiveGameAnnouncement:
    """Restore a live-game announcement from JSON state."""
    return LiveGameAnnouncement(
        text=str(payload.get("text", "")),
        game=payload.get("game", {}),
        highlight_puuids=set(payload.get("highlight_puuids", [])),
        game_type=str(payload.get("game_type", "lol")),
    )
