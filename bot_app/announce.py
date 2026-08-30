"""Formatting and publishing match announcements.

Both pollers and ``/selftest`` share this. The two pollers previously carried
their own copy of the publish step — resolve channel, build embed, build
columns, build chart, send — which is where they drifted apart.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass
from typing import Any, Sequence

import discord

from . import ddragon, emoji as emoji_lookup
from .charts import (
    DAMAGE_CHART_FILENAME,
    build_damage_chart,
    build_jungle_proximity_comparison_chart,
    build_team_gold_difference_chart,
)
from .config import get_settings
from .jungle_proximity_render import jungle_chart_checkpoints
from .queues import FLEX_QUEUE_ID, RANKED_QUEUE_IDS, SOLO_QUEUE_ID, lobby_queue_name, queue_name
from .ranks import RankSnapshot
from .rating import PlayerRating, rate_match
from .render import (
    NameStyle,
    add_inventory_columns,
    add_rating_columns,
    add_team_columns,
    build_inventory_columns,
    build_match_columns,
    build_lobby_columns,
    build_rating_columns,
    format_duration,
    kda_text,
    make_embed,
    outcome_color,
    rank_text,
)
from .riot import RiotAPIError, TTLCache, get_client
from .routing import opgg_url
from .store import (
    load_accounts,
    load_embed_button_states,
    load_guild_channels,
    remember_embed_button_state,
)

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


def _announcement_payload(announcement: MatchAnnouncement) -> dict[str, Any]:
    """Serialize the data needed to rebuild a completed-match view."""
    return {
        "text": announcement.text,
        "outcome": announcement.outcome,
        "match": announcement.match,
        "highlight_puuids": sorted(announcement.highlight_puuids),
        "name_style": announcement.name_style.value,
    }


def _live_game_payload(announcement: LiveGameAnnouncement) -> dict[str, Any]:
    """Serialize the data needed to rebuild a live-game view."""
    return {
        "text": announcement.text,
        "game": announcement.game,
        "highlight_puuids": sorted(announcement.highlight_puuids),
    }


def _announcement_from_payload(payload: dict[str, Any]) -> MatchAnnouncement:
    """Restore a completed-match announcement from JSON state."""
    try:
        name_style = NameStyle(payload.get("name_style", NameStyle.SUMMONER.value))
    except ValueError:
        name_style = NameStyle.SUMMONER
    return MatchAnnouncement(
        text=str(payload.get("text", "")),
        outcome=payload.get("outcome"),
        match=payload.get("match", {}),
        highlight_puuids=set(payload.get("highlight_puuids", [])),
        name_style=name_style,
    )


def _live_game_from_payload(payload: dict[str, Any]) -> LiveGameAnnouncement:
    """Restore a live-game announcement from JSON state."""
    return LiveGameAnnouncement(
        text=str(payload.get("text", "")),
        game=payload.get("game", {}),
        highlight_puuids=set(payload.get("highlight_puuids", [])),
    )


async def remember_match_view_state(
    message: Any, channel_id: int, announcement: MatchAnnouncement
) -> None:
    """Persist a completed-match embed's button state so it survives a bot restart.

    Every poster of a MatchAnnouncementView must call this — the poller does,
    but a caller that skips it (as the /match command previously did) leaves
    that message's buttons dead after the next restart, since there's nothing
    for register_persistent_embed_views to restore from.
    """
    message_id = getattr(message, "id", None)
    if isinstance(message_id, int) and isinstance(channel_id, int):
        await asyncio.to_thread(
            remember_embed_button_state,
            message_id,
            channel_id,
            "match",
            _announcement_payload(announcement),
        )


async def remember_live_game_view_state(
    message: Any, channel_id: int, announcement: LiveGameAnnouncement
) -> None:
    """Persist a live-game embed's button state so it survives a bot restart.

    Every poster of a LiveGameAnnouncementView must call this — the poller does,
    and /livegame must too, since otherwise that message's buttons are dead
    after the next restart with nothing for register_persistent_embed_views to
    restore from.
    """
    message_id = getattr(message, "id", None)
    if isinstance(message_id, int) and isinstance(channel_id, int):
        await asyncio.to_thread(
            remember_embed_button_state,
            message_id,
            channel_id,
            "live_game",
            _live_game_payload(announcement),
        )


def register_persistent_embed_views(bot: Any) -> int:
    """Register SQLite-backed match/live-game views after a bot restart."""
    restored = 0
    states = load_embed_button_states()
    LOGGER.debug("Restoring persistent embed views from %d stored states", len(states))
    for state in states:
        try:
            view = (
                MatchAnnouncementView(_announcement_from_payload(state["payload"]))
                if state["kind"] == "match"
                else LiveGameAnnouncementView(_live_game_from_payload(state["payload"]))
                if state["kind"] == "live_game"
                else None
            )
            if view is None:
                continue
            bot.add_view(view, message_id=state["message_id"])
            restored += 1
        except (KeyError, TypeError, ValueError):
            LOGGER.warning("Skipping malformed persistent embed view state")
    LOGGER.info("Restored %d persistent embed views", restored)
    return restored


def _relative_timestamp(epoch_ms: int | None) -> str:
    """Discord's live-updating relative-time markdown, or "" if unknown."""
    if not epoch_ms:
        return ""
    return f"<t:{int(epoch_ms / 1000)}:R>"


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
        LOGGER.debug("Skipping format_match: game not yet finished")
        return None

    queue_id = info.get("queueId")
    if require_ranked_queue and queue_id not in RANKED_QUEUE_IDS:
        LOGGER.debug("Skipping format_match: queue_id=%s is not ranked", queue_id)
        return None

    participants = info.get("participants", []) or []
    duration = format_duration(info.get("gameDuration", 0))
    catalog = ddragon.catalog()
    participants_by_puuid = {
        p.get("puuid"): p for p in participants if p.get("puuid") is not None
    }

    lines: list[tuple[str, str]] = []
    results: set[str] = set()
    for player in players:
        participant = participants_by_puuid.get(player.puuid)
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
            standing = rank_text(player.rank, with_winrate=True)
            if standing:
                line += f" | {standing}"
            if player.lp_change:
                line += f" | {player.lp_change}"

        lines.append((result, line))

    if not lines:
        LOGGER.debug("Skipping format_match: no tracked players found in match")
        return None

    ago = _relative_timestamp(info.get("gameEndTimestamp"))
    ago_suffix = f" • {ago}" if ago else ""

    team_kills: dict[int, int] = {}
    for participant in participants:
        team_id = participant.get("teamId")
        if team_id is None:
            continue
        team_kills[team_id] = team_kills.get(team_id, 0) + (participant.get("kills") or 0)

    scoreline = ""
    if len(team_kills) == 2:
        team_ids = sorted(team_kills)
        if len(results) == 1:
            tracked_team = next(
                (
                    participants_by_puuid[player.puuid].get("teamId")
                    for player in players
                    if player.puuid in participants_by_puuid
                ),
                None,
            )
            if tracked_team in team_ids:
                team_ids = [tracked_team, next(t for t in team_ids if t != tracked_team)]
        scoreline = f" • {team_kills[team_ids[0]]}-{team_kills[team_ids[1]]}"

    if len(results) == 1:
        outcome: str | None = next(iter(results))
        header = f"**{queue_name(queue_id)} - {outcome}** ({duration}){ago_suffix}{scoreline}"
        body = "\n".join(line for _, line in lines)
    else:
        outcome = None
        header = f"**{queue_name(queue_id)}** ({duration}){ago_suffix}{scoreline}"
        body = "\n".join(f"**{result}** — {line}" for result, line in lines)

    LOGGER.debug("Formatted match announcement: players=%d outcome=%s", len(lines), outcome)
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
    participants_by_puuid = {
        entry.get("puuid"): entry for entry in participants if entry.get("puuid") is not None
    }

    for player in players:
        participant = participants_by_puuid.get(player.puuid)
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
        LOGGER.debug("Skipping format_live_game: no tracked players found in lobby")
        return None

    game_length = game.get("gameLength", 0)
    start_ms = game.get("gameStartTime") or int(time.time() * 1000) - game_length * 1000
    ago = _relative_timestamp(start_ms)
    ago_suffix = f" • started {ago}" if ago else ""

    return LiveGameAnnouncement(
        text=(
            f"**{lobby_queue_name(game.get('gameQueueConfigId'))} — Live Game** "
            f"({format_duration(game_length)}){ago_suffix}\n" + "\n".join(lines)
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
    LOGGER.debug("Announcement channel %s not cached; fetching", channel_id)
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
    LOGGER.debug("Resolved %d announcement channel(s)", len(channels))
    return channels


_DEFAULT_CHART_FIELD = "totalDamageDealtToChampions"

# Mirrors GoldView's six chart buttons: (field, label). Shared so any control
# that rebuilds the embed can render whichever chart was already on screen
# instead of always resetting to the default damage chart.
_CHART_LABELS: dict[str, str] = {
    "totalDamageDealtToChampions": "Damage Done",
    "goldEarned": "Gold Graph",
    "teamGoldDifference": "Gold Difference Graph",
    "totalDamageTaken": "Damage Taken",
    "healingAndShielding": "Healing and Shielding",
    "visionScore": "Vision Score",
    "jungleProximity": "Jungle Proximity",
}

_CHART_CACHE = TTLCache(ttl_seconds=15 * 60, max_entries=256)


def _chart_filename(field: str) -> str:
    """Stable attachment filename for one chart metric."""
    return DAMAGE_CHART_FILENAME if field == _DEFAULT_CHART_FIELD else f"{field}.png"


def _render_match_chart_bytes(
    match: dict[str, Any], field: str, highlight_puuids: set[str]
) -> tuple[bytes, str] | discord.File | None:
    """Render one chart into reusable bytes on a worker thread."""
    filename = _chart_filename(field)
    if field == "teamGoldDifference":
        info = match.get("info", {})
        match_id = match.get("metadata", {}).get("matchId")
        timeline = get_client().match_timeline(
            match_id, info.get("platformId") or "NA1"
        )
        chart = build_team_gold_difference_chart(timeline, filename=filename)
    elif field == "jungleProximity":
        info = match.get("info", {})
        match_id = match.get("metadata", {}).get("matchId")
        timeline = get_client().match_timeline(
            match_id, info.get("platformId") or "NA1"
        )
        checkpoints = jungle_chart_checkpoints(match, timeline)
        chart = (
            build_jungle_proximity_comparison_chart(checkpoints, filename=filename)
            if checkpoints
            else None
        )
    else:
        chart = build_damage_chart(
            match,
            highlight_puuids,
            metric_field=field,
            chart_title=_CHART_LABELS.get(field, field),
            filename=filename,
        )
    if chart is None:
        return None
    # Several network-free tests replace the renderer with an opaque sentinel;
    # only real Discord files can be converted into reusable bytes.
    if not isinstance(chart, discord.File):
        return chart
    try:
        file_pointer = chart.fp
    except AttributeError:
        return chart
    try:
        file_pointer.seek(0)
        return file_pointer.read(), chart.filename
    finally:
        chart.close()


async def _build_match_chart(
    match: dict[str, Any], field: str, highlight_puuids: set[str]
) -> discord.File | None:
    """Return a fresh Discord file backed by bounded cached chart bytes."""
    match_id = match.get("metadata", {}).get("matchId") or id(match)
    cache_key = (match_id, field, tuple(sorted(highlight_puuids)))
    LOGGER.debug("Chart request: match_id=%s field=%s", match_id, field)
    rendered = await asyncio.to_thread(
        _CHART_CACHE.get_or_set,
        cache_key,
        lambda: _render_match_chart_bytes(match, field, highlight_puuids),
        cache_none=True,
    )
    if rendered is None:
        return None
    if not isinstance(rendered, tuple):
        return rendered
    data, filename = rendered
    return discord.File(io.BytesIO(data), filename=filename)


def _existing_chart_url(message: Any, field: str) -> str | None:
    """Return the current unchanged stat-chart URL when its attachment remains."""
    expected = _chart_filename(field)
    attachments = getattr(message, "attachments", ()) or ()
    if not isinstance(attachments, (list, tuple)):
        return None
    if not any(getattr(item, "filename", None) == expected for item in attachments):
        return None
    embeds = getattr(message, "embeds", ()) or ()
    image = getattr(embeds[0], "image", None) if embeds else None
    url = getattr(image, "url", None)
    return str(url) if url else None


async def build_announcement_embed(
    announcement: MatchAnnouncement,
    *,
    rank_queue_id: int | None = None,
    show_rank_names: bool = False,
    show_mastery: bool = False,
    active_field: str = _DEFAULT_CHART_FIELD,
    existing_chart_url: str | None = None,
) -> tuple[discord.Embed, discord.File | None]:
    """Render an announcement into an embed plus its optional chart attachment.

    ``active_field`` picks which of GoldView's charts to render — defaulting
    to the damage chart for a fresh announcement, but callers that already
    have a different chart on screen (e.g. the rank-toggle buttons) pass the
    field currently shown so toggling ranks doesn't reset it. ``show_mastery``
    switches the player label to end-of-game champion mastery points and
    reuses values fetched by live-game views.

    Both the column lookups and the chart render are blocking, so each runs on
    a worker thread rather than on the event loop.
    """
    LOGGER.debug(
        "Building match announcement embed: active_field=%s show_mastery=%s show_rank_names=%s",
        active_field, show_mastery, show_rank_names,
    )
    embed = make_embed(announcement.text, color=outcome_color(announcement.outcome))
    info = announcement.match.get("info", {})

    columns_task = asyncio.to_thread(
        build_match_columns,
        info.get("participants", []) or [],
        server=info.get("platformId"),
        queue_id=rank_queue_id or info.get("queueId"),
        highlight_puuids=announcement.highlight_puuids,
        name_style=announcement.name_style,
        show_rank_names=show_rank_names,
        show_mastery=show_mastery,
    )
    chart_task = (
        None
        if existing_chart_url
        else _build_match_chart(
            announcement.match, active_field, announcement.highlight_puuids
        )
    )
    if chart_task is None:
        columns = await columns_task
        chart = None
    else:
        columns, chart = await asyncio.gather(columns_task, chart_task)
    add_team_columns(embed, columns, include_rank_rows=False)
    if existing_chart_url:
        embed.set_image(url=existing_chart_url)
    elif chart is not None:
        embed.set_image(url=f"attachment://{chart.filename}")
    return embed, chart


async def build_live_game_embed(
    announcement: LiveGameAnnouncement,
    *,
    rank_queue_id: int | None = None,
    name_style: NameStyle = NameStyle.SUMMONER,
    show_rank_names: bool = False,
    show_mastery: bool = False,
) -> discord.Embed:
    """Render a live-game announcement with selectable name-column displays.

    Live games never render a separate rank block underneath the player rows.
    The Display dropdown's Ranks mode replaces each summoner name with that
    player's rank, matching the completed-match display behavior.
    """
    game = announcement.game
    LOGGER.debug("Building live-game announcement embed: show_mastery=%s show_rank_names=%s", show_mastery, show_rank_names)
    embed = make_embed(announcement.text, color=discord.Color.gold())
    columns = await asyncio.to_thread(
        build_lobby_columns,
        game,
        game.get("platformId") or "NA1",
        queue_id=rank_queue_id,
        name_style=name_style,
        show_rank_names=show_rank_names,
        show_mastery=show_mastery,
    )
    add_team_columns(embed, columns, include_rank_rows=False)
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


async def _fetch_match_timeline(match: dict[str, Any]) -> dict[str, Any] | None:
    """The timeline for one match, or None if it can't be fetched.

    Rating without a timeline still works — it just loses the bounty ledger,
    lane diffs, and objective-presence metrics, per :func:`rate_match`'s
    documented degradation — so a failed fetch here is a quality loss, not a
    reason to refuse the button.
    """
    info = match.get("info", {})
    match_id = match.get("metadata", {}).get("matchId")
    if not match_id:
        return None
    try:
        return await asyncio.to_thread(
            get_client().match_timeline, match_id, info.get("platformId") or "NA1"
        )
    except RiotAPIError:
        LOGGER.info("No timeline available for %s; rating from box score alone", match_id)
        return None


def build_rating_embed(
    match: dict[str, Any], ratings: dict[str, PlayerRating]
) -> discord.Embed:
    """Render every player's lobby-relative rating as blue/red score columns."""
    info = match.get("info", {})
    embed = make_embed(
        f"Player ratings — {queue_name(info.get('queueId'))}",
        title="Match Ratings",
        color=discord.Color.blue(),
    )
    if not ratings:
        embed.description = (
            embed.description or ""
        ) + (
            "\n\nThis game is too short, has too few players, or isn't a "
            "standard Summoner's Rift match, to rate."
        )
        return embed
    add_rating_columns(embed, build_rating_columns(match, ratings))
    notes = sorted({note for rating in ratings.values() for note in rating.notes})
    if notes:
        embed.add_field(name="Notes", value="\n".join(f"• {note}" for note in notes), inline=False)
    return embed


def build_inventory_embed(
    match: dict[str, Any], *, color: discord.Color | None = None,
    timeline: dict[str, Any] | None = None,
) -> discord.Embed:
    """Render every player's end-of-game item slots as blue/red name/items columns.

    ``color`` keeps the embed's side stripe matching the base match embed
    (win/loss) instead of switching to a fixed color when Items is selected.
    ``timeline`` supplies the shared renderer's ADC boot fallback when the
    final Match-V5 participant slots no longer contain a purchased boot.
    """
    info = match.get("info", {})
    embed = make_embed(
        f"Item builds — {queue_name(info.get('queueId'))}",
        title="Items",
        color=color or discord.Color.dark_gold(),
    )
    add_inventory_columns(embed, build_inventory_columns(match, timeline))
    return embed


_MATCH_CHART_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("totalDamageDealtToChampions", "Damage Done", "⚔️", "Damage dealt to champions, per player"),
    ("goldEarned", "Gold Graph", "🪙", "Gold earned, per player"),
    ("teamGoldDifference", "Gold Difference Graph", "📊", "Team gold difference over the game"),
    ("jungleProximity", "Jungle Proximity", "🌳", "Both junglers' lane scores at 5/10/15 minutes"),
    ("totalDamageTaken", "Damage Taken", "🛡️", "Damage taken, per player"),
    ("healingAndShielding", "Healing and Shielding", "💚", "Healing and shielding provided, per player"),
    ("visionScore", "Vision Score", "👁️", "Vision score, per player"),
)

_MATCH_DISPLAY_PLAYERS = "players"
_MATCH_DISPLAY_RANKS = "ranks"
_MATCH_DISPLAY_RANKINGS = "rankings"
_MATCH_DISPLAY_RATINGS = "ratings"
_MATCH_DISPLAY_INVENTORY = "inventory"
_MATCH_DISPLAY_MASTERY = "mastery"


class _ChartSelect(discord.ui.Select):
    """Choose which match statistic chart is shown."""

    def __init__(
        self,
        match: dict[str, Any],
        *,
        highlight_puuids: set[str] | None = None,
        active_field: str | None = None,
        announcement: MatchAnnouncement | None = None,
    ) -> None:
        """Initialize the chart select."""
        options = [
            discord.SelectOption(
                label=label,
                value=field,
                emoji=emoji,
                description=description,
                default=field == active_field,
            )
            for field, label, emoji, description in _MATCH_CHART_FIELDS
        ]
        super().__init__(
            placeholder="Chart",
            options=options,
            custom_id="embed:chart:select",
        )
        self._match = match
        self._highlight_puuids = highlight_puuids or set()
        self._announcement = announcement

    async def callback(self, interaction: discord.Interaction) -> None:
        """Render the selected statistic chart."""
        # Chart generation can include a Riot timeline request and must not
        # leave Discord's three-second component acknowledgement window open.
        await interaction.response.defer()
        field = self.values[0]
        label = next(label for value, label, _, _desc in _MATCH_CHART_FIELDS if value == field)
        chart = await _build_match_chart(self._match, field, self._highlight_puuids)
        embed = interaction.message.embeds[0].copy() if interaction.message.embeds else make_embed(
            label, color=discord.Color.gold()
        )
        if chart is not None:
            embed.set_image(url=f"attachment://{chart.filename}")
        await interaction.edit_original_response(
            embed=embed,
            file=chart,
            view=GoldView(
                self._match,
                highlight_puuids=self._highlight_puuids,
                active_field=field,
                announcement=self._announcement,
            ),
        )


class _MatchDisplaySelect(discord.ui.Select):
    """Choose the match embed's mode, including end-of-game champion mastery.

    Guest announcements render with :attr:`NameStyle.CHAMPION` to stay
    anonymous, so the Players option is omitted for them — picking it would
    put summoner names back in the name column.
    """

    def __init__(
        self,
        announcement: MatchAnnouncement,
        *,
        rank_queue_id: int | None = None,
        show_rank_names: bool = False,
        active_field: str = _DEFAULT_CHART_FIELD,
        mode: str = "match",
        show_mastery: bool = False,
    ) -> None:
        """Initialize the display select."""
        if mode == "ratings":
            current = _MATCH_DISPLAY_RATINGS
        elif mode == "inventory":
            current = _MATCH_DISPLAY_INVENTORY
        elif mode == "mastery":
            current = _MATCH_DISPLAY_MASTERY
        elif show_mastery:
            current = _MATCH_DISPLAY_MASTERY
        else:
            is_solo_rankings = rank_queue_id == SOLO_QUEUE_ID and show_rank_names
            current = (
                _MATCH_DISPLAY_RANKINGS
                if is_solo_rankings
                else _MATCH_DISPLAY_RANKS
                if show_rank_names
                else _MATCH_DISPLAY_PLAYERS
            )
        anonymous = announcement.name_style is NameStyle.CHAMPION
        options: list[discord.SelectOption] = []
        if not anonymous:
            options.append(
                discord.SelectOption(
                    label="Players",
                    value=_MATCH_DISPLAY_PLAYERS,
                    emoji="🧑",
                    description="Summoner names and KDA",
                    default=current == _MATCH_DISPLAY_PLAYERS,
                )
            )
        is_flex_match = announcement.match.get("info", {}).get("queueId") == FLEX_QUEUE_ID
        options.append(
            discord.SelectOption(
                label="Ranked Flex" if is_flex_match else "Ranked Solo",
                value=_MATCH_DISPLAY_RANKS,
                emoji="🏅",
                description="Replace names with rank standing",
                default=current == _MATCH_DISPLAY_RANKS,
            )
        )
        if is_flex_match:
            options.append(
                discord.SelectOption(
                    label="Solo Rank",
                    value=_MATCH_DISPLAY_RANKINGS,
                    emoji="🏆",
                    description="Show Solo/Duo standings instead",
                    default=current == _MATCH_DISPLAY_RANKINGS,
                )
            )
        options.append(
            discord.SelectOption(
                label="Ratings",
                value=_MATCH_DISPLAY_RATINGS,
                emoji="📈",
                description="Score each player's individual performance",
                default=current == _MATCH_DISPLAY_RATINGS,
            )
        )
        options.append(
            discord.SelectOption(
                label="Items",
                value=_MATCH_DISPLAY_INVENTORY,
                emoji="🎒",
                description="End-of-game item builds",
                default=current == _MATCH_DISPLAY_INVENTORY,
            )
        )
        options.append(
            discord.SelectOption(
                label="Mastery",
                value=_MATCH_DISPLAY_MASTERY,
                emoji="⭐",
                description="Champion mastery points",
                default=current == _MATCH_DISPLAY_MASTERY,
            )
        )
        super().__init__(placeholder="Display", options=options, custom_id="embed:match:display")
        self._announcement = announcement
        self._active_field = active_field

    async def callback(self, interaction: discord.Interaction) -> None:
        """Render the match in the selected mode, keeping the active chart.

        A missing timeline degrades the rating rather than failing it — see
        :func:`_fetch_match_timeline` — so Ratings always has something to
        show.
        """
        await interaction.response.defer()
        choice = self.values[0]
        existing_chart_url = _existing_chart_url(
            interaction.message, self._active_field
        )
        if choice == _MATCH_DISPLAY_RATINGS:
            timeline = await _fetch_match_timeline(self._announcement.match)
            ratings = await asyncio.to_thread(rate_match, self._announcement.match, timeline)
            embed = build_rating_embed(self._announcement.match, ratings)
            chart = None
            if existing_chart_url:
                embed.set_image(url=existing_chart_url)
            else:
                chart = await _build_match_chart(
                    self._announcement.match,
                    self._active_field,
                    self._announcement.highlight_puuids,
                )
            if chart is not None:
                embed.set_image(url=f"attachment://{chart.filename}")
            edit_kwargs = dict(
                embed=embed,
                view=_MatchRatingView(self._announcement, active_field=self._active_field),
            )
            if chart is not None:
                edit_kwargs.update(file=chart, attachments=[])
            elif not existing_chart_url:
                edit_kwargs["attachments"] = []
            await interaction.edit_original_response(**edit_kwargs)
            return
        if choice == _MATCH_DISPLAY_INVENTORY:
            timeline = await _fetch_match_timeline(self._announcement.match)
            embed = build_inventory_embed(
                self._announcement.match,
                color=outcome_color(self._announcement.outcome),
                timeline=timeline,
            )
            chart = None
            if existing_chart_url:
                embed.set_image(url=existing_chart_url)
            else:
                chart = await _build_match_chart(
                    self._announcement.match,
                    self._active_field,
                    self._announcement.highlight_puuids,
                )
            if chart is not None:
                embed.set_image(url=f"attachment://{chart.filename}")
            edit_kwargs = dict(
                embed=embed,
                view=_MatchInventoryView(self._announcement, active_field=self._active_field),
            )
            if chart is not None:
                edit_kwargs.update(file=chart, attachments=[])
            elif not existing_chart_url:
                edit_kwargs["attachments"] = []
            await interaction.edit_original_response(**edit_kwargs)
            return
        show_mastery = choice == _MATCH_DISPLAY_MASTERY
        show_rank_names = choice not in (_MATCH_DISPLAY_PLAYERS, _MATCH_DISPLAY_MASTERY)
        rank_queue_id = SOLO_QUEUE_ID if choice == _MATCH_DISPLAY_RANKINGS else None
        embed, chart = await build_announcement_embed(
            self._announcement,
            rank_queue_id=rank_queue_id,
            show_rank_names=show_rank_names,
            show_mastery=show_mastery,
            active_field=self._active_field,
            existing_chart_url=existing_chart_url,
        )
        edit_kwargs = dict(
            embed=embed,
            view=GoldView(
                self._announcement.match,
                highlight_puuids=self._announcement.highlight_puuids,
                active_field=self._active_field,
                announcement=self._announcement,
                rank_queue_id=rank_queue_id,
                show_rank_names=show_rank_names,
                show_mastery=show_mastery,
            ),
        )
        if chart is not None:
            edit_kwargs.update(file=chart, attachments=[])
        elif not existing_chart_url:
            edit_kwargs["attachments"] = []
        await interaction.edit_original_response(**edit_kwargs)


class GoldView(discord.ui.View):
    """The gold detail control attached to a completed-match post."""

    def __init__(
        self,
        match: dict[str, Any],
        *,
        highlight_puuids: set[str] | None = None,
        active_field: str | None = "totalDamageDealtToChampions",
        announcement: MatchAnnouncement | None = None,
        rank_queue_id: int | None = None,
        show_rank_names: bool = False,
        show_mastery: bool = False,
    ) -> None:
        """Initialize the instance."""
        super().__init__(timeout=None)
        highlight_puuids = highlight_puuids or set()
        self._highlight_puuids = highlight_puuids
        if announcement is not None:
            self.add_item(
                _MatchDisplaySelect(
                    announcement,
                    rank_queue_id=rank_queue_id,
                    show_rank_names=show_rank_names,
                    show_mastery=show_mastery,
                    active_field=active_field,
                )
            )
        self.add_item(
            _ChartSelect(
                match,
                highlight_puuids=highlight_puuids,
                active_field=active_field,
                announcement=announcement,
            )
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


class _RatingChartSelect(discord.ui.Select):
    """Choose which match statistic chart is shown behind the rating columns."""

    def __init__(
        self,
        announcement: MatchAnnouncement,
        *,
        active_field: str = _DEFAULT_CHART_FIELD,
    ) -> None:
        """Initialize the chart select."""
        options = [
            discord.SelectOption(
                label=label,
                value=field,
                emoji=emoji,
                description=description,
                default=field == active_field,
            )
            for field, label, emoji, description in _MATCH_CHART_FIELDS
        ]
        super().__init__(
            placeholder="Chart",
            options=options,
            custom_id="embed:rating:chart:select",
        )
        self._announcement = announcement

    async def callback(self, interaction: discord.Interaction) -> None:
        """Swap the chart image, keeping the rating columns already on screen."""
        await interaction.response.defer()
        field = self.values[0]
        chart = await _build_match_chart(
            self._announcement.match, field, self._announcement.highlight_puuids
        )
        embed = (
            interaction.message.embeds[0].copy()
            if interaction.message.embeds
            else build_rating_embed(self._announcement.match, {})
        )
        if chart is not None:
            embed.set_image(url=f"attachment://{chart.filename}")
        await interaction.edit_original_response(
            embed=embed,
            file=chart,
            attachments=[],
            view=_MatchRatingView(self._announcement, active_field=field),
        )


class _MatchRatingView(discord.ui.View):
    """The controls shown while a match embed is in its rating view."""

    def __init__(
        self, announcement: MatchAnnouncement, *, active_field: str = _DEFAULT_CHART_FIELD
    ) -> None:
        """Initialize the instance."""
        super().__init__(timeout=None)
        self.add_item(_MatchDisplaySelect(announcement, active_field=active_field, mode="ratings"))
        self.add_item(_RatingChartSelect(announcement, active_field=active_field))


class _InventoryChartSelect(discord.ui.Select):
    """Choose which match statistic chart is shown behind the item columns."""

    def __init__(
        self,
        announcement: MatchAnnouncement,
        *,
        active_field: str = _DEFAULT_CHART_FIELD,
    ) -> None:
        """Initialize the chart select."""
        options = [
            discord.SelectOption(
                label=label,
                value=field,
                emoji=emoji,
                description=description,
                default=field == active_field,
            )
            for field, label, emoji, description in _MATCH_CHART_FIELDS
        ]
        super().__init__(
            placeholder="Chart",
            options=options,
            custom_id="embed:inventory:chart:select",
        )
        self._announcement = announcement

    async def callback(self, interaction: discord.Interaction) -> None:
        """Swap the chart image, keeping the item columns already on screen."""
        await interaction.response.defer()
        field = self.values[0]
        chart = await _build_match_chart(
            self._announcement.match, field, self._announcement.highlight_puuids
        )
        embed = (
            interaction.message.embeds[0].copy()
            if interaction.message.embeds
            else build_inventory_embed(
                self._announcement.match,
                color=outcome_color(self._announcement.outcome),
            )
        )
        if chart is not None:
            embed.set_image(url=f"attachment://{chart.filename}")
        await interaction.edit_original_response(
            embed=embed,
            file=chart,
            attachments=[],
            view=_MatchInventoryView(self._announcement, active_field=field),
        )


class _MatchInventoryView(discord.ui.View):
    """The controls shown while a match embed is in its final-items view."""

    def __init__(
        self, announcement: MatchAnnouncement, *, active_field: str = _DEFAULT_CHART_FIELD
    ) -> None:
        """Initialize the instance."""
        super().__init__(timeout=None)
        self.add_item(_MatchDisplaySelect(announcement, mode="inventory", active_field=active_field))
        self.add_item(_InventoryChartSelect(announcement, active_field=active_field))


_LIVE_DISPLAY_PLAYERS = "players"
_LIVE_DISPLAY_RANKS = "ranks"
_LIVE_DISPLAY_FLEX_RANK = "flex_rank"
_LIVE_DISPLAY_SOLO_RANK = "solo_rank"
_LIVE_DISPLAY_MASTERY = "mastery"

_LIVE_DISPLAY_LABELS: dict[str, str] = {
    _LIVE_DISPLAY_PLAYERS: "Players",
    _LIVE_DISPLAY_RANKS: "Ranks",
    _LIVE_DISPLAY_FLEX_RANK: "Flex Rank",
    _LIVE_DISPLAY_SOLO_RANK: "Solo Rank",
    _LIVE_DISPLAY_MASTERY: "Mastery",
}

_LIVE_DISPLAY_EMOJIS: dict[str, str] = {
    _LIVE_DISPLAY_PLAYERS: "🧑",
    _LIVE_DISPLAY_RANKS: "🏅",
    _LIVE_DISPLAY_FLEX_RANK: "🏅",
    _LIVE_DISPLAY_SOLO_RANK: "🏆",
    _LIVE_DISPLAY_MASTERY: "⭐",
}

_LIVE_DISPLAY_DESCRIPTIONS: dict[str, str] = {
    _LIVE_DISPLAY_PLAYERS: "Summoner names",
    _LIVE_DISPLAY_RANKS: "Replace names with rank standing",
    _LIVE_DISPLAY_FLEX_RANK: "Show this lobby's Flex standings",
    _LIVE_DISPLAY_SOLO_RANK: "Show Solo/Duo standings instead",
    _LIVE_DISPLAY_MASTERY: "Mastery on their current champion",
}


class _LiveDisplaySelect(discord.ui.Select):
    """Pick what the live lobby's name column shows: players, ranks, or mastery.

    Flex lobbies offer separate Flex Rank and Solo Rank options instead of a
    single Ranks entry, since either queue's standings can be shown.
    """

    def __init__(
        self,
        announcement: LiveGameAnnouncement,
        *,
        active_mode: str,
        is_flex_lobby: bool,
    ) -> None:
        """Initialize the display dropdown."""
        modes = (
            (_LIVE_DISPLAY_PLAYERS, _LIVE_DISPLAY_FLEX_RANK, _LIVE_DISPLAY_SOLO_RANK, _LIVE_DISPLAY_MASTERY)
            if is_flex_lobby
            else (_LIVE_DISPLAY_PLAYERS, _LIVE_DISPLAY_RANKS, _LIVE_DISPLAY_MASTERY)
        )
        options = [
            discord.SelectOption(
                label=_LIVE_DISPLAY_LABELS[mode],
                value=mode,
                emoji=_LIVE_DISPLAY_EMOJIS[mode],
                description=_LIVE_DISPLAY_DESCRIPTIONS[mode],
                default=mode == active_mode,
            )
            for mode in modes
        ]
        super().__init__(
            placeholder="Display",
            options=options,
            custom_id="embed:live:display",
        )
        self._announcement = announcement
        self._is_flex_lobby = is_flex_lobby

    async def callback(self, interaction: discord.Interaction) -> None:
        """Render the live lobby with the selected display column."""
        await interaction.response.defer()
        mode = self.values[0]
        rank_queue_id = {
            _LIVE_DISPLAY_FLEX_RANK: FLEX_QUEUE_ID,
            _LIVE_DISPLAY_SOLO_RANK: SOLO_QUEUE_ID,
        }.get(mode)
        embed = await build_live_game_embed(
            self._announcement,
            rank_queue_id=rank_queue_id,
            show_rank_names=mode in (_LIVE_DISPLAY_RANKS, _LIVE_DISPLAY_FLEX_RANK, _LIVE_DISPLAY_SOLO_RANK),
            show_mastery=mode == _LIVE_DISPLAY_MASTERY,
        )
        await interaction.edit_original_response(
            embed=embed,
            view=LiveGameAnnouncementView(
                self._announcement,
                rank_queue_id=rank_queue_id,
                display_mode=mode,
            ),
        )


class LiveGameAnnouncementView(discord.ui.View):
    """Controls attached to live-game announcements."""

    def __init__(
        self,
        announcement: LiveGameAnnouncement,
        *,
        rank_queue_id: int | None = None,
        display_mode: str = _LIVE_DISPLAY_PLAYERS,
    ) -> None:
        """Initialize the display dropdown."""
        super().__init__(timeout=None)
        is_flex_lobby = announcement.game.get("gameQueueConfigId") == FLEX_QUEUE_ID
        self.add_item(
            _LiveDisplaySelect(
                announcement,
                active_mode=display_mode,
                is_flex_lobby=is_flex_lobby,
            )
        )


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

    LOGGER.info("Publishing %d match announcement(s)", len(announcements))
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
                message = await channel.send(
                    embed=embed,
                    file=chart_file,
                    view=MatchAnnouncementView(announcement),
                )
                await remember_match_view_state(message, channel.id, announcement)
                LOGGER.debug("Posted match announcement to channel=%s message=%s", channel.id, message.id)
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
    LOGGER.info("Publishing %d live-game announcement(s)", len(announcements))
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
                message = await channel.send(
                    embed=embed,
                    view=LiveGameAnnouncementView(announcement),
                )
                await remember_live_game_view_state(message, channel.id, announcement)
                LOGGER.debug("Posted live-game announcement to channel=%s message=%s", channel.id, message.id)
            except Exception:
                LOGGER.exception(
                    "Could not post a live-game announcement to %s", channel.id
                )
