"""Public ``/championpool`` report over a bounded recent match window."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from .. import emoji
from ..champion_pool import ROLE_NAMES, ROLES, aggregate_pool
from ..match_cache import get_match_cache
from ..render import make_embed
from ..routing import opgg_url
from ..services.riot_api import RiotAPIError, get_client
from .shared import GUILD_IDS, SERVERS, log_command, not_found_embed, set_player_author, target_for

LOGGER = logging.getLogger(__name__)
MATCH_LIMIT = 100
MAX_ROWS_PER_ROLE = 5
QUEUE_CHOICES = ("All SR", "Ranked Solo/Duo", "Ranked Flex")


def _load_matches(puuid: str, server: str) -> tuple[list[dict], int, int]:
    """Load the latest match window, using cached payloads before Riot details."""
    client = get_client()
    match_ids = client.match_ids(puuid, server, count=MATCH_LIMIT)
    cache = get_match_cache()
    matches: list[dict] = []
    failures = 0
    for match_id in match_ids:
        payload = cache.get(match_id)
        if payload is None:
            try:
                payload = client.match(match_id, server)
            except RiotAPIError as error:
                LOGGER.warning("/championpool could not load %s: %s", match_id, error)
                failures += 1
                continue
        matches.append(payload)
    return matches, len(match_ids), failures


def _pool_embed(
    player_name: str, matches: list[dict], puuid: str, queue: str,
    checked: int, failures: int, profile_url: str | None = None,
) -> discord.Embed:
    """Render aligned role columns and link the player title when possible."""
    rows = aggregate_pool(matches, puuid, queue)
    games = sum(row.games for row in rows)
    embed = make_embed(
        f"{queue} · {games} eligible games from the latest {checked} matches"
        + (f" · {failures} unavailable" if failures else ""),
        title=f"Champion Pool — {player_name}",
    )
    if profile_url:
        embed.url = profile_url
    if not rows:
        embed.description += "\nNo eligible games with a recorded role were found."
        return embed
    for role in ROLES:
        role_rows = sorted(
            (row for row in rows if row.role == role),
            key=lambda row: (-row.games, -row.win_rate, row.champion.casefold()),
        )
        if not role_rows:
            continue
        shown = role_rows[:MAX_ROWS_PER_ROLE]
        suffix = f" (+{len(role_rows) - len(shown)} more)" if len(role_rows) > len(shown) else ""
        embed.add_field(
            name=ROLE_NAMES[role] + suffix,
            value="\n".join(
                emoji.prefixed(emoji.champion_emoji(None, name=row.champion), row.champion)
                for row in shown
            ),
            inline=True,
        )
        embed.add_field(name="Record · WR", value="\n".join(
            f"{row.wins}W–{row.games-row.wins}L · {row.win_rate:.0%}" for row in shown
        ), inline=True)
        # Keep each role on its own row in Discord's three-column embed grid.
        embed.add_field(name="\u200b", value="\u200b", inline=True)
    return embed


class ChampionPoolCommands(commands.Cog):
    """Register the public champion pool report."""

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Show your champion record by role")
    @discord.option("queue", description="Summoner's Rift queue scope", choices=QUEUE_CHOICES, required=False)
    @discord.option("server", description="Player's server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    async def championpool(self, ctx, queue="All SR", server=None, username=None):
        """Show champion usage, record, and win rate by role."""
        log_command(ctx, queue=queue, server=server, username=username)
        await ctx.defer()
        target = await target_for(ctx, server, username)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return
        try:
            matches, checked, failures = await asyncio.to_thread(_load_matches, target.puuid, target.server)
        except RiotAPIError as error:
            await ctx.respond(embed=make_embed(f"Could not load match history: {error}"))
            return
        embed = _pool_embed(
            target.riot_id, matches, target.puuid, queue, checked, failures,
            opgg_url(target.server, target.riot_id),
        )
        set_player_author(embed, target)
        await ctx.respond(embed=embed)


def setup(bot: discord.Bot) -> None:
    """Register the champion pool command cog."""
    bot.add_cog(ChampionPoolCommands(bot))
