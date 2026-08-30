"""Owner-only /add command: quick account registration defaulting to NA1."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ..account_registry import RegistryError, track_account
from ..config import get_settings
from ..render import make_embed
from ..routing import DEFAULT_PLATFORM, split_riot_id
from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


class AddCommand(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Track a Riot account (defaults to NA1)"
    )
    @discord.option("summoner", description="League username#Tag")
    @discord.option("user", discord.User, description="Discord username")
    @commands.is_owner()
    async def add(self, ctx, summoner, user):
        """Handle add."""
        log_command(ctx, summoner=summoner, user=user)
        name, tag = split_riot_id(summoner, None)
        if not tag:
            LOGGER.debug("/add rejected summoner %r: missing tagline", summoner)
            await ctx.respond(
                embed=make_embed("Give a Riot ID in the form `Name#Tag`."),
                ephemeral=True,
            )
            return
        await ctx.defer(ephemeral=True)
        try:
            account = await asyncio.to_thread(
                track_account,
                user.id,
                name,
                tag,
                DEFAULT_PLATFORM,
                allow_reassign=True,
                max_accounts=get_settings().max_tracked_accounts,
            )
        except RegistryError as error:
            LOGGER.info("/add failed for %s#%s: %s", name, tag, error)
            await ctx.respond(embed=make_embed(str(error)), ephemeral=True)
            return
        LOGGER.info(
            "Tracking %s on %s for Discord user %s", account.riot_id, account.server, user.id
        )
        await ctx.respond(
            embed=make_embed(
                f"Tracking **{account.riot_id}** on {account.server} for {user.mention}."
            ),
            ephemeral=True,
        )


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(AddCommand(bot))
