"""Public command disabling selected automatic announcement categories."""

import asyncio
import logging

import discord
from discord.ext import commands

from ..channel_settings import disable_channels, TYPE_LABELS
from ..render import make_embed
from .shared import GUILD_IDS, log_command
from .channel_options import CHANNEL_TYPES

LOGGER = logging.getLogger(__name__)


class UnsetChannelCommands(commands.Cog):
    """Persist opt-outs for tracked players, Worlds, or both and clear their routes."""

    @discord.slash_command(guild_ids=GUILD_IDS, description="Disable tracked-player announcements, Worlds, or both")
    @commands.has_permissions(manage_guild=True)
    @discord.option("type", str, choices=CHANNEL_TYPES, default="both", description="Which announcements to disable")
    async def unsetchannel(self, ctx, type="both"):
        """Defer before disabling only the selected routes and their fallbacks."""
        await ctx.defer(ephemeral=True)
        log_command(ctx, type=type)
        if ctx.guild is None:
            await ctx.respond(embed=make_embed("Use `/unsetchannel` in a server."), ephemeral=True)
            return
        await asyncio.to_thread(disable_channels, ctx.guild.id, type)
        LOGGER.info("Disabled announcements guild=%s type=%s", ctx.guild.id, type)
        await ctx.respond(embed=make_embed(f"{TYPE_LABELS[type]} disabled."), ephemeral=True)


def setup(bot):
    """Register the public server announcement opt-out command."""
    bot.add_cog(UnsetChannelCommands())
