"""Shared laning-phase comparison rendering, used by the ``/laning`` command.

Mirrors :mod:`bot_app.jungle_proximity_render`'s embed and chart shape:
metrics (Gold/XP) stand in for lanes, and You/Opponent stand in for the two
teams.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord

from . import ddragon, emoji as emoji_lookup
from .charts import build_laning_comparison_chart
from .queues import queue_name
from .render import make_embed
from .timeline import MatchTimeline, opponent_participant_id

LOGGER = logging.getLogger(__name__)

CHECKPOINT_MINUTES = (5, 10, 15)

_POSITION_LABELS = {"TOP": "Top", "MIDDLE": "Mid", "BOTTOM": "Bottom", "UTILITY": "Support"}
_METRIC_LABELS = ("Gold", "XP", "CS")


def _champion_label(participant: dict[str, Any], catalog) -> str:
    """Icon + champion name (display name) for a participant."""
    champion = catalog.by_key(participant.get("championId")) if catalog else None
    champion_name = champion.name if champion else participant.get("championName", "Unknown champion")
    display_name = participant.get("riotIdGameName") or participant.get("summonerName", "Unknown")
    icon = emoji_lookup.champion_emoji(champion, name=champion_name)
    return f"{emoji_lookup.prefixed(icon, champion_name)} ({display_name})"


def _format_stats(stats: dict[str, int] | None) -> str:
    """Gold/XP/CS as embed-field lines, or em dashes if that minute never happened."""
    if stats is None:
        return "—\n—\n—"
    return f"{stats['gold']:,}\n{stats['xp']:,}\n{stats['cs']:,}"


async def build_laning_embed(
    match: dict[str, Any], timeline: dict[str, Any] | None, puuid: str
) -> tuple[discord.Embed, discord.File | None]:
    """Render a player's Gold/XP/CS side by side with their lane opponent's, at 5/10/15 minutes."""
    LOGGER.info("Building laning-phase embed for puuid=%s", puuid)
    info = match.get("info", {})
    participants = info.get("participants", []) or []
    embed = make_embed(
        f"Laning-phase comparison — {queue_name(info.get('queueId'))}",
        title="Laning Phase",
        color=discord.Color.gold(),
    )

    participant = next((p for p in participants if p.get("puuid") == puuid), None)
    if participant is None:
        LOGGER.debug("Laning: puuid=%s not found in match participants", puuid)
        embed.description = f"{embed.description}\n\nCould not find that player in this match."
        return embed, None

    position = participant.get("teamPosition")
    if not position or position == "JUNGLE":
        LOGGER.debug("Laning: position=%s has no lane opponent, skipping", position)
        embed.description = (
            f"{embed.description}\n\nJungle has no lane opponent, so there is nothing to compare "
            "for this player in this match."
        )
        return embed, None

    if timeline is None:
        LOGGER.debug("Laning: no timeline available for this match")
        embed.description = f"{embed.description}\n\nNo timeline is available for this match."
        return embed, None

    opponent_id = opponent_participant_id(match, participant)
    opponent = next((p for p in participants if p.get("participantId") == opponent_id), None)
    if opponent is None:
        LOGGER.debug("Laning: no lane opponent identified for participant_id=%s", participant.get("participantId"))
        embed.description = f"{embed.description}\n\nNo lane opponent could be identified for this match."
        return embed, None

    catalog = await asyncio.to_thread(ddragon.catalog)
    embed.add_field(name="You", value=_champion_label(participant, catalog), inline=True)
    embed.add_field(name="Opponent", value=_champion_label(opponent, catalog), inline=True)
    embed.add_field(name="Lane", value=_POSITION_LABELS.get(position, position.title()), inline=True)

    match_timeline = MatchTimeline(timeline)
    participant_id = participant.get("participantId")
    chart_checkpoints: list[tuple[int, dict[str, dict[str, float] | None]]] = []
    for minutes in CHECKPOINT_MINUTES:
        timestamp = minutes * 60_000
        you_stats = match_timeline.stats_at(participant_id, timestamp)
        opponent_stats = match_timeline.stats_at(opponent_id, timestamp)
        chart_checkpoints.append((
            minutes,
            {
                "you": {"Gold": you_stats["gold"], "XP": you_stats["xp"]} if you_stats else None,
                "opponent": {"Gold": opponent_stats["gold"], "XP": opponent_stats["xp"]} if opponent_stats else None,
            },
        ))
        embed.add_field(name=f"{minutes}m", value="\n".join(_METRIC_LABELS), inline=True)
        embed.add_field(name="You", value=_format_stats(you_stats), inline=True)
        embed.add_field(name="Opponent", value=_format_stats(opponent_stats), inline=True)

    chart = await asyncio.to_thread(build_laning_comparison_chart, chart_checkpoints)
    if chart is not None:
        embed.set_image(url=f"attachment://{chart.filename}")
    return embed, chart
