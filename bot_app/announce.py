"""Formatting and publishing match announcements.

Both pollers and ``/selftest`` share this. The two pollers previously carried
their own copy of the publish step — resolve channel, build embed, build
columns, build chart, send — which is where they drifted apart.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Any, Sequence

import discord

from . import ddragon, emoji as emoji_lookup
from .charts import DAMAGE_CHART_FILENAME, build_damage_chart, build_team_gold_difference_chart
from .config import get_settings
from .queues import FLEX_QUEUE_ID, RANKED_QUEUE_IDS, SOLO_QUEUE_ID, lobby_queue_name, queue_name
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
from .riot import get_client
from .store import load_accounts, load_guild_channels

LOGGER = logging.getLogger(__name__)
_UNSET = object()

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
    """Handle for."""
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
        participant = next(
            (p for p in participants if p.get("puuid") == player.puuid), None
        )
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


def format_live_game(
    game: dict[str, Any], players: Sequence[TrackedPlayer]
) -> LiveGameAnnouncement | None:
    """Create the tracked-player summary for a newly detected live lobby."""
    participants = game.get("participants", []) or []
    catalog = ddragon.catalog()
    lines = []
    highlighted = set()

    for player in players:
        participant = next(
            (entry for entry in participants if entry.get("puuid") == player.puuid),
            None,
        )
        if participant is None:
            continue
        champion = catalog.by_key(participant.get("championId")) if catalog else None
        champion_name = (
            champion.name
            if champion
            else f"Champion {participant.get('championId', '?')}"
        )
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
            f"({format_duration(game.get('gameLength', 0))})\n" + "\n".join(lines)
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


async def resolve_announcement_channels(
    bot: Any,
    highlight_puuids: set[str] | None = None,
    *,
    configured: dict[str, int] | None = None,
    accounts: dict[str, Any] | None = None,
    member_cache: dict[tuple[int, int], bool] | None = None,
    channel_cache: dict[int, Any | None] | None = None,
    global_channel: Any = _UNSET,
) -> list[Any]:
    """Resolve membership routes while always preserving the legacy channel."""
    configured_routes = (
        configured
        if configured is not None
        else await asyncio.to_thread(load_guild_channels)
    )
    resolved_accounts = (
        accounts if accounts is not None else await asyncio.to_thread(load_accounts)
    )
    if member_cache is None:
        member_cache = {}
    if channel_cache is None:
        channel_cache = {}
    if global_channel is _UNSET:
        global_channel = await resolve_announcement_channel(bot)

    discord_ids = {
        int(account.discord_id)
        for account in resolved_accounts.values()
        if not highlight_puuids or account.puuid in highlight_puuids
    }
    channels = [global_channel] if global_channel is not None else []
    channel_ids = (
        {getattr(global_channel, "id", None)} if global_channel is not None else set()
    )
    for guild_id, channel_id in configured_routes.items():
        guild = bot.get_guild(int(guild_id))
        if guild is None:
            continue
        has_member = False
        for discord_id in discord_ids:
            cache_key = (int(guild_id), discord_id)
            if cache_key in member_cache:
                has_member = member_cache[cache_key]
                if has_member:
                    break
                continue
            if guild.get_member(discord_id) is not None:
                member_cache[cache_key] = True
                has_member = True
                break
            try:
                await guild.fetch_member(discord_id)
                member_cache[cache_key] = True
                has_member = True
                break
            except discord.NotFound:
                member_cache[cache_key] = False
                continue
            except discord.DiscordException as error:
                LOGGER.warning(
                    "Could not verify member %s in guild %s; routing anyway: %s",
                    discord_id,
                    guild_id,
                    error,
                )
                member_cache[cache_key] = True
                has_member = True
                break
        if not has_member:
            continue
        if channel_id in channel_ids:
            continue
        if channel_id in channel_cache:
            channel = channel_cache[channel_id]
        else:
            channel = bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(channel_id)
                except discord.DiscordException:
                    LOGGER.exception(
                        "Could not access announcement channel %s", channel_id
                    )
                    channel = None
            channel_cache[channel_id] = channel
        if channel is None:
            continue
        channels.append(channel)
        channel_ids.add(channel_id)
    return channels


async def build_announcement_embed(
    announcement: MatchAnnouncement,
    *,
    rank_queue_id: int | None = None,
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
        queue_id=rank_queue_id or info.get("queueId"),
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


async def build_live_game_embed(
    announcement: LiveGameAnnouncement, *, rank_queue_id: int | None = None
) -> discord.Embed:
    """Render a live-game announcement with the same ranked lobby columns."""
    game = announcement.game
    embed = make_embed(announcement.text, color=discord.Color.gold())
    columns = await asyncio.to_thread(
        build_lobby_columns,
        game,
        game.get("platformId") or "NA1",
        queue_id=rank_queue_id,
    )
    add_team_columns(embed, columns)
    return embed


def gold_embed(match: dict[str, Any]) -> discord.Embed:
    """Private, side-by-side gold-earned breakdown for a finished match."""
    info = match.get("info", {})
    participants = info.get("participants", []) or []

    def team_gold(team_id: int) -> list[int]:
        """Handle gold."""
        members = sorted(
            (
                participant
                for participant in participants
                if participant.get("teamId") == team_id
            ),
            key=lambda participant: participant.get("participantId", 0),
        )
        return [participant.get("goldEarned", 0) for participant in members]

    blue_gold = team_gold(100)
    red_gold = team_gold(200)
    if not blue_gold and not red_gold:
        return make_embed(
            "Gold data was unavailable for this match.",
            title="Gold Earned",
            color=discord.Color.gold(),
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
        diff_lines.append(
            f"{blue - red:+,} gold" if blue is not None and red is not None else "—"
        )

    embed = discord.Embed(title="Gold Earned", color=discord.Color.gold())
    embed.add_field(name="Blue Team", value="\n".join(blue_lines), inline=True)
    embed.add_field(name="Red Team", value="\n".join(red_lines), inline=True)
    embed.add_field(name="Diff", value="\n".join(diff_lines), inline=True)
    return embed


def team_stat_embed(
    match: dict[str, Any], *, field: str, label: str, title: str
) -> discord.Embed:
    """Show one per-player team statistic and the blue-vs-red difference."""
    participants = match.get("info", {}).get("participants", []) or []
    teams = {
        team_id: sorted(
            (p for p in participants if p.get("teamId") == team_id),
            key=lambda p: p.get("participantId", 0),
        )
        for team_id in (100, 200)
    }
    if not any(teams.values()):
        return make_embed(f"{label} data was unavailable for this match.", title=title)

    blue = [int(p.get(field, 0) or 0) for p in teams[100]]
    red = [int(p.get(field, 0) or 0) for p in teams[200]]
    rows = max(len(blue), len(red))
    differences = [
        blue[i] - red[i] if i < len(blue) and i < len(red) else None
        for i in range(rows)
    ]
    embed = discord.Embed(title=title, color=discord.Color.gold())
    embed.add_field(name="Blue Team", value="\n".join(f"{value:,}" for value in blue) or "—")
    embed.add_field(name="Red Team", value="\n".join(f"{value:,}" for value in red) or "—")
    embed.add_field(
        name="Blue − Red",
        value="\n".join(f"{value:+,}" if value is not None else "—" for value in differences)
        or "—",
    )
    return embed


class _GoldButton(discord.ui.Button):
    def __init__(self, match: dict[str, Any]) -> None:
        """Initialize the instance."""
        super().__init__(
            label="Show Gold", style=discord.ButtonStyle.secondary, emoji="🪙"
        )
        self._match = match

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle the component interaction."""
        await interaction.response.edit_message(
            embed=gold_embed(self._match),
            attachments=[],
            view=GoldView(self._match),
        )


class _StatButton(discord.ui.Button):
    """Show a per-player team statistic."""

    def __init__(self, match: dict[str, Any], *, field: str, label: str, emoji: str) -> None:
        """Initialize the statistic button."""
        super().__init__(label=label, style=discord.ButtonStyle.secondary, emoji=emoji)
        self._match = match
        self._field = field
        self._title = label

    async def callback(self, interaction: discord.Interaction) -> None:
        """Render the selected statistic."""
        await interaction.response.edit_message(
            embed=team_stat_embed(
                self._match,
                field=self._field,
                label=self._title,
                title=self._title,
            ),
            attachments=[],
            view=GoldView(self._match),
        )


class _ChartButton(discord.ui.Button):
    """Show a chart for one match statistic."""

    def __init__(
        self,
        match: dict[str, Any],
        *,
        field: str,
        label: str,
        emoji: str,
        highlight_puuids: set[str] | None = None,
        active_field: str | None = None,
        announcement: MatchAnnouncement | None = None,
    ) -> None:
        """Initialize the chart button."""
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            emoji=emoji,
            disabled=field == active_field,
        )
        self._match = match
        self._field = field
        self._label = label
        self._highlight_puuids = highlight_puuids or set()
        self._announcement = announcement

    async def callback(self, interaction: discord.Interaction) -> None:
        """Render the selected statistic chart."""
        filename = f"{self._field}.png"
        if self._field == "teamGoldDifference":
            info = self._match.get("info", {})
            match_id = self._match.get("metadata", {}).get("matchId")
            timeline = await asyncio.to_thread(
                get_client().match_timeline, match_id, info.get("platformId") or "NA1"
            )
            chart = await asyncio.to_thread(
                build_team_gold_difference_chart, timeline, filename=filename
            )
        else:
            chart = await asyncio.to_thread(
                build_damage_chart,
                self._match,
                self._highlight_puuids,
                metric_field=self._field,
                chart_title=self._label,
                filename=filename,
            )
        embed = interaction.message.embeds[0].copy() if interaction.message.embeds else make_embed(
            self._label, color=discord.Color.gold()
        )
        if chart is not None:
            embed.set_image(url=f"attachment://{filename}")
        await interaction.response.edit_message(
            embed=embed,
            file=chart,
            view=GoldView(
                self._match,
                highlight_puuids=self._highlight_puuids,
                active_field=self._field,
                announcement=self._announcement,
            ),
        )


class GoldView(discord.ui.View):
    """The gold detail control attached to a completed-match post."""

    def __init__(
        self,
        match: dict[str, Any],
        *,
        highlight_puuids: set[str] | None = None,
        active_field: str | None = "totalDamageDealtToChampions",
        announcement: MatchAnnouncement | None = None,
    ) -> None:
        """Initialize the instance."""
        super().__init__(timeout=900)
        highlight_puuids = highlight_puuids or set()
        self._highlight_puuids = highlight_puuids
        self.add_item(
            _ChartButton(
                match,
                field="totalDamageDealtToChampions",
                label="Dmg Done",
                emoji="⚔️",
                highlight_puuids=highlight_puuids,
                active_field=active_field,
                announcement=announcement,
            )
        )
        self.add_item(
            _ChartButton(
                match,
                field="goldEarned",
                label="Gold",
                emoji="🪙",
                highlight_puuids=highlight_puuids,
                active_field=active_field,
                announcement=announcement,
            )
        )
        self.add_item(
            _ChartButton(
                match,
                field="teamGoldDifference",
                label="Gold Diff",
                emoji="📊",
                highlight_puuids=highlight_puuids,
                active_field=active_field,
                announcement=announcement,
            )
        )
        self.add_item(
            _ChartButton(
                match,
                field="totalDamageTaken",
                label="Dmg Taken",
                emoji="🛡️",
                highlight_puuids=highlight_puuids,
                active_field=active_field,
                announcement=announcement,
            )
        )
        self.add_item(
            _ChartButton(
                match,
                field="healingAndShielding",
                label="Healing & Shielding",
                emoji="💚",
                highlight_puuids=highlight_puuids,
                active_field=active_field,
                announcement=announcement,
            )
        )
        if announcement is not None and announcement.match.get("info", {}).get("queueId") == FLEX_QUEUE_ID:
            self.add_item(_SoloRankButton(announcement))


class _SoloRankButton(discord.ui.Button):
    """Switch a Flex announcement's team columns to Solo/Duo ranks."""

    def __init__(self, announcement: MatchAnnouncement) -> None:
        """Initialize the button."""
        super().__init__(
            label="Show Ranked Solo",
            style=discord.ButtonStyle.secondary,
            emoji="🏆",
        )
        self._announcement = announcement

    async def callback(self, interaction: discord.Interaction) -> None:
        """Render the same match with Ranked Solo/Duo standings."""
        embed, chart = await build_announcement_embed(
            self._announcement, rank_queue_id=SOLO_QUEUE_ID
        )
        attachment = chart
        await interaction.response.edit_message(
            embed=embed,
            file=attachment,
            view=GoldView(
                self._announcement.match,
                highlight_puuids=self._announcement.highlight_puuids,
                announcement=self._announcement,
            ),
        )


class MatchAnnouncementView(GoldView):
    """Completed-match controls, including the Flex-to-Solo rank toggle."""

    def __init__(self, announcement: MatchAnnouncement) -> None:
        """Initialize controls for one match announcement."""
        super().__init__(
            announcement.match,
            highlight_puuids=announcement.highlight_puuids,
            announcement=announcement,
        )


class _LiveSoloRankButton(discord.ui.Button):
    """Switch a Flex live-game announcement to Solo/Duo standings."""

    def __init__(self, announcement: LiveGameAnnouncement) -> None:
        """Initialize the button."""
        super().__init__(
            label="Show Ranked Solo",
            style=discord.ButtonStyle.secondary,
            emoji="🏆",
        )
        self._announcement = announcement

    async def callback(self, interaction: discord.Interaction) -> None:
        """Render the live lobby with Ranked Solo/Duo standings."""
        embed = await build_live_game_embed(
            self._announcement, rank_queue_id=SOLO_QUEUE_ID
        )
        await interaction.response.edit_message(
            embed=embed,
            view=LiveGameAnnouncementView(self._announcement, rank_queue_id=SOLO_QUEUE_ID),
        )


class _LiveFlexRankButton(discord.ui.Button):
    """Switch a live-game announcement back to Flex standings."""

    def __init__(self, announcement: LiveGameAnnouncement) -> None:
        """Initialize the button."""
        super().__init__(
            label="Show Ranked Flex",
            style=discord.ButtonStyle.secondary,
            emoji="🏆",
        )
        self._announcement = announcement

    async def callback(self, interaction: discord.Interaction) -> None:
        """Render the live lobby with Flex standings."""
        embed = await build_live_game_embed(self._announcement)
        await interaction.response.edit_message(
            embed=embed,
            view=LiveGameAnnouncementView(self._announcement),
        )


class LiveGameAnnouncementView(discord.ui.View):
    """Controls attached to live-game announcements."""

    def __init__(
        self, announcement: LiveGameAnnouncement, *, rank_queue_id: int | None = None
    ) -> None:
        """Initialize the appropriate rank toggle when applicable."""
        super().__init__(timeout=900)
        if announcement.game.get("gameQueueConfigId") == FLEX_QUEUE_ID:
            button = (
                _LiveFlexRankButton(announcement)
                if rank_queue_id == SOLO_QUEUE_ID
                else _LiveSoloRankButton(announcement)
            )
            self.add_item(button)


async def publish(
    bot: Any,
    announcements: Sequence[MatchAnnouncement],
    *,
    global_channel: Any = _UNSET,
) -> None:
    """Post each announcement to the configured channel.

    One failure is logged and skipped rather than dropping the rest of the batch.
    """
    if not announcements:
        return

    configured, accounts = await asyncio.gather(
        asyncio.to_thread(load_guild_channels),
        asyncio.to_thread(load_accounts),
    )
    if global_channel is _UNSET:
        global_channel = await resolve_announcement_channel(bot)
    member_cache: dict[tuple[int, int], bool] = {}
    channel_cache: dict[int, Any | None] = {}
    for announcement in announcements:
        channels = await resolve_announcement_channels(
            bot,
            announcement.highlight_puuids,
            configured=configured,
            accounts=accounts,
            member_cache=member_cache,
            channel_cache=channel_cache,
            global_channel=global_channel,
        )
        if not channels:
            LOGGER.warning("Match announcement resolved to zero channels")
            continue
        try:
            embed, chart = await build_announcement_embed(announcement)
            chart_bytes: bytes | None = None
            chart_filename = DAMAGE_CHART_FILENAME
            if chart is not None:
                chart.fp.seek(0)
                chart_bytes = chart.fp.read()
                chart_filename = chart.filename
                chart.close()
        except Exception:
            LOGGER.exception("Could not render a match announcement")
            continue
        for channel in channels:
            try:
                chart_file = (
                    discord.File(io.BytesIO(chart_bytes), filename=chart_filename)
                    if chart_bytes is not None
                    else None
                )
                await channel.send(
                    embed=embed,
                    file=chart_file,
                    view=MatchAnnouncementView(announcement),
                )
            except Exception:
                LOGGER.exception(
                    "Could not post a match announcement to %s", channel.id
                )


async def publish_live_games(
    bot: Any,
    announcements: Sequence[LiveGameAnnouncement],
    *,
    global_channel: Any = _UNSET,
) -> None:
    """Post newly detected live lobbies to the normal announcement channel."""
    if not announcements:
        return
    configured, accounts = await asyncio.gather(
        asyncio.to_thread(load_guild_channels),
        asyncio.to_thread(load_accounts),
    )
    if global_channel is _UNSET:
        global_channel = await resolve_announcement_channel(bot)
    member_cache: dict[tuple[int, int], bool] = {}
    channel_cache: dict[int, Any | None] = {}
    for announcement in announcements:
        channels = await resolve_announcement_channels(
            bot,
            announcement.highlight_puuids,
            configured=configured,
            accounts=accounts,
            member_cache=member_cache,
            channel_cache=channel_cache,
            global_channel=global_channel,
        )
        if not channels:
            LOGGER.warning("Live-game announcement resolved to zero channels")
            continue
        try:
            embed = await build_live_game_embed(announcement)
        except Exception:
            LOGGER.exception("Could not render a live-game announcement")
            continue
        for channel in channels:
            try:
                await channel.send(
                    embed=embed,
                    view=LiveGameAnnouncementView(announcement),
                )
            except Exception:
                LOGGER.exception(
                    "Could not post a live-game announcement to %s", channel.id
                )
