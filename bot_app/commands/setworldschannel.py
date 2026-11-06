"""Public Manage Server command for an independent Worlds announcement channel."""

import asyncio
import logging

import discord
from discord.ext import commands

from ..render import make_embed
from ..channel_settings import configure_channel, worlds_permissions_available, WORLDS_PERMISSION_ERROR
from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


class WorldsChannelCommands(commands.Cog):
    """Configure the destination for daily Worlds schedules and series announcements."""

    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Set this server's Worlds announcement channel")
    @commands.has_permissions(manage_guild=True)
    @discord.option("channel", discord.TextChannel, required=False,
                    description="Worlds channel; defaults to the channel where you run this command")
    async def setworldschannel(self, ctx, channel=None):
        """Acknowledge before saving; require the permissions needed by numbered schedules."""
        await ctx.defer(ephemeral=True)
        log_command(ctx, channel=channel)
        if ctx.guild is None:
            await ctx.respond(embed=make_embed("Use `/setworldschannel` in a server."), ephemeral=True)
            return
        selected = channel or ctx.channel
        if not worlds_permissions_available(selected, ctx.guild.me):
            await ctx.respond(embed=make_embed(WORLDS_PERMISSION_ERROR), ephemeral=True)
            return
        await asyncio.to_thread(configure_channel, ctx.guild.id, selected.id, "worlds")
        LOGGER.info("Worlds channel guild=%s channel=%s", ctx.guild.id, selected.id)
        await ctx.respond(embed=make_embed(f"Worlds announcements will be posted in {selected.mention}."), ephemeral=True)


def setup(bot):
    """Register the public Worlds routing command."""
    bot.add_cog(WorldsChannelCommands(bot))
