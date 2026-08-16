"""The public ``/duo`` command."""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from .shared import GUILD_IDS, SERVERS, log_command, target_for
from ..match_cache import get_match_cache
from ..render import make_embed
from ..store import load_accounts


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
    @discord.option("summoner", description="Primary player's Game Name", required=False)
    @discord.option("user", description="Primary user (defaults to you)", required=False)
    async def duo(self, ctx, teammate, server=None, summoner=None, user=None):
        """Show the cached record for two tracked players."""
        log_command(ctx, server=server, summoner=summoner, user=user, teammate=teammate)
        await ctx.defer()
        target = await target_for(ctx, server, summoner, user, include_icon=False)
        accounts = await asyncio.to_thread(load_accounts)
        teammate_account = accounts.get(str(teammate.id))
        if target is None or teammate_account is None:
            await ctx.respond(embed=make_embed("Both players must resolve to tracked accounts."))
            return
        games, wins = await asyncio.to_thread(
            get_match_cache().duo_record, target.puuid, teammate_account.puuid
        )
        if not games:
            await ctx.respond(
                embed=make_embed("No shared completed matches are in the cache yet.")
            )
            return
        await ctx.respond(
            embed=make_embed(
                f"**Record:** {wins}W–{games - wins}L\n**Win rate:** {wins / games:.1%}\n**Games:** {games}",
                title=f"Duo — {target.riot_id} + {teammate_account.riot_id}",
            )
        )


def setup(bot: discord.Bot) -> None:
    """Register the duo command cog."""
    bot.add_cog(DuoCommands(bot))
