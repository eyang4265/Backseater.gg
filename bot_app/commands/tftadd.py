"""Owner-only ``/tftadd`` command for the independent TFT registry."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ..account_registry import RegistryError
from ..config import get_settings
from ..render import make_embed
from ..routing import DEFAULT_PLATFORM, split_riot_id
from ..tft_account_registry import track_tft_account
from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


class TftAddCommand(commands.Cog):
    """Register TFT PUUIDs separately from League accounts."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the cog."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Track a TFT account separately from League (defaults to NA1)",
    )
    @discord.option("summoner", description="TFT Riot ID in the form username#Tag")
    @discord.option("user", discord.User, description="Discord username")
    @commands.is_owner()
    async def tftadd(self, ctx, summoner, user):
        """Resolve, seed, and link one TFT-specific PUUID."""
        log_command(ctx, summoner=summoner, user=user)
        name, tag = split_riot_id(summoner, None)
        if not tag:
            await ctx.respond(
                embed=make_embed("Give a TFT Riot ID in the form `Name#Tag`."),
                ephemeral=True,
            )
            return
        await ctx.defer(ephemeral=True)
        try:
            account = await asyncio.to_thread(
                track_tft_account,
                user.id,
                name,
                tag,
                DEFAULT_PLATFORM,
                allow_reassign=True,
                max_accounts=get_settings().max_tracked_accounts,
            )
        except RegistryError as error:
            LOGGER.info("/tftadd failed for %s#%s: %s", name, tag, error)
            await ctx.respond(embed=make_embed(str(error)), ephemeral=True)
            return
        await ctx.respond(
            embed=make_embed(
                f"Tracking TFT account **{account.riot_id}** on "
                f"{account.server} for {user.mention}."
            ),
            ephemeral=True,
        )


def setup(bot: discord.Bot) -> None:
    """Register the TFT account command."""
    bot.add_cog(TftAddCommand(bot))
