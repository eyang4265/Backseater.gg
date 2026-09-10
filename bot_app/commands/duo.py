"""The public ``/duo`` command."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from .shared import (
    GUILD_IDS,
    MATCH_POSITION_DESCRIPTION,
    SERVERS,
    game_mode_choices,
    log_command,
    not_found_embed,
    supplied_options_text,
    target_at_latest_match_position,
    target_for,
)
from ..match_cache import get_match_cache
from ..render import make_embed
from ..services.riot_api import RiotAPIError
from ..store import load_accounts

LOGGER = logging.getLogger(__name__)


class DuoCommands(commands.Cog):
    """Provide the cached duo record command."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the command cog."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Cached win rate for two tracked players"
    )
    @discord.option("teammate", discord.User, description="Second tracked user")
    @discord.option(
        "server", description="Primary player's server", choices=SERVERS, required=False
    )
    @discord.option(
        "username",
        description="Primary player's League or Discord username (defaults to you)",
        required=False,
    )
    @discord.option(
        "game_mode",
        description="Filter by game mode, such as Ranked Solo/Duo or ARAM",
        autocomplete=discord.utils.basic_autocomplete(game_mode_choices),
        required=False,
    )
    @discord.option("position", int, description=MATCH_POSITION_DESCRIPTION, min_value=1, max_value=10, required=False)
    async def duo(self, ctx, teammate, server=None, username=None, game_mode=None, position=None):
        """Show a teammate's record with a direct primary target or latest-match slot."""
        log_command(
            ctx,
            server=server,
            username=username,
            teammate=teammate,
            game_mode=game_mode,
            position=position,
        )
        await ctx.defer()
        target = await target_for(ctx, server, username, include_icon=False)
        accounts = await asyncio.to_thread(load_accounts)
        teammate_account = accounts.get(str(teammate.id))
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return
        if position is not None:
            try:
                target = await target_at_latest_match_position(target, position)
            except RiotAPIError as error:
                await ctx.respond(embed=make_embed(f"Could not fetch the latest match: {error}"))
                return
            if target is None:
                await ctx.respond(embed=make_embed(f"The latest match has no player in position {position}."))
                return
        if teammate_account is None:
            LOGGER.info("/duo teammate %s has no tracked Riot account", teammate.id)
            await ctx.respond(
                embed=make_embed(
                    f"{getattr(teammate, 'display_name', 'That user')} "
                    "has no tracked Riot account."
                    + supplied_options_text(ctx)
                )
            )
            return
        games, wins = await asyncio.to_thread(
            get_match_cache().duo_record,
            target.puuid,
            teammate_account.puuid,
            game_mode=game_mode,
        )
        LOGGER.debug(
            "/duo cache lookup for %s + %s (mode=%s): %d games, %d wins",
            target.riot_id, teammate_account.riot_id, game_mode, games, wins,
        )
        if not games:
            await ctx.respond(
                embed=make_embed("No shared completed matches are in the cache yet.")
            )
            return
        embed = make_embed(
            f"**Record:** {wins}W–{games - wins}L\n**Win rate:** {wins / games:.1%}\n**Games:** {games}",
            title=f"Duo — {target.riot_id} + {teammate_account.riot_id}",
        )
        await ctx.respond(embed=embed)


def setup(bot: discord.Bot) -> None:
    """Register the duo command cog."""
    bot.add_cog(DuoCommands(bot))
