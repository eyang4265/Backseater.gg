"""Formatting and publishing match announcements.

Both pollers and ``/selftest`` share this. The two pollers previously carried
their own copy of the publish step — resolve channel, build embed, build
columns, build chart, send — which is where they drifted apart.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Sequence

import discord

from . import ddragon, emoji as emoji_lookup
from .charts import DAMAGE_CHART_FILENAME, build_damage_chart
from .config import get_settings
from .queues import RANKED_QUEUE_IDS, lobby_queue_name, queue_name
from .ranks import RankSnapshot
from .render import (
    NameStyle,
    add_team_columns,
    build_match_columns,
    build_lobby_columns,
    format_duration,
    kda_text,
    make_embed,
    outcome_color,
    rank_text,
)
from .routing import opgg_url

LOGGER = logging.getLogger(__name__)

REMAKE = "Remake"
VICTORY = "Victory"
DEFEAT = "Defeat"

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
    text: str
    outcome: str | None
    match: dict[str, Any]
    highlight_puuids: set[str]
    name_style: NameStyle = NameStyle.SUMMONER


@dataclass(frozen=True)
class LiveGameAnnouncement:
    """A lobby posted once when one or more tracked players enter a game."""

    text: str
    game: dict[str, Any]
    highlight_puuids: set[str]


def _result_for(participant: dict[str, Any]) -> str:
    if participant.get("gameEndedInEarlySurrender"):
        return REMAKE
    return VICTORY if participant.get("win") else DEFEAT


def format_match(
    match: dict[str, Any],
    players: Sequence[TrackedPlayer],
    *,
    require_finished: bool = True,
    require_ranked_queue: bool = True,
    name_style: NameStyle = NameStyle.SUMMONER,
) -> MatchAnnouncement | None:
    """One announcement covering every tracked player in a match.

    ``require_finished`` and ``require_ranked_queue`` gate the two checks that
    only make sense for the live poller (skip games still in progress; only
    announce ranked). ``/selftest`` and the guest tracker turn them off so
    they can render any match through this same code path.

    The full lobby isn't part of this text — callers add it as embed fields
    via :func:`build_match_columns`, so the rows line up in real columns.

    ``outcome`` is set when every tracked player had the same result, and None
    when they were on opposing teams; callers colour the embed by it.
    """
    info = match.get("info", {})

    if require_finished and not info.get("gameEndTimestamp"):
        return None

    queue_id = info.get("queueId")
    if require_ranked_queue and queue_id not in RANKED_QUEUE_IDS:
        return None

    participants = info.get("participants", []) or []
    duration = format_duration(info.get("gameDuration", 0))
    catalog = ddragon.catalog()

    lines: list[tuple[str, str]] = []
    results: set[str] = set()
    for player in players:
        participant = next((p for p in participants if p.get("puuid") == player.puuid), None)
        if participant is None:
            continue

        result = _result_for(participant)
        results.add(result)

        champion_id = participant.get("championName", "Unknown champion")
        champion = catalog.by_key(participant.get("championId")) if catalog else None
        icon = emoji_lookup.champion_emoji(champion, name=champion_id)

        link = opgg_url(player.server, player.riot_id)
        name = f"[{player.riot_id}]({link})" if link else player.riot_id
        line = f"{emoji_lookup.prefixed(icon, name)} — ({kda_text(participant)})"

        if result != REMAKE:
            standing = rank_text(player.rank)
            if standing:
                line += f" | {standing}"
            if player.lp_change:
                line += f" | {player.lp_change}"

        lines.append((result, line))

    if not lines:
        return None

    # One shared outcome is the common case, so it goes in the header. Tracked
    # players on opposing teams get it per line instead.
    if len(results) == 1:
        outcome: str | None = next(iter(results))
        header = f"**{queue_name(queue_id)} - {outcome}** ({duration})"
        body = "\n".join(line for _, line in lines)
    else:
        outcome = None
        header = f"**{queue_name(queue_id)}** ({duration})"
        body = "\n".join(f"**{result}** — {line}" for result, line in lines)

    return MatchAnnouncement(
        text=f"{header}\n{body}",
        outcome=outcome,
        match=match,
        highlight_puuids={player.puuid for player in players},
        name_style=name_style,
    )


def format_live_game(game: dict[str, Any], players: Sequence[TrackedPlayer]) -> LiveGameAnnouncement | None:
    """Create the tracked-player summary for a newly detected live lobby."""
    participants = game.get("participants", []) or []
    catalog = ddragon.catalog()
    lines = []
    highlighted = set()

    for player in players:
        participant = next((entry for entry in participants if entry.get("puuid") == player.puuid), None)
        if participant is None:
            continue
        champion = catalog.by_key(participant.get("championId")) if catalog else None
        champion_name = champion.name if champion else f"Champion {participant.get('championId', '?')}"
        icon = emoji_lookup.champion_emoji(champion, name=champion_name)
        link = opgg_url(player.server, player.riot_id)
        name = f"[{player.riot_id}]({link})" if link else player.riot_id
        lines.append(f"{emoji_lookup.prefixed(icon, name)} — {champion_name}")
        highlighted.add(player.puuid)

    if not lines:
        return None

    return LiveGameAnnouncement(
        text=(
            f"**{lobby_queue_name(game.get('gameQueueConfigId'))} — Live Game** "
            f"({format_duration(game.get('gameLength', 0))})\n"
            + "\n".join(lines)
        ),
        game=game,
        highlight_puuids=highlighted,
    )


async def resolve_announcement_channel(bot: Any) -> Any | None:
    """The configured announcement channel, fetched if it isn't cached."""
    channel_id = get_settings().announcement_channel_id
    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await bot.fetch_channel(channel_id)
    except discord.DiscordException:
        LOGGER.exception("Could not access announcement channel %s", channel_id)
        return None


async def build_announcement_embed(
    announcement: MatchAnnouncement,
) -> tuple[discord.Embed, discord.File | None]:
    """Render an announcement into an embed plus its optional chart attachment.

    Both the column lookups and the chart render are blocking, so each runs on
    a worker thread rather than on the event loop.
    """
    embed = make_embed(announcement.text, color=outcome_color(announcement.outcome))
    info = announcement.match.get("info", {})

    columns = await asyncio.to_thread(
        build_match_columns,
        info.get("participants", []) or [],
        server=info.get("platformId"),
        queue_id=info.get("queueId"),
        highlight_puuids=announcement.highlight_puuids,
        name_style=announcement.name_style,
    )
    add_team_columns(embed, columns)

    chart = await asyncio.to_thread(
        build_damage_chart, announcement.match, announcement.highlight_puuids
    )
    if chart is not None:
        embed.set_image(url=f"attachment://{DAMAGE_CHART_FILENAME}")
    return embed, chart


async def build_live_game_embed(announcement: LiveGameAnnouncement) -> discord.Embed:
    """Render a live-game announcement with the same ranked lobby columns."""
    game = announcement.game
    embed = make_embed(announcement.text, color=discord.Color.gold())
    columns = await asyncio.to_thread(
        build_lobby_columns, game, game.get("platformId") or "NA1"
    )
    add_team_columns(embed, columns)
    return embed


def gold_embed(match: dict[str, Any]) -> discord.Embed:
    """Private, side-by-side gold-earned breakdown for a finished match."""
    info = match.get("info", {})
    participants = info.get("participants", []) or []

    def team_gold(team_id: int) -> list[int]:
        members = sorted(
            (participant for participant in participants if participant.get("teamId") == team_id),
            key=lambda participant: participant.get("participantId", 0),
        )
        return [participant.get("goldEarned", 0) for participant in members]

    blue_gold = team_gold(100)
    red_gold = team_gold(200)
    if not blue_gold and not red_gold:
        return make_embed(
            "Gold data was unavailable for this match.", title="Gold Earned", color=discord.Color.gold()
        )

    row_count = max(len(blue_gold), len(red_gold))
    blue_lines = []
    red_lines = []
    diff_lines = []
    for index in range(row_count):
        blue = blue_gold[index] if index < len(blue_gold) else None
        red = red_gold[index] if index < len(red_gold) else None
        blue_lines.append(f"{blue:,} gold" if blue is not None else "—")
        red_lines.append(f"{red:,} gold" if red is not None else "—")
        diff_lines.append(f"{blue - red:+,} gold" if blue is not None and red is not None else "—")

    embed = discord.Embed(title="Gold Earned", color=discord.Color.gold())
    embed.add_field(name="Blue Team", value="\n".join(blue_lines), inline=True)
    embed.add_field(name="Red Team", value="\n".join(red_lines), inline=True)
    embed.add_field(name="Diff", value="\n".join(diff_lines), inline=True)
    return embed


class _GoldButton(discord.ui.Button):
    def __init__(self, match: dict[str, Any]) -> None:
        super().__init__(label="Show Gold", style=discord.ButtonStyle.secondary, emoji="🪙")
        self._match = match

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=gold_embed(self._match), ephemeral=True)


class GoldView(discord.ui.View):
    """The private gold detail control attached to a completed-match post."""

    def __init__(self, match: dict[str, Any]) -> None:
        super().__init__(timeout=900)
        self.add_item(_GoldButton(match))


async def publish(bot: Any, announcements: Sequence[MatchAnnouncement]) -> None:
    """Post each announcement to the configured channel.

    One failure is logged and skipped rather than dropping the rest of the batch.
    """
    if not announcements:
        return

    channel = await resolve_announcement_channel(bot)
    if channel is None:
        return

    for announcement in announcements:
        try:
            embed, chart = await build_announcement_embed(announcement)
            await channel.send(
                embed=embed,
                file=chart,
                view=GoldView(announcement.match),
            )
        except Exception:
            LOGGER.exception("Could not post a match announcement to %s", channel.id)


async def publish_live_games(bot: Any, announcements: Sequence[LiveGameAnnouncement]) -> None:
    """Post newly detected live lobbies to the normal announcement channel."""
    if not announcements:
        return
    channel = await resolve_announcement_channel(bot)
    if channel is None:
        return
    for announcement in announcements:
        try:
            await channel.send(embed=await build_live_game_embed(announcement))
        except Exception:
            LOGGER.exception("Could not post a live-game announcement to %s", channel.id)
