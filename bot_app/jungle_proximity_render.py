"""Shared jungle-proximity embed rendering.

Used by both the match announcements' Jungle Proximity display option and the
``/jungleproximity`` command so the two never diverge.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord

from . import ddragon, emoji as emoji_lookup
from .charts import (
    build_jungle_proximity_comparison_chart,
    jungle_checkpoint_minutes,
    jungle_proximity_breakdown,
)
from .queues import queue_name
from .render import make_embed

LOGGER = logging.getLogger(__name__)

JUNGLE_TEAM_IDS = (100, 200)
JUNGLE_TEAM_LABELS = {100: "Blue Jungler", 200: "Red Jungler"}


def match_junglers(participants: list[dict[str, Any]]) -> dict[int, dict[str, Any] | None]:
    """Map each team id to its jungler participant, or None if unidentified."""
    return {
        team_id: next(
            (
                p
                for p in participants
                if p.get("teamId") == team_id and (p.get("teamPosition") or "").upper() == "JUNGLE"
            ),
            None,
        )
        for team_id in JUNGLE_TEAM_IDS
    }


def jungle_chart_checkpoints(
    match: dict[str, Any], timeline: dict[str, Any] | None
) -> list[tuple[int, dict[int, dict[str, Any] | None]]] | None:
    """Per-checkpoint lane-proximity breakdowns for both junglers, or None.

    None means there is nothing to chart: no timeline, or neither team's
    jungler could be identified.
    """
    if timeline is None:
        LOGGER.debug("Jungle proximity: no timeline available, skipping checkpoints")
        return None
    info = match.get("info", {})
    participants = info.get("participants", []) or []
    junglers = match_junglers(participants)
    if not any(junglers.values()):
        LOGGER.debug("Jungle proximity: no jungler identified for either team")
        return None

    game_duration = info.get("gameDuration", 0)
    chart_checkpoints: list[tuple[int, dict[int, dict[str, Any] | None]]] = []
    for minutes in jungle_checkpoint_minutes(game_duration):
        if minutes > 15:
            break
        breakdowns: dict[int, dict[str, Any] | None] = {}
        lanes: list[str] | None = None
        for team_id in JUNGLE_TEAM_IDS:
            jungler = junglers[team_id]
            if jungler is None:
                breakdowns[team_id] = None
                continue
            breakdown = jungle_proximity_breakdown(
                timeline, jungler["participantId"], participants, minutes
            )
            breakdowns[team_id] = breakdown
            lanes = lanes or list(breakdown)
        if lanes is None:
            continue
        chart_checkpoints.append((minutes, breakdowns))
    LOGGER.debug("Jungle proximity: computed %s checkpoint(s)", len(chart_checkpoints))
    return chart_checkpoints


async def build_jungle_proximity_chart(
    match: dict[str, Any], timeline: dict[str, Any] | None
) -> discord.File | None:
    """Render just the jungle-proximity checkpoint bar chart, no embed fields.

    Shared by the Chart dropdown's Jungle Proximity option, which swaps only
    the chart image behind whichever Display columns are already on screen.
    """
    chart_checkpoints = await asyncio.to_thread(jungle_chart_checkpoints, match, timeline)
    if not chart_checkpoints:
        return None
    return await asyncio.to_thread(build_jungle_proximity_comparison_chart, chart_checkpoints)


async def build_jungle_proximity_embed(
    match: dict[str, Any], timeline: dict[str, Any] | None
) -> tuple[discord.Embed, discord.File | None]:
    """Render both team junglers' lane-proximity scores plus a checkpoint bar chart.

    The chart (see :func:`build_jungle_proximity_comparison_chart`) shows
    both teams' junglers side by side, per lane, at each 5/10/15-minute
    checkpoint.
    """
    LOGGER.info("Building jungle proximity embed for match %s", match.get("metadata", {}).get("matchId"))
    info = match.get("info", {})
    participants = info.get("participants", []) or []
    embed = make_embed(
        f"Lane proximity for both junglers — {queue_name(info.get('queueId'))}",
        title="Jungle Proximity",
        color=discord.Color.dark_green(),
    )
    if timeline is None:
        embed.description = f"{embed.description}\n\nNo timeline is available for this match."
        return embed, None

    junglers = match_junglers(participants)
    if not any(junglers.values()):
        embed.description = f"{embed.description}\n\nNo jungler could be identified for either team."
        return embed, None

    catalog = await asyncio.to_thread(ddragon.catalog)
    for team_id in JUNGLE_TEAM_IDS:
        jungler = junglers[team_id]
        if jungler is None:
            embed.add_field(name=JUNGLE_TEAM_LABELS[team_id], value="Unknown", inline=True)
            continue
        champion = catalog.by_key(jungler.get("championId")) if catalog else None
        champion_name = champion.name if champion else jungler.get("championName", "Unknown champion")
        name = jungler.get("riotIdGameName") or jungler.get("summonerName", "Unknown")
        kda = f"{jungler.get('kills', 0)}/{jungler.get('deaths', 0)}/{jungler.get('assists', 0)}"
        icon = emoji_lookup.champion_emoji(champion, name=champion_name)
        embed.add_field(
            name=JUNGLE_TEAM_LABELS[team_id],
            value=f"{emoji_lookup.prefixed(icon, name)} ({kda})",
            inline=True,
        )
    embed.add_field(name="​", value="​", inline=True)

    lane_labels = {"Bottom": "Bot"}
    chart_checkpoints = await asyncio.to_thread(jungle_chart_checkpoints, match, timeline) or []
    for minutes, breakdowns in chart_checkpoints:
        lanes = list(next(b for b in breakdowns.values() if b is not None))
        embed.add_field(
            name=f"{minutes}m",
            value="\n".join(lane_labels.get(lane, lane) for lane in lanes),
            inline=True,
        )
        for team_id in JUNGLE_TEAM_IDS:
            breakdown = breakdowns[team_id]
            label = "Blue Score" if team_id == 100 else "Red Score"
            value = (
                "\n".join(f"{breakdown[lane]['score']:.1f}%" for lane in lanes)
                if breakdown is not None
                else "\n".join("—" for _ in lanes)
            )
            embed.add_field(name=label, value=value, inline=True)

    chart = (
        await asyncio.to_thread(build_jungle_proximity_comparison_chart, chart_checkpoints)
        if chart_checkpoints
        else None
    )
    if chart is not None:
        embed.set_image(url=f"attachment://{chart.filename}")
    return embed, chart
