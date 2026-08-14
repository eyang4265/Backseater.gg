"""Player lookup commands: /profile, /livegame, /matchhistory, /matchlist, /puuid."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ... import ddragon
from ...emoji import champion_emoji, prefixed
from ...history import format_match_history_line
from ...queues import FLEX_QUEUE_ID, SOLO_QUEUE_ID, lobby_queue_name
from ...ranks import RankSnapshot, fetch_ranks
from ...render import (
    add_team_columns,
    build_lobby_columns,
    format_duration,
    make_embed,
    rank_text,
    relative_time,
)
from ...services.riot_api import RiotAPIError, get_client
from ..shared import GUILD_IDS, SERVERS, log_command, not_found_embed, resolve_target, set_player_author

LOGGER = logging.getLogger(__name__)

_TOP_MASTERY_COUNT = 3
_MATCH_HISTORY_COUNT = 10


def _record_text(snapshot: RankSnapshot | None) -> str | None:
    if snapshot is None or snapshot.winrate is None:
        return None
    return f"{snapshot.wins}W {snapshot.losses}L ({snapshot.winrate * 100:.2f}%)"


def _match_history_line(match: dict, puuid: str, catalog) -> str | None:
    """Format one completed game for the compact match-history embed."""
    info = match.get("info", {})
    participant = next(
        (entry for entry in info.get("participants", []) if entry.get("puuid") == puuid), None
    )
    if participant is None:
        return None

    champion = catalog.by_key(participant.get("championId")) if catalog else None
    champion_name = champion.name if champion else participant.get("championName", "Unknown champion")
    champion_label = prefixed(champion_emoji(champion, name=champion_name), champion_name)
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


class PlayerCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Link to a player's OP.GG profile")
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("summoner", description="Game Name", required=False)
    @discord.option("tag", description="Tagline", required=False)
    @discord.option("user", description="User (defaults to you)", required=False)
    async def opgg(self, ctx, server, summoner, tag, user):
        """Return an OP.GG profile link for a tracked or looked-up player."""
        log_command(ctx, server=server, summoner=summoner, tag=tag, user=user)
        target = resolve_target(ctx, server, summoner, tag, user)
        if target is None:
            await ctx.respond(embed=not_found_embed(summoner, tag, server))
            return

        url = target.opgg_url
        if url is None:
            await ctx.respond(embed=make_embed(f"No OP.GG profile is available for {target.riot_id} on {target.server}."))
            return
        embed = discord.Embed(title=f"OP.GG — {target.riot_id}", url=url, description=f"[Open {target.riot_id}'s OP.GG profile]({url})", color=discord.Color.blurple())
        set_player_author(embed, target)
        await ctx.respond(embed=embed)

    @discord.slash_command(guild_ids=GUILD_IDS, description="Player Profile")
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("summoner", description="Game Name", required=False)
    @discord.option("tag", description="Tagline", required=False)
    @discord.option("user", description="User", required=False)
    async def profile(self, ctx, server, summoner, tag, user):
        """Level, server, ranked standing in both queues, and top champion masteries."""
        log_command(ctx, server=server, summoner=summoner, tag=tag, user=user)
        await ctx.defer()

        target = resolve_target(ctx, server, summoner, tag, user)
        if target is None:
            await ctx.respond(embed=not_found_embed(summoner, tag, server))
            return

        client = get_client()
        lines = [
            f"**Level:** {client.summoner_level(target.puuid, target.server)}",
            f"**Server:** {target.server}",
        ]

        ranks = fetch_ranks(target.puuid, target.server) or {}
        for queue_id, label in ((SOLO_QUEUE_ID, "Ranked Solo"), (FLEX_QUEUE_ID, "Ranked Flex")):
            snapshot = ranks.get(queue_id)
            lines.append(f"**{label}:** {rank_text(snapshot) or 'Unranked'}")
            record = _record_text(snapshot)
            if record:
                lines.append(f"**Record:** {record}")

        mastery_lines = self._top_mastery_lines(target)
        if mastery_lines:
            lines.append("\n**Top Champions:**\n" + "\n".join(mastery_lines))

        embed = make_embed("\n".join(lines))
        set_player_author(embed, target)
        await ctx.respond(embed=embed)

    @staticmethod
    def _top_mastery_lines(target) -> list[str]:
        try:
            masteries = get_client().top_champion_masteries(
                target.puuid, target.server, count=_TOP_MASTERY_COUNT
            )
        except RiotAPIError as error:
            LOGGER.warning("Could not fetch top masteries for %s: %s", target.puuid, error)
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
    @discord.option("summoner", description="Game Name", required=False)
    @discord.option("tag", description="Tagline", required=False)
    @discord.option("user", description="User", required=False)
    async def livegame(self, ctx, server, summoner, tag, user):
        """The target's current lobby: names, ranks, and win rates per team."""
        log_command(ctx, server=server, summoner=summoner, tag=tag, user=user)
        await ctx.defer()

        target = resolve_target(ctx, server, summoner, tag, user)
        if target is None:
            await ctx.respond(embed=not_found_embed(summoner, tag, server))
            return

        try:
            game = await asyncio.to_thread(get_client().active_game, target.puuid, target.server)
        except RiotAPIError as error:
            await ctx.respond(embed=make_embed(f"Could not check live game status: {error}"))
            return

        in_game = game is not None and any(
            p.get("puuid") == target.puuid for p in game.get("participants", []) or []
        )
        if not in_game:
            await ctx.respond(embed=make_embed(f"**{target.riot_id}** is not currently in a game."))
            return

        try:
            columns = await asyncio.to_thread(build_lobby_columns, game, target.server)
        except RiotAPIError as error:
            await ctx.respond(embed=make_embed(f"Could not fetch lobby details: {error}"))
            return

        embed = discord.Embed(
            title=f"Live Game — {lobby_queue_name(game.get('gameQueueConfigId'))}",
            description=f"**Game Time:** {format_duration(game.get('gameLength', 0))}",
            color=discord.Color.gold(),
        )
        add_team_columns(embed, columns)

        set_player_author(embed, target, name=f"{target.riot_id}'s Lobby")
        await ctx.respond(embed=embed)

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Show a player's 10 most recent games",
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("summoner", description="Game Name", required=False)
    @discord.option("tag", description="Tagline", required=False)
    @discord.option("user", description="User (defaults to you)", required=False)
    async def matchhistory(self, ctx, server, summoner, tag, user):
        """Recent games with the result, champion, KDA, CS, and game details."""
        log_command(ctx, server=server, summoner=summoner, tag=tag, user=user)
        await ctx.defer()

        target = resolve_target(ctx, server, summoner, tag, user)
        if target is None:
            await ctx.respond(embed=not_found_embed(summoner, tag, server))
            return

        client = get_client()
        try:
            match_ids = await asyncio.to_thread(
                client.match_ids, target.puuid, target.server, count=_MATCH_HISTORY_COUNT
            )
        except RiotAPIError as error:
            await ctx.respond(embed=make_embed(f"Could not fetch match history: {error}"))
            return
        if not match_ids:
            await ctx.respond(embed=make_embed(f"No matches found for {target.riot_id}."))
            return

        fetched = await asyncio.gather(
            *(asyncio.to_thread(client.match, match_id, target.server) for match_id in match_ids),
            return_exceptions=True,
        )
        matches = []
        for match_id, result in zip(match_ids, fetched):
            if isinstance(result, Exception):
                LOGGER.warning("Could not fetch match %s for history: %s", match_id, result)
            else:
                matches.append(result)

        catalog = await asyncio.to_thread(ddragon.catalog)
        lines = [
            line
            for match in matches
            if (line := _match_history_line(match, target.puuid, catalog)) is not None
        ]
        if not lines:
            await ctx.respond(embed=make_embed("Could not load any match details. Please try again."))
            return

        embed = make_embed("\n\n".join(lines), title=f"Match History — {target.riot_id}")
        set_player_author(embed, target)
        await ctx.respond(embed=embed)

    @discord.slash_command(guild_ids=GUILD_IDS, description="Recent match IDs for a tracked player")
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("summoner", description="Game Name", required=False)
    @discord.option("tag", description="Tagline", required=False)
    @discord.option("user", description="User (defaults to you)", required=False)
    async def matchlist(self, ctx, server, summoner, tag, user):
        """The player's most recent match ids."""
        log_command(ctx, server=server, summoner=summoner, tag=tag, user=user)
        await ctx.defer()

        target = resolve_target(ctx, server, summoner, tag, user)
        if target is None:
            await ctx.respond(embed=not_found_embed(summoner, tag, server))
            return

        try:
            match_ids = await asyncio.to_thread(get_client().match_ids, target.puuid, target.server)
        except RiotAPIError as error:
            await ctx.respond(embed=make_embed(f"Could not fetch match list: {error}"))
            return

        if not match_ids:
            await ctx.respond(embed=make_embed("No matches found."))
            return

        await ctx.respond(
            embed=make_embed("\n".join(match_ids), title=f"Recent Matches — {target.riot_id}")
        )

    @discord.slash_command(guild_ids=GUILD_IDS, description="Look up a player's PUUID (owner only)")
    @commands.is_owner()
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("summoner", description="Game Name", required=False)
    @discord.option("tag", description="Tagline", required=False)
    @discord.option("user", description="User (defaults to you)", required=False)
    async def puuid(self, ctx, server, summoner, tag, user):
        """Resolve a player's PUUID.

        Owner-only and ephemeral: a PUUID can be used to look someone up
        through the Riot API directly, without going through this bot.
        """
        log_command(ctx, server=server, summoner=summoner, tag=tag, user=user)

        target = resolve_target(ctx, server, summoner, tag, user)
        if target is None:
            await ctx.respond(embed=not_found_embed(summoner, tag, server), ephemeral=True)
            return

        await ctx.respond(
            embed=make_embed(f"`{target.puuid}`", title=f"PUUID — {target.riot_id}"),
            ephemeral=True,
        )


def setup(bot: discord.Bot) -> None:
    bot.add_cog(PlayerCommands(bot))
