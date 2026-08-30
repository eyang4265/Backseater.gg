"""Player lookup commands: /profile, /livegame, /matchhistory, /matchlist, /puuid."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ... import ddragon
from ...announce import (
    LiveGameAnnouncementView,
    TrackedPlayer,
    build_live_game_embed,
    format_live_game,
    remember_live_game_view_state,
)
from ...emoji import champion_emoji, prefixed
from ...history import format_match_history_line, match_history_score
from ...queues import FLEX_QUEUE_ID, SOLO_QUEUE_ID, queue_name
from ...ranks import RankSnapshot, fetch_ranks
from ...render import (
    make_embed,
    rank_text,
    relative_time,
)
from ...services.riot_api import RiotAPIError, get_client
from ...store import load_accounts
from ..shared import (
    GUILD_IDS,
    SERVERS,
    game_mode_choices,
    log_command,
    not_found_embed,
    riot_to_thread,
    set_player_author,
    target_for,
)

LOGGER = logging.getLogger(__name__)

_TOP_MASTERY_COUNT = 3
_MATCH_HISTORY_COUNT = 10
_MATCH_HISTORY_FILTER_LOOKBACK = 100
_MATCH_HISTORY_FETCH_BATCH = 5


def _record_text(snapshot: RankSnapshot | None) -> str | None:
    """Handle text."""
    if snapshot is None or snapshot.winrate is None:
        return None
    return f"{snapshot.wins}W {snapshot.losses}L ({snapshot.winrate * 100:.2f}%)"


def _match_history_line(match: dict, puuid: str, catalog) -> str | None:
    """Format one completed game for the compact match-history embed."""
    info = match.get("info", {})
    participant = next(
        (
            entry
            for entry in info.get("participants", [])
            if entry.get("puuid") == puuid
        ),
        None,
    )
    if participant is None:
        return None

    champion = catalog.by_key(participant.get("championId")) if catalog else None
    champion_name = (
        champion.name
        if champion
        else participant.get("championName", "Unknown champion")
    )
    champion_label = prefixed(
        champion_emoji(champion, name=champion_name), champion_name
    )
    timestamp = (
        info.get("gameEndTimestamp")
        or info.get("gameStartTimestamp")
        or info.get("gameCreation")
    )
    return format_match_history_line(
        info,
        participant,
        champion_label=champion_label,
        time_label=relative_time(timestamp),
    )


def _matches_history_filters(
    match: dict, puuid: str, catalog, game_mode: str | None, champion: str | None
) -> bool:
    """Return whether a match contains the requested mode and champion."""
    info = match.get("info", {})
    if game_mode and game_mode.casefold() not in queue_name(info.get("queueId")).casefold():
        return False
    if not champion:
        return True
    participant = next(
        (entry for entry in info.get("participants", []) if entry.get("puuid") == puuid),
        None,
    )
    if participant is None:
        return False
    champion_data = catalog.by_key(participant.get("championId")) if catalog else None
    actual = champion_data.name if champion_data else participant.get("championName", "")
    return actual.casefold() == champion.casefold()


class PlayerCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Link to a player's OP.GG profile"
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    async def opgg(self, ctx, server, username):
        """Return an OP.GG profile link for a tracked or looked-up player."""
        log_command(ctx, server=server, username=username)
        target = await target_for(ctx, server, username)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return

        url = target.opgg_url
        if url is None:
            LOGGER.debug("No OP.GG profile available for %s on %s", target.riot_id, target.server)
            await ctx.respond(
                embed=make_embed(
                    f"No OP.GG profile is available for {target.riot_id} on {target.server}."
                )
            )
            return
        LOGGER.info("Sent OP.GG link for %s", target.riot_id)
        embed = discord.Embed(
            title=f"OP.GG — {target.riot_id}",
            url=url,
            description=f"[Open {target.riot_id}'s OP.GG profile]({url})",
            color=discord.Color.blurple(),
        )
        set_player_author(embed, target)
        await ctx.respond(embed=embed)

    @discord.slash_command(guild_ids=GUILD_IDS, description="Player Profile")
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    async def profile(self, ctx, server, username):
        """Level, ranked standing in both queues, and top champion masteries."""
        log_command(ctx, server=server, username=username)
        await ctx.defer()

        target = await target_for(ctx, server, username)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return

        client = get_client()
        level, ranks = await asyncio.gather(
            asyncio.to_thread(client.summoner_level, target.puuid, target.server),
            asyncio.to_thread(fetch_ranks, target.puuid, target.server),
        )
        lines = [
            f"**Level:** {level}",
        ]

        ranks = ranks or {}
        for queue_id, label in (
            (SOLO_QUEUE_ID, "Ranked Solo"),
            (FLEX_QUEUE_ID, "Ranked Flex"),
        ):
            snapshot = ranks.get(queue_id)
            lines.append(
                f"**{label}:** {rank_text(snapshot, with_winrate=True) or 'Unranked'}"
            )
            record = _record_text(snapshot)
            if record:
                lines.append(f"**Record:** {record}")

        mastery_lines = await asyncio.to_thread(self._top_mastery_lines, target)
        if mastery_lines:
            lines.append("\n**Top Champions:**\n" + "\n".join(mastery_lines))

        embed = make_embed("\n".join(lines))
        set_player_author(embed, target)
        LOGGER.info("Sent profile for %s (level %s)", target.riot_id, level)
        await ctx.respond(embed=embed)

    @staticmethod
    def _top_mastery_lines(target) -> list[str]:
        """Handle mastery lines."""
        try:
            masteries = get_client().top_champion_masteries(
                target.puuid, target.server, count=_TOP_MASTERY_COUNT
            )
        except RiotAPIError as error:
            LOGGER.warning(
                "Could not fetch top masteries for %s: %s", target.puuid, error
            )
            return []

        catalog = ddragon.catalog()
        lines = []
        for entry in masteries:
            champion = catalog.by_key(entry.get("championId")) if catalog else None
            name = champion.name if champion else f"Champion {entry.get('championId')}"
            lines.append(
                f"{prefixed(champion_emoji(champion), name)} — {entry.get('championPoints', 0):,} pts"
            )
        return lines

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Show a live game's lobby (champions + ranks). Leave everything blank for your own account.",
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    async def livegame(self, ctx, server, username):
        """The target's current lobby: names, ranks, and win rates per team.

        Renders with the same embed and buttons as automatic live-game
        announcements, and persists its button state the same way so the
        buttons survive a bot restart.
        """
        log_command(ctx, server=server, username=username)
        await ctx.defer()

        target = await target_for(ctx, server, username)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return

        try:
            game = await asyncio.to_thread(
                get_client().active_game, target.puuid, target.server
            )
        except RiotAPIError as error:
            LOGGER.info("Live game lookup failed for %s: %s", target.riot_id, error)
            await ctx.respond(
                embed=make_embed(f"Could not check live game status: {error}")
            )
            return

        in_game = game is not None and any(
            p.get("puuid") == target.puuid for p in game.get("participants", []) or []
        )
        if not in_game:
            LOGGER.debug("%s is not currently in a game", target.riot_id)
            await ctx.respond(
                embed=make_embed(f"**{target.riot_id}** is not currently in a game.")
            )
            return

        try:
            accounts = await asyncio.to_thread(load_accounts)
            registered = {account.puuid: account for account in accounts.values()}
            lobby_players = []
            for participant in game.get("participants", []) or []:
                account = registered.get(participant.get("puuid"))
                if account is None:
                    continue
                lobby_players.append(
                    TrackedPlayer(
                        puuid=account.puuid,
                        riot_id=account.riot_id,
                        server=account.server,
                    )
                )
            if target.puuid not in registered:
                lobby_players.append(
                    TrackedPlayer(
                        puuid=target.puuid,
                        riot_id=target.riot_id,
                        server=target.server,
                    )
                )
            announcement = await asyncio.to_thread(
                format_live_game,
                game,
                lobby_players,
            )
            if announcement is None:
                raise RiotAPIError("Could not find the target in the live game")
            embed = await build_live_game_embed(announcement)
        except RiotAPIError as error:
            LOGGER.info("Could not build live game embed for %s: %s", target.riot_id, error)
            await ctx.respond(
                embed=make_embed(f"Could not fetch lobby details: {error}")
            )
            return

        set_player_author(embed, target, name=f"{target.riot_id}'s Lobby")
        message = await ctx.respond(embed=embed, view=LiveGameAnnouncementView(announcement))
        if message is not None:
            await remember_live_game_view_state(message, message.channel.id, announcement)
        LOGGER.info("Sent /livegame lobby for %s (%d tracked players)", target.riot_id, len(lobby_players))

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Show a player's 10 most recent games",
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    @discord.option(
        "game_mode",
        description="Filter by game mode, such as Ranked Solo/Duo or ARAM",
        autocomplete=discord.utils.basic_autocomplete(game_mode_choices),
        required=False,
    )
    @discord.option("champion", description="Filter by champion", required=False)
    async def matchhistory(self, ctx, server, username, game_mode, champion):
        """Show up to ten recent games, optionally filtered by mode or champion."""
        log_command(
            ctx,
            server=server,
            username=username,
            game_mode=game_mode,
            champion=champion,
        )
        await ctx.defer()

        target = await target_for(ctx, server, username)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return

        client = get_client()
        try:
            match_ids = await riot_to_thread(
                client.match_ids,
                target.puuid,
                target.server,
                count=(
                    _MATCH_HISTORY_FILTER_LOOKBACK
                    if game_mode or champion
                    else _MATCH_HISTORY_COUNT
                ),
            )
        except RiotAPIError as error:
            LOGGER.info("Match history fetch failed for %s: %s", target.riot_id, error)
            await ctx.respond(
                embed=make_embed(f"Could not fetch match history: {error}")
            )
            return
        if not match_ids:
            await ctx.respond(
                embed=make_embed(f"No matches found for {target.riot_id}.")
            )
            return
        LOGGER.debug(
            "Scanning up to %d match ids for %s (game_mode=%s, champion=%s)",
            len(match_ids), target.riot_id, game_mode, champion,
        )

        catalog = await asyncio.to_thread(ddragon.catalog)
        displayed_matches = []
        lines = []
        fetch_errors = []
        filtered_out = 0
        # Fetch in small batches and stop as soon as enough matches pass the
        # filter, instead of always fetching all `count` matches up front —
        # a full 100-match fan-out would monopolize the shared Riot API rate
        # limiter that the background match/live-game poller also relies on.
        for batch_start in range(0, len(match_ids), _MATCH_HISTORY_FETCH_BATCH):
            if len(lines) == _MATCH_HISTORY_COUNT:
                break
            batch_ids = match_ids[batch_start : batch_start + _MATCH_HISTORY_FETCH_BATCH]
            fetched = await asyncio.gather(
                *(
                    riot_to_thread(client.match, match_id, target.server)
                    for match_id in batch_ids
                ),
                return_exceptions=True,
            )
            for match_id, result in zip(batch_ids, fetched):
                if isinstance(result, Exception):
                    LOGGER.warning(
                        "Could not fetch match %s for history: %s", match_id, result
                    )
                    fetch_errors.append(f"{match_id} ({result})")
                    continue
                if not _matches_history_filters(
                    result, target.puuid, catalog, game_mode, champion
                ):
                    filtered_out += 1
                    continue
                line = _match_history_line(result, target.puuid, catalog)
                if line is None:
                    filtered_out += 1
                    continue
                displayed_matches.append(result)
                lines.append(line)
                if len(lines) == _MATCH_HISTORY_COUNT:
                    break
        if not lines:
            details = [
                f"Player: {target.riot_id} ({target.server})",
                f"Matches checked: {len(match_ids)}",
            ]
            if game_mode:
                details.append(f"Game mode filter: {game_mode}")
            if champion:
                details.append(f"Champion filter: {champion}")
            if fetch_errors:
                details.append(
                    f"Fetch failures ({len(fetch_errors)}): "
                    + "; ".join(fetch_errors[:5])
                    + (" ..." if len(fetch_errors) > 5 else "")
                )
            if filtered_out:
                details.append(f"Filtered out: {filtered_out}")
            await ctx.respond(
                embed=make_embed(
                    "Could not load any match details. Please try again.\n"
                    + "\n".join(details)
                )
            )
            return

        wins, losses = match_history_score(displayed_matches, target.puuid)
        embed = make_embed(
            "\n\n".join(lines),
            title=f"Match History — {target.riot_id} ({wins}W {losses}L)",
        )
        set_player_author(embed, target)
        LOGGER.info(
            "Sent match history for %s (%d shown, %d filtered out)",
            target.riot_id, len(lines), filtered_out,
        )
        await ctx.respond(embed=embed)

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Recent match IDs for a tracked player"
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    async def matchlist(self, ctx, server, username):
        """The player's most recent match ids."""
        log_command(ctx, server=server, username=username)
        await ctx.defer()

        target = await target_for(ctx, server, username)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return

        try:
            match_ids = await asyncio.to_thread(
                get_client().match_ids, target.puuid, target.server
            )
        except RiotAPIError as error:
            LOGGER.info("Match list fetch failed for %s: %s", target.riot_id, error)
            await ctx.respond(embed=make_embed(f"Could not fetch match list: {error}"))
            return

        if not match_ids:
            await ctx.respond(embed=make_embed("No matches found."))
            return

        embed = make_embed("\n".join(match_ids), title=f"Recent Matches — {target.riot_id}")
        set_player_author(embed, target)
        LOGGER.info("Sent %d match ids for %s", len(match_ids), target.riot_id)
        await ctx.respond(embed=embed)

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Look up a player's PUUID (owner only)"
    )
    @commands.is_owner()
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    async def puuid(self, ctx, server, username):
        """Resolve a player's PUUID.

        Owner-only and ephemeral: a PUUID can be used to look someone up
        through the Riot API directly, without going through this bot.
        """
        log_command(ctx, server=server, username=username)

        target = await target_for(ctx, server, username)
        if target is None:
            await ctx.respond(
                embed=not_found_embed(username, server, ctx=ctx), ephemeral=True
            )
            return

        embed = make_embed(f"`{target.puuid}`", title=f"PUUID — {target.riot_id}")
        set_player_author(embed, target)
        LOGGER.info("Sent PUUID for %s", target.riot_id)
        await ctx.respond(embed=embed, ephemeral=True)


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(PlayerCommands(bot))
